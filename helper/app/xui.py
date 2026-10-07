"""3x-ui panel API client (3.8.x, Bearer token). Stdlib only, like the rest of the hub.

Every panel response is {"success": bool, "msg": str, "obj": ...}; success:false -> XUIError.

Notes taken from the panel's OpenAPI spec (and its UI strings), because they decide how
clients behave across inbounds:

* "Not found": the spec does not document it for GET /clients/get/{email}. The panel
  normally answers HTTP 200 with success:false and a "record not found" / "client not
  found" msg; some paths answer HTTP 404. client_get() maps all of these (and a null obj)
  to None; any other success:false still raises.
* Per-inbound XTLS flow: the spec exposes it only as the `flow` field on the client body
  and as `ClientInbound.flowOverride` (DB: client_inbounds.flow_override). The panel marks
  inbounds with a server-computed `tlsFlowCapable` flag ("true for VLESS on TCP with tls or
  reality, or on XHTTP with VLESS encryption") and injects xtls-rprx-vision automatically
  only where the transport supports it (an inbound can opt out with `disableFlow`). So the
  server decides: callers must NOT send a flow in client_add -- one client attached to a
  Vision-Reality inbound and a plain XHTTP-Reality inbound then works on both. client_update
  replaces the whole row, so pass back what client_get returned.
* Endpoint quirks: /clients/onlines is POST; /clients/del/{email} takes a required
  keepTraffic query param (we always send 0 = drop traffic); share links are
  GET /clients/links/{email}.
"""
import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("xui")
DEFAULT_URL = "https://host.docker.internal:10358/"   # compose sets XUI_URL with the real port + base path
TIMEOUT = 15


class XUIError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class XUI:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/")
        self.token = token
        # The panel cert is valid for the public domain but we connect through
        # host.docker.internal: verify the chain, not the hostname.
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_REQUIRED

    @classmethod
    def from_env(cls):
        token = (os.environ.get("XUI_API_TOKEN") or "").strip()
        if not token:
            return None
        return cls((os.environ.get("XUI_URL") or "").strip() or DEFAULT_URL, token)

    # -- transport ---------------------------------------------------------
    def _scrub(self, text):
        return str(text).replace(self.token, "***") if self.token else str(text)

    def _call(self, method, path, body=None, query=None):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            data = b""
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        ctx = self._ctx if url.startswith("https:") else None
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
                msg = payload.get("msg") if isinstance(payload, dict) else None
            except Exception:
                msg = None
            raise XUIError(self._scrub(msg or f"HTTP {e.code}"), status=e.code) from None
        except urllib.error.URLError as e:
            raise XUIError(self._scrub(f"network: {e.reason}")) from None
        except (OSError, ValueError) as e:  # timeouts, resets, bad status line
            raise XUIError(self._scrub(f"network: {e}")) from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise XUIError("bad response (not JSON)") from None
        if not isinstance(payload, dict) or not payload.get("success"):
            msg = payload.get("msg") if isinstance(payload, dict) else None
            raise XUIError(self._scrub(msg or "unknown error"))
        return payload.get("obj")

    @staticmethod
    def _q(email):
        return urllib.parse.quote(email, safe="")

    # -- inbounds / clients ------------------------------------------------
    def inbounds(self):
        obj = self._call("GET", "/panel/api/inbounds/list/slim") or []
        return [{"id": i.get("id"), "remark": i.get("remark"), "protocol": i.get("protocol"),
                 "port": i.get("port"), "enable": i.get("enable")} for i in obj]

    def clients(self):
        return self._call("GET", "/panel/api/clients/list") or []

    def client_get(self, email):
        try:
            obj = self._call("GET", f"/panel/api/clients/get/{self._q(email)}")
        except XUIError as e:
            if e.status == 404:
                return None
            low = str(e).lower()
            if e.status is None and "not found" in low and not low.startswith("network:"):
                return None
            raise
        if not obj:
            return None
        # /get wraps the row: {client:{...}, inboundIds, usedTraffic, ...}. Flatten it to the
        # same shape /list returns, so callers can treat both alike.
        if isinstance(obj.get("client"), dict):
            rec = dict(obj["client"])
            rec["inboundIds"] = obj.get("inboundIds") or []
            rec.setdefault("traffic", {"used": obj.get("usedTraffic")})
            return rec
        return obj

    # Fields /list and /get return that are not part of the client payload.
    READ_ONLY = ("inboundIds", "traffic", "usedTraffic", "externalLinks", "createdAt", "updatedAt")

    @classmethod
    def _payload(cls, client):
        """A read-back record -> an update payload. In /list and /get, `id` is the DB row id
        and `uuid` is the VLESS id; the update payload wants the VLESS id in `id`
        (sending the row id fails: "cannot unmarshal number into ... .id of type string")."""
        out = {k: v for k, v in client.items() if k not in cls.READ_ONLY and v is not None}
        # Read back as a comma-separated string, written as a list.
        for k in ("allowedIPs",):
            if isinstance(out.get(k), str):
                out[k] = [x.strip() for x in out[k].split(",") if x.strip()]
        if "uuid" in out:
            uuid = out.pop("uuid")
            if uuid:
                out["id"] = uuid
            elif not isinstance(out.get("id"), str):
                out.pop("id", None)
        elif not isinstance(out.get("id"), str):
            out.pop("id", None)
        return out

    def client_add(self, client, inbound_ids):
        # Send flow "xtls-rprx-vision" for a client that should use Vision: verified on 3.8.5
        # that the panel applies it only on flow-capable inbounds (VLESS TCP + Reality) and
        # leaves it off XHTTP links of the same client.
        self._call("POST", "/panel/api/clients/add",
                   {"client": client, "inboundIds": list(inbound_ids)})

    def client_update(self, email, client):
        # The panel replaces the row, it does not patch: send the full record.
        self._call("POST", f"/panel/api/clients/update/{self._q(email)}", self._payload(client))

    def client_attach(self, email, inbound_ids):
        self._call("POST", f"/panel/api/clients/{self._q(email)}/attach",
                   {"inboundIds": list(inbound_ids)})

    def client_detach(self, email, inbound_ids):
        self._call("POST", f"/panel/api/clients/{self._q(email)}/detach",
                   {"inboundIds": list(inbound_ids)})

    def client_delete(self, email):
        self._call("POST", f"/panel/api/clients/del/{self._q(email)}", query={"keepTraffic": 0})

    def inbound_set_remark(self, inbound_id, remark):
        """Rename an inbound. /update replaces the whole inbound, so send back the full
        record /get returns (including settings.clients) with only the remark changed;
        verified on 3.8.5 that clients, keys and AWG addresses survive."""
        inb = self._call("GET", f"/panel/api/inbounds/get/{int(inbound_id)}")
        body = {k: v for k, v in inb.items() if k not in ("id", "clientStats")}
        body["remark"] = remark
        self._call("POST", f"/panel/api/inbounds/update/{int(inbound_id)}", body)

    def client_links(self, email):
        return self._call("GET", f"/panel/api/clients/links/{self._q(email)}") or []

    def onlines(self):
        return self._call("POST", "/panel/api/clients/onlines") or []
