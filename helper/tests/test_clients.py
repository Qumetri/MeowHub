"""Offline tests for xui.py / synapse.py: a local http.server fakes the endpoints."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from synapse import Synapse, SynapseError
from xui import XUI, XUIError


class Fake:
    """HTTP server on 127.0.0.1; `routes` maps (method, path-without-query) -> callable(req) -> (status, obj)."""

    def __init__(self):
        self.routes = {}
        self.calls = []  # (method, path, query, headers, body)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                path, _, query = self.path.partition("?")
                outer.calls.append((self.command, path, query, dict(self.headers), body))
                fn = outer.routes.get((self.command, path))
                status, obj = fn(outer.calls[-1]) if fn else (404, {"success": False, "msg": "no route"})
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _do

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def ok(obj=None):
    return lambda req: (200, {"success": True, "msg": "", "obj": obj})


class XUITests(unittest.TestCase):
    def setUp(self):
        self.f = Fake()
        self.addCleanup(self.f.close)
        self.x = XUI(self.f.url + "/base/", "s3cret-token")

    def test_from_env(self):
        import os
        old = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        os.environ["XUI_API_TOKEN"] = ""
        self.assertIsNone(XUI.from_env())
        os.environ["XUI_API_TOKEN"] = "t"
        os.environ.pop("XUI_URL", None)
        self.assertEqual(XUI.from_env().base, "https://host.docker.internal:10358")

    def test_bearer_and_base_url(self):
        self.f.routes[("GET", "/base/panel/api/clients/list")] = ok([{"email": "a"}])
        self.assertEqual(self.x.clients(), [{"email": "a"}])
        m, path, q, headers, body = self.f.calls[0]
        self.assertEqual(headers["Authorization"], "Bearer s3cret-token")

    def test_inbounds_projection(self):
        self.f.routes[("GET", "/base/panel/api/inbounds/list/slim")] = ok(
            [{"id": 1, "remark": "r", "protocol": "vless", "port": 443, "enable": True, "settings": {"x": 1}}])
        self.assertEqual(self.x.inbounds(),
                         [{"id": 1, "remark": "r", "protocol": "vless", "port": 443, "enable": True}])

    def test_success_false_raises_without_token(self):
        self.f.routes[("GET", "/base/panel/api/clients/list")] = lambda r: (
            200, {"success": False, "msg": "boom s3cret-token"})
        with self.assertRaises(XUIError) as cm:
            self.x.clients()
        self.assertIn("boom", str(cm.exception))
        self.assertNotIn("s3cret-token", str(cm.exception))

    def test_http_error_raises(self):
        with self.assertRaises(XUIError) as cm:
            self.x.clients()  # no route -> 404 JSON with msg
        self.assertEqual(cm.exception.status, 404)

    def test_client_get_not_found_variants(self):
        path = ("GET", "/base/panel/api/clients/get/mh-1")
        self.f.routes[path] = lambda r: (404, {"success": False, "msg": "not found"})
        self.assertIsNone(self.x.client_get("mh-1"))
        self.f.routes[path] = lambda r: (200, {"success": False, "msg": "record not found"})
        self.assertIsNone(self.x.client_get("mh-1"))
        self.f.routes[path] = ok(None)
        self.assertIsNone(self.x.client_get("mh-1"))
        self.f.routes[path] = lambda r: (200, {"success": False, "msg": "database is locked"})
        with self.assertRaises(XUIError):
            self.x.client_get("mh-1")
        self.f.routes[path] = ok({"email": "mh-1", "enable": True})
        self.assertEqual(self.x.client_get("mh-1")["email"], "mh-1")

    def test_email_is_url_encoded(self):
        self.f.routes[("GET", "/base/panel/api/clients/get/a%2Fb%40c")] = ok({"email": "a/b@c"})
        self.assertEqual(self.x.client_get("a/b@c")["email"], "a/b@c")

    def test_client_add_and_update_bodies(self):
        self.f.routes[("POST", "/base/panel/api/clients/add")] = ok()
        self.f.routes[("POST", "/base/panel/api/clients/update/mh-1")] = ok()
        c = {"email": "mh-1", "enable": True, "expiryTime": 5, "subId": "abc", "flow": "x", "tgId": 0}
        self.x.client_add({"email": "mh-1", "enable": True}, [1, 2])
        self.x.client_update("mh-1", c)
        self.assertEqual(self.f.calls[0][4], {"client": {"email": "mh-1", "enable": True}, "inboundIds": [1, 2]})
        self.assertEqual(self.f.calls[1][4], c)  # full record, not a patch

    def test_attach_detach_delete_links_onlines(self):
        base = "/base/panel/api/clients"
        self.f.routes[("POST", base + "/mh-1/attach")] = ok()
        self.f.routes[("POST", base + "/mh-1/detach")] = ok()
        self.f.routes[("POST", base + "/del/mh-1")] = ok()
        self.f.routes[("GET", base + "/links/mh-1")] = ok(["vless://a", "vless://b"])
        self.f.routes[("POST", base + "/onlines")] = ok(["mh-1"])
        self.x.client_attach("mh-1", [3])
        self.x.client_detach("mh-1", [4, 5])
        self.x.client_delete("mh-1")
        self.assertEqual(self.x.client_links("mh-1"), ["vless://a", "vless://b"])
        self.assertEqual(self.x.onlines(), ["mh-1"])
        c = self.f.calls
        self.assertEqual(c[0][4], {"inboundIds": [3]})
        self.assertEqual(c[1][4], {"inboundIds": [4, 5]})
        self.assertEqual(c[2][2], "keepTraffic=0")

    def test_unreachable_raises_xuierror(self):
        self.f.close()
        with self.assertRaises(XUIError):
            self.x.clients()
        self.f.srv = type("S", (), {"shutdown": lambda s: None, "server_close": lambda s: None})()


class SynapseTests(unittest.TestCase):
    def setUp(self):
        self.f = Fake()
        self.addCleanup(self.f.close)
        self.store = {}
        self.logins = 0
        self.valid = {"tok1"}
        self.f.routes[("POST", "/_matrix/client/v3/login")] = self._login
        self.s = Synapse(self.f.url + "/", "example.org", "boss", "pw",
                         lambda: self.store.get("t"), lambda t: self.store.__setitem__("t", t))

    def _login(self, req):
        self.logins += 1
        tok = f"tok{self.logins}"
        self.valid.add(tok)
        return 200, {"access_token": tok}

    def _guard(self, obj):
        def fn(req):
            tok = req[3].get("Authorization", "")[len("Bearer "):]
            if tok not in self.valid:
                return 401, {"errcode": "M_UNKNOWN_TOKEN", "error": "bad token"}
            return 200, obj
        return fn

    def test_login_once_and_cached(self):
        self.valid.clear()
        self.f.routes[("GET", "/_synapse/admin/v1/username_available")] = self._guard({"available": True})
        self.assertTrue(self.s.available("bob"))
        self.assertTrue(self.s.available("bob"))
        self.assertEqual(self.logins, 1)
        self.assertEqual(self.store["t"], "tok1")
        login = [c for c in self.f.calls if c[1].endswith("/login")][0][4]
        self.assertEqual(login["identifier"], {"type": "m.id.user", "user": "boss"})
        self.assertEqual(login["type"], "m.login.password")

    def test_401_relogins_exactly_once(self):
        self.store["t"] = "stale"
        self.f.routes[("GET", "/_synapse/admin/v1/username_available")] = self._guard({"available": True})
        self.assertTrue(self.s.available("bob"))
        self.assertEqual(self.logins, 1)
        self.assertEqual(self.store["t"], "tok1")
        # a persistently rejected token must not loop
        self.f.routes[("GET", "/_synapse/admin/v1/username_available")] = lambda r: (
            401, {"errcode": "M_UNKNOWN_TOKEN", "error": "x"})
        with self.assertRaises(SynapseError) as cm:
            self.s.available("bob")
        self.assertEqual(cm.exception.code, "M_UNKNOWN_TOKEN")
        self.assertEqual(self.logins, 2)

    def test_login_failure_carries_errcode(self):
        self.f.routes[("POST", "/_matrix/client/v3/login")] = lambda r: (
            403, {"errcode": "M_FORBIDDEN", "error": "Invalid username or password"})
        with self.assertRaises(SynapseError) as cm:
            self.s.available("bob")
        self.assertEqual(cm.exception.code, "M_FORBIDDEN")

    def test_create_refuses_taken_name(self):
        self.store["t"] = "tok1"
        self.f.routes[("GET", "/_synapse/admin/v1/username_available")] = lambda r: (
            400, {"errcode": "M_USER_IN_USE", "error": "User ID already taken."})
        with self.assertRaises(SynapseError) as cm:
            self.s.create("bob", "pw12345678")
        self.assertEqual(cm.exception.code, "M_USER_IN_USE")
        self.assertFalse([c for c in self.f.calls if c[0] == "PUT"])

    def test_create_ok_and_mxid_encoding(self):
        self.store["t"] = "tok1"
        self.f.routes[("GET", "/_synapse/admin/v1/username_available")] = self._guard({"available": True})
        self.f.routes[("PUT", "/_synapse/admin/v2/users/%40bob%3Aexample.org")] = self._guard({})
        self.assertEqual(self.s.create("bob", "pw12345678", "Bob"), "@bob:example.org")
        put = [c for c in self.f.calls if c[0] == "PUT"][0]
        self.assertEqual(put[4]["password"], "pw12345678")
        self.assertEqual(put[4]["displayname"], "Bob")
        self.assertFalse(put[4]["admin"])

    def test_create_rejects_invalid_and_reserved(self):
        for bad in ("admin", "boss", "ab", "_hidden"):
            with self.assertRaises(SynapseError):
                self.s.create(bad, "pw12345678")
        self.assertEqual(self.f.calls, [])

    def test_locked_reset_and_user(self):
        self.store["t"] = "tok1"
        enc = "%40bob%3Aexample.org"
        self.f.routes[("PUT", "/_synapse/admin/v2/users/" + enc)] = self._guard({})
        self.f.routes[("POST", "/_synapse/admin/v1/reset_password/" + enc)] = self._guard({})
        self.f.routes[("GET", "/_synapse/admin/v2/users/" + enc)] = self._guard({"name": "x"})
        self.s.set_locked("@bob:example.org", True)
        self.s.reset_password("@bob:example.org", "newpw")
        self.assertEqual(self.f.calls[0][4], {"locked": True})
        self.assertEqual(self.f.calls[1][4], {"new_password": "newpw", "logout_devices": True})
        self.assertEqual(self.s.user("@bob:example.org"), {"name": "x"})
        self.f.routes[("GET", "/_synapse/admin/v2/users/" + enc)] = lambda r: (
            404, {"errcode": "M_NOT_FOUND", "error": "User not found"})
        self.assertIsNone(self.s.user("@bob:example.org"))

    def test_valid_localpart(self):
        v = self.s.valid_localpart
        for good in ("bob", "a.b-c_d".replace("_", "="), "user123", "x" * 24, "a=b"):
            self.assertTrue(v(good), good)
        for bad in ("", "ab", "x" * 25, "Bob", "bob smith", "bob\n", "_bob", "admin", "root", "meowhub",
                    "moderator", "boss", "bob@x", None, 5):
            self.assertFalse(v(bad), repr(bad))

    def test_from_env(self):
        import os
        old = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        for k in ("MATRIX_ADMIN_USER", "MATRIX_ADMIN_PASSWORD", "MATRIX_URL", "MATRIX_SERVER_NAME"):
            os.environ.pop(k, None)
        os.environ["BASE_DOMAIN"] = "ex.org"
        self.assertIsNone(Synapse.from_env(None, None))
        os.environ["MATRIX_ADMIN_USER"] = "boss"
        self.assertIsNone(Synapse.from_env(None, None))
        os.environ["MATRIX_ADMIN_PASSWORD"] = "pw"
        s = Synapse.from_env(None, None)
        self.assertEqual((s.base, s.server_name, s.mxid("a")), ("http://matrix-synapse:8008", "ex.org", "@a:ex.org"))


if __name__ == "__main__":
    unittest.main()


class PayloadTest(unittest.TestCase):
    """Shapes measured on the live 3.8.5 panel (2026-10-07)."""

    def test_list_record_to_update_payload(self):
        from xui import XUI
        rec = {"id": 70, "uuid": "451fc5b0-0000", "email": "mh-1", "allowedIPs": "",
               "inboundIds": [1, 7], "traffic": {"up": 1}, "createdAt": 1, "updatedAt": 2,
               "reverse": None, "enable": True}
        p = XUI._payload(rec)
        self.assertEqual(p["id"], "451fc5b0-0000")
        self.assertEqual(p["allowedIPs"], [])
        for k in ("uuid", "inboundIds", "traffic", "createdAt", "updatedAt", "reverse"):
            self.assertNotIn(k, p)

    def test_no_uuid_drops_numeric_id(self):
        from xui import XUI
        self.assertNotIn("id", XUI._payload({"id": 5, "email": "x"}))
