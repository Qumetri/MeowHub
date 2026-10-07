"""Telegram Bot API client. Stdlib only, like the rest of the hub."""
import json
import logging
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger("tg")
API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


class Bot:
    def __init__(self, token):
        self.token = token

    def _open(self, req, timeout):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
            except Exception:
                raise TelegramError(f"HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise TelegramError(f"network: {e.reason}") from None
        if not payload.get("ok"):
            raise TelegramError(payload.get("description", "unknown error"))
        return payload["result"]

    def call(self, method, _timeout=30, **params):
        # JSON, not form encoding: reply_markup is a nested object.
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=body,
                                     headers={"Content-Type": "application/json"})
        return self._open(req, _timeout)

    def upload(self, method, field, path, _timeout=600, **params):
        """multipart/form-data upload (sendVideo, sendAudio, sendDocument)."""
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        name = os.path.basename(path)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n').encode()
        with open(path, "rb") as f:
            data = b"".join(parts) + head + f.read() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=data,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return self._open(req, _timeout)

    def file_bytes(self, file_path, max_bytes=5 * 1024 * 1024):
        """Download a file by the `file_path` that getFile returned (15 s, max 5 MB).
        The URL carries the bot token, so no exception text may include it."""
        url = f"{API}/file/bot{self.token}/{urllib.parse.quote(file_path, safe='/')}"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = r.read(max_bytes + 1)
        except urllib.error.HTTPError as e:
            raise TelegramError(f"HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise TelegramError(f"network: {str(e.reason).replace(self.token, '***')}") from None
        except (OSError, ValueError) as e:
            raise TelegramError(f"download failed: {type(e).__name__}") from None
        if len(data) > max_bytes:
            raise TelegramError("file too large")
        return data

    # -- conveniences ------------------------------------------------------
    def send(self, chat_id, text, **kw):
        kw.setdefault("parse_mode", "HTML")
        kw.setdefault("link_preview_options", {"is_disabled": True})
        return self.call("sendMessage", chat_id=chat_id, text=text, **kw)

    def edit(self, chat_id, message_id, text, **kw):
        kw.setdefault("parse_mode", "HTML")
        kw.setdefault("link_preview_options", {"is_disabled": True})
        try:
            return self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                             text=text, **kw)
        except TelegramError as e:
            if "not modified" not in str(e):
                raise

    def delete(self, chat_id, message_id):
        try:
            self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        except TelegramError as e:
            log.info("delete %s/%s: %s", chat_id, message_id, e)

    def answer(self, callback_id, text=None, alert=False):
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id,
                      text=text, show_alert=alert)
        except TelegramError:
            pass
