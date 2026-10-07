"""HTTP API + static file serving for the crypto tracker. Stdlib only.

Served behind Caddy under a secret path with basic auth; handle_path strips
the prefix, so everything here is rooted at / and the page uses relative URLs.
"""
import json
import logging
import mimetypes
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import binance
import kraken
import news
import telegram

log = logging.getLogger("http")
WEB_DIR = os.environ.get("CRYPTO_WEB", "/app/web")
MAX_BODY = 256 * 1024


class Hub:
    """Fan-out for server-sent events. One bounded queue per browser tab."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def add(self):
        q = queue.Queue(maxsize=50)
        with self.lock:
            self.clients.add(q)
        return q

    def remove(self, q):
        with self.lock:
            self.clients.discard(q)

    def broadcast(self, payload):
        msg = json.dumps(payload, separators=(",", ":"))
        with self.lock:
            targets = list(self.clients)
        for q in targets:
            try:
                q.put_nowait(msg)
            except queue.Full:
                # A stalled tab must not slow the feed; it will resync on
                # its next /api/state poll.
                pass


def mask_token(tok):
    if not tok:
        return ""
    return ("*" * max(0, len(tok) - 6)) + tok[-6:] if len(tok) > 6 else "*" * len(tok)


def _stamp_assets(html):
    """Append ?v=<mtime> to the page's own script/stylesheet references."""
    out = html.decode("utf-8", "replace")
    for name in ("app.js", "style.css"):
        try:
            v = int(os.stat(os.path.join(WEB_DIR, name)).st_mtime)
        except OSError:
            continue
        out = out.replace(f'"{name}"', f'"{name}?v={v}"')
    return out.encode()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "meowhub-crypto"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # ---------- helpers ----------
        def _send(self, code, body=b"", ctype="application/json", extra=None):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, separators=(",", ":")))

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            if n > MAX_BODY:
                raise ValueError("body too large")
            raw = self.rfile.read(n)
            try:
                d = json.loads(raw.decode())
            except ValueError:
                raise ValueError("invalid JSON")
            if not isinstance(d, dict):
                raise ValueError("expected a JSON object")
            return d

        # ---------- routing ----------
        def do_GET(self):
            try:
                self._route_get()
            except BrokenPipeError:
                pass
            except Exception as e:                     # noqa: BLE001
                log.exception("GET %s", self.path)
                try:
                    self._err(500, str(e))
                except Exception:
                    pass

        do_HEAD = do_GET

        def do_POST(self):
            try:
                self._route_post()
            except ValueError as e:
                self._err(400, str(e))
            except BrokenPipeError:
                pass
            except Exception as e:                     # noqa: BLE001
                log.exception("POST %s", self.path)
                try:
                    self._err(500, str(e))
                except Exception:
                    pass

        def do_DELETE(self):
            try:
                u = urlparse(self.path)
                p = u.path.rstrip("/")
                if p.startswith("/api/targets/"):
                    app.store.delete_target(int(p.rsplit("/", 1)[1]))
                    return self._json({"ok": True})
                if p.startswith("/api/coins/"):
                    app.remove_coin(p.rsplit("/", 1)[1].upper())
                    return self._json({"ok": True})
                self._err(404, "not found")
            except Exception as e:                     # noqa: BLE001
                self._err(500, str(e))

        # ---------- GET ----------
        def _route_get(self):
            u = urlparse(self.path)
            p = u.path
            q = parse_qs(u.query)

            if p == "/api/state":
                return self._json(app.state())
            if p == "/api/events":
                return self._json({"events": app.store.events(
                    limit=min(int(q.get("limit", ["60"])[0]), 500))})
            if p == "/api/candles":
                sym = (q.get("symbol", [""])[0] or "").upper()
                if not sym:
                    return self._err(400, "symbol required")
                minutes = min(int(q.get("minutes", ["1440"])[0]), 60 * 24 * 30)
                since = int(time.time()) - minutes * 60
                return self._json({"symbol": sym,
                                   "candles": app.store.candles(sym, since=since, limit=5000)})
            if p == "/api/digest":
                # Everything the daily summary needs in one call. Slow on a cold
                # cache (one news fetch per coin), so the caller should allow ~60s.
                hours = min(max(int(q.get("hours", ["24"])[0]), 1), 72)
                with_news = q.get("news", ["1"])[0] != "0"
                fresh = q.get("fresh", ["0"])[0] == "1"
                return self._json(news.build_digest(app, hours=hours, with_news=with_news, fresh=fresh))
            if p == "/api/health":
                st = app._feed_status()
                return self._json({"ok": True, "connected": st["connected"],
                                   "sources": st["sources"]})
            if p == "/api/stream":
                return self._sse()
            return self._static(p)

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            cq = app.hub.add()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = cq.get(timeout=20)
                        self.wfile.write(f"data: {msg}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")   # keep proxies from idling us out
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                app.hub.remove(cq)

        def _static(self, p):
            rel = "index.html" if p in ("/", "") else p.lstrip("/")
            full = os.path.normpath(os.path.join(WEB_DIR, rel))
            if not full.startswith(os.path.realpath(WEB_DIR)) and not full.startswith(WEB_DIR):
                return self._err(403, "forbidden")
            if os.path.isdir(full):
                full = os.path.join(full, "index.html")
            if not os.path.isfile(full):
                return self._err(404, "not found")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"

            st = os.stat(full)
            etag = '"%x-%x"' % (int(st.st_mtime), st.st_size)

            # The page's own code must revalidate on every load. Serving app.js
            # with a plain max-age and no validator means a UI change stays
            # invisible until the cache expires, with no way for the browser to
            # ask whether it is stale. Icons and vendored libraries are content-
            # stable for a given filename, so they may sit in cache, but they
            # still carry an ETag so a replacement is picked up.
            long_lived = rel.startswith(("icons/", "vendor/"))
            cache = "public, max-age=86400" if long_lived else "no-cache"

            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", cache)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            with open(full, "rb") as f:
                data = f.read()

            # Cache-bust the page's own assets by stamping their mtime into the
            # URL. index.html always revalidates, so a changed stamp forces the
            # browser to fetch the new app.js even when it is still holding an
            # older copy it thinks is fresh -- otherwise a UI change is invisible
            # until that entry expires, and the user has to know to hard-refresh.
            if rel == "index.html":
                data = _stamp_assets(data)

            self._send(200, data, ctype,
                       {"Cache-Control": cache, "ETag": etag,
                        "Last-Modified": self.date_time_string(int(st.st_mtime))})

        # ---------- POST ----------
        def _route_post(self):
            p = urlparse(self.path).path.rstrip("/")
            b = self._body()

            if p == "/api/targets":
                sym = (b.get("symbol") or "").upper()
                direction = b.get("direction")
                if direction not in ("above", "below"):
                    raise ValueError("direction must be 'above' or 'below'")
                price = float(b.get("price", 0))
                if price <= 0:
                    raise ValueError("price must be positive")
                if sym not in {c["symbol"] for c in app.store.coins()}:
                    raise ValueError(f"{sym} is not a tracked coin")
                # Arm relative to the current price so a target that is already
                # satisfied waits for a genuine crossing instead of firing now.
                cur = app.engine.prices.get(sym)
                armed = 1
                if cur is not None:
                    armed = 0 if (price <= cur if direction == "above" else price >= cur) else 1
                tid = app.store.add_target(
                    sym, direction, price, note=(b.get("note") or "")[:200],
                    repeat=1 if b.get("repeat") else 0,
                    cooldown_s=max(0, int(b.get("cooldown_s", 900))), armed=armed)
                app.engine.refresh_rules(force=True)
                return self._json({"ok": True, "id": tid, "armed": armed})

            if p.startswith("/api/targets/"):
                tid = int(p.rsplit("/", 1)[1])
                fields = {}
                for k in ("enabled", "repeat", "cooldown_s"):
                    if k in b:
                        fields[k] = int(b[k])
                if "price" in b:
                    fields["price"] = float(b["price"])
                if "note" in b:
                    fields["note"] = str(b["note"])[:200]
                if "direction" in b:
                    if b["direction"] not in ("above", "below"):
                        raise ValueError("bad direction")
                    fields["direction"] = b["direction"]
                if fields.get("enabled"):
                    fields["armed"] = 1        # re-enabling re-arms
                app.store.update_target(tid, **fields)
                app.engine.refresh_rules(force=True)
                return self._json({"ok": True})

            if p == "/api/fluctuation":
                sym = (b.get("symbol") or "").upper()
                fields = {}
                if "pct" in b:
                    fields["pct"] = max(0.0, float(b["pct"]))
                if "window_s" in b:
                    fields["window_s"] = max(30, int(b["window_s"]))
                if "cooldown_s" in b:
                    fields["cooldown_s"] = max(0, int(b["cooldown_s"]))
                if "enabled" in b:
                    fields["enabled"] = 1 if b["enabled"] else 0
                if "urgent_pct" in b:
                    fields["urgent_pct"] = max(0.0, float(b["urgent_pct"]))
                if "urgent_repeat" in b:
                    fields["urgent_repeat"] = min(5, max(1, int(b["urgent_repeat"])))
                # A changed threshold or window invalidates the open episode.
                if {"pct", "window_s"} & set(fields):
                    fields.update(ep_dir=0, ep_anchor=0, ep_step=0, ep_peak=0, ep_pb=0)
                app.store.set_fluctuation(sym, **fields)
                app.engine.refresh_rules(force=True)
                return self._json({"ok": True})

            if p == "/api/settings":
                allowed = {"tg_chat_id", "tg_enabled", "summary_enabled",
                           "summary_hour", "quiet_hours", "retention_days"}
                out = {k: v for k, v in b.items() if k in allowed}
                # Never overwrite a stored token with the masked placeholder.
                tok = b.get("tg_token")
                if tok and "*" not in tok:
                    out["tg_token"] = tok.strip()
                # Set by the helper bot's Bots page; saved even when .env has a token.
                if "tg_token_override" in b:
                    ov = str(b.get("tg_token_override") or "").strip()
                    if len(ov) > 200:
                        return self._json({"ok": False, "error": "tg_token_override too long"}, 400)
                    out["tg_token_override"] = ov
                if out:
                    app.store.set_many(out)
                return self._json({"ok": True, "settings": app.public_settings()})

            if p == "/api/telegram/test":
                s = app.store.settings()
                tok = (b.get("tg_token") or s.get("tg_token") or "").strip()
                chat = (b.get("tg_chat_id") or s.get("tg_chat_id") or "").strip()
                try:
                    who = telegram.verify(tok)
                    telegram.send(tok, chat,
                                  "✅ <b>MeowHub crypto tracker</b>\n\n"
                                  "Telegram delivery is working. Alerts will arrive here.")
                except telegram.TelegramError as e:
                    return self._json({"ok": False, "error": str(e)}, 200)
                return self._json({"ok": True, "bot": who})

            if p == "/api/telegram/detect":
                s = app.store.settings()
                tok = (b.get("tg_token") or s.get("tg_token") or "").strip()
                try:
                    return self._json({"ok": True, "chats": telegram.discover_chat_id(tok)})
                except telegram.TelegramError as e:
                    return self._json({"ok": False, "error": str(e)}, 200)

            if p == "/api/coins":
                raw = (b.get("symbol") or "").upper().strip().replace("/", "")
                if not raw.isalnum() or not 2 <= len(raw) <= 20:
                    raise ValueError("bad symbol")
                existing = {c["symbol"] for c in app.store.coins()}

                # Try Binance first (deeper history, 1000-candle backfill), then
                # Kraken. Binance must be actually TRADING: a delisted symbol
                # still answers /ticker/price with a frozen number.
                cands = []
                if raw.endswith("USDT"):
                    cands.append(("binance", raw))
                elif raw.endswith(("USD", "EUR", "USDC")):
                    cands.append(("kraken", raw))
                else:
                    cands.append(("binance", raw + "USDT"))
                    cands.append(("kraken", raw + "USD"))

                chosen = None
                for src, cand in cands:
                    if cand in existing:
                        raise ValueError(f"{cand} is already tracked")
                    ok = (binance.symbol_exists(cand) if src == "binance"
                          else kraken.symbol_exists(cand))
                    if ok:
                        chosen = (src, cand)
                        break
                if not chosen:
                    tried = ", ".join(c for _, c in cands)
                    raise ValueError(
                        f"no exchange is currently trading {tried}. "
                        "Binance-delisted pairs are rejected on purpose — they keep "
                        "returning the price they froze at.")

                src, sym = chosen
                base = sym[:-4] if sym.endswith("USDT") else sym[:-3]
                ticker = (b.get("ticker") or base).upper()[:8]
                app.add_coin(sym, ticker, b.get("name") or base, src)
                return self._json({"ok": True, "symbol": sym, "source": src})

            if p.startswith("/api/coins/") and p.endswith("/replace"):
                old_sym = p[len("/api/coins/"):-len("/replace")].upper()
                if old_sym not in {c["symbol"] for c in app.store.coins()}:
                    raise ValueError(f"{old_sym} is not tracked")
                raw = (b.get("symbol") or "").upper().strip().replace("/", "")
                if not raw.isalnum() or not 2 <= len(raw) <= 20:
                    raise ValueError("bad replacement symbol")
                cands = ([("binance", raw)] if raw.endswith("USDT")
                         else [("kraken", raw)] if raw.endswith(("USD", "EUR", "USDC"))
                         else [("binance", raw + "USDT"), ("kraken", raw + "USD")])
                chosen = None
                for src, cand in cands:
                    ok = (binance.symbol_exists(cand) if src == "binance"
                          else kraken.symbol_exists(cand))
                    if ok:
                        chosen = (src, cand)
                        break
                if not chosen:
                    raise ValueError("no exchange is currently trading "
                                     + ", ".join(c for _, c in cands))
                src, new_sym = chosen
                base = new_sym[:-4] if new_sym.endswith("USDT") else new_sym[:-3]
                app.replace_coin(old_sym, new_sym,
                                 (b.get("ticker") or base).upper()[:8],
                                 b.get("name") or base, src)
                return self._json({"ok": True, "symbol": new_sym, "source": src})

            if p == "/api/summary/send":
                ok = app.engine.send_summary(force=True)
                return self._json({"ok": ok})

            self._err(404, "not found")

    return Handler


def serve(app, host="0.0.0.0", port=9102):
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    log.info("http listening on %s:%d", host, port)
    httpd.serve_forever()
