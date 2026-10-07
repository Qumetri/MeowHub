"""Synapse admin API client (Matrix accounts for members). Stdlib only.

The admin access token comes from one /login and is cached through the get_token/set_token
callables (the bot keeps it in its store). Only a 401 triggers a re-login: Synapse
rate-limits logins, so we never log in "just in case".
"""
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("synapse")
TIMEOUT = 15
RESERVED = {"admin", "administrator", "root", "matrix", "synapse", "support", "help", "bot",
            "system", "server", "mod", "moderator", "owner", "meowhub"}
_LOCALPART = re.compile(r"[a-z0-9._=-]{3,24}")


class SynapseError(Exception):
    def __init__(self, msg, code=None, status=None):
        super().__init__(msg)
        self.code = code
        self.status = status


class Synapse:
    def __init__(self, url, server_name, admin_user, admin_password, get_token, set_token):
        self.base = url.rstrip("/")
        self.server_name = server_name
        # Accept "admin" or "@admin:server".
        self.admin_localpart = admin_user.lstrip("@").split(":", 1)[0]
        self._password = admin_password
        self._get_token = get_token
        self._set_token = set_token
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, get_token, set_token):
        user = (os.environ.get("MATRIX_ADMIN_USER") or "").strip()
        pw = os.environ.get("MATRIX_ADMIN_PASSWORD") or ""
        if not user or not pw:
            return None
        return cls((os.environ.get("MATRIX_URL") or "").strip() or "http://matrix-synapse:8008",
                   (os.environ.get("MATRIX_SERVER_NAME") or os.environ.get("BASE_DOMAIN") or "").strip(),
                   user, pw, get_token, set_token)

    # -- names -------------------------------------------------------------
    def mxid(self, localpart):
        return f"@{localpart}:{self.server_name}"

    def valid_localpart(self, s):
        if not isinstance(s, str) or not _LOCALPART.fullmatch(s):
            return False
        return not (s in RESERVED or s.startswith("_") or s == self.admin_localpart)

    # -- transport ---------------------------------------------------------
    def _raw(self, method, path, body=None, token=None, query=None):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            raise SynapseError(payload.get("error") or f"HTTP {e.code}",
                               code=payload.get("errcode"), status=e.code) from None
        except urllib.error.URLError as e:
            raise SynapseError(f"network: {e.reason}") from None
        except (OSError, ValueError) as e:
            raise SynapseError(f"network: {e}") from None
        try:
            return json.loads(raw) if raw.strip() else {}
        except ValueError:
            raise SynapseError("bad response (not JSON)") from None

    def _login(self):
        res = self._raw("POST", "/_matrix/client/v3/login", {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": self.admin_localpart},
            "password": self._password,
            "initial_device_display_name": "meowhub-helper"})
        tok = res.get("access_token")
        if not tok:
            raise SynapseError("login returned no access_token")
        self._set_token(tok)
        return tok

    def _token(self, stale=None):
        """Cached token; log in when there is none or the cached one is the stale one."""
        with self._lock:  # concurrent callers must not each trigger a login
            tok = self._get_token()
            if tok and tok != stale:
                return tok
            return self._login()

    def _call(self, method, path, body=None, query=None):
        tok = self._token()
        try:
            return self._raw(method, path, body, tok, query)
        except SynapseError as e:
            if e.status != 401 and e.code != "M_UNKNOWN_TOKEN":
                raise
        return self._raw(method, path, body, self._token(stale=tok), query)

    @staticmethod
    def _q(s):
        return urllib.parse.quote(s, safe="")

    # -- admin API ---------------------------------------------------------
    def available(self, localpart):
        try:
            res = self._call("GET", "/_synapse/admin/v1/username_available",
                             query={"username": localpart})
        except SynapseError as e:
            if e.code == "M_USER_IN_USE":
                return False
            raise
        return bool(res.get("available", True))

    def create(self, localpart, password, displayname=""):
        if not self.valid_localpart(localpart):
            raise SynapseError("invalid or reserved username", code="M_INVALID_USERNAME")
        if not self.available(localpart):
            raise SynapseError("username is taken", code="M_USER_IN_USE")
        mxid = self.mxid(localpart)
        body = {"password": password, "admin": False, "logout_devices": False}
        if displayname:
            body["displayname"] = displayname
        self._call("PUT", f"/_synapse/admin/v2/users/{self._q(mxid)}", body)
        return mxid

    def set_locked(self, mxid, locked):
        self._call("PUT", f"/_synapse/admin/v2/users/{self._q(mxid)}", {"locked": bool(locked)})

    def reset_password(self, mxid, password):
        self._call("POST", f"/_synapse/admin/v1/reset_password/{self._q(mxid)}",
                   {"new_password": password, "logout_devices": True})

    def user(self, mxid):
        try:
            return self._call("GET", f"/_synapse/admin/v2/users/{self._q(mxid)}")
        except SynapseError as e:
            if e.status == 404 or e.code == "M_NOT_FOUND":
                return None
            raise
