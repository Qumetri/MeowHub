#!/usr/bin/env python3
"""Send Claude Code reports to the owner's Telegram via @norwquackbot.

  tg-report.py "text"              send a message (split at 4096 chars)
  echo text | tg-report.py         same, from stdin
  tg-report.py -f FILE [caption]   send a file as a document (repeat -f for several)

Token and chat id come from CLAUDE_REPORT_BOT_TOKEN / CLAUDE_REPORT_CHAT_ID in the
stack's .env. Send-only: the 3x-ui panel polls this bot, so never call getUpdates.
"""
import json
import mimetypes
import os
import sys
import urllib.request
import uuid

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
LIMIT = 4096


def load_env():
    cfg = {}
    with open(ENV) as f:
        for line in f:
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.rstrip('\n').split('=', 1)
                cfg[k.strip()] = v.strip()
    return cfg['CLAUDE_REPORT_BOT_TOKEN'], cfg['CLAUDE_REPORT_CHAT_ID']


def call(token, method, data=None, body=None, ctype=None):
    url = f'https://api.telegram.org/bot{token}/{method}'
    if body is None:
        body = json.dumps(data).encode()
        ctype = 'application/json'
    req = urllib.request.Request(url, body, {'Content-Type': ctype})
    with urllib.request.urlopen(req, timeout=300) as r:
        res = json.load(r)
    if not res.get('ok'):
        raise SystemExit(f'telegram error: {res}')
    return res


def chunks(text):
    while len(text) > LIMIT:
        cut = text.rfind('\n', 0, LIMIT)
        if cut <= 0:
            cut = LIMIT
        yield text[:cut]
        text = text[cut:].lstrip('\n')
    if text.strip():
        yield text


def send_text(token, chat, text):
    for part in chunks(text):
        call(token, 'sendMessage', {'chat_id': chat, 'text': part,
                                    'disable_web_page_preview': True})


def send_file(token, chat, path, caption=''):
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    with open(path, 'rb') as f:
        content = f.read()
    parts = []
    for k, v in (('chat_id', chat), ('caption', caption[:1024])):
        if v:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"'
                         f'\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                 f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
                 + content + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    call(token, 'sendDocument', body=b''.join(parts),
         ctype=f'multipart/form-data; boundary={boundary}')


def main(argv):
    token, chat = load_env()
    if argv and argv[0] == '-f':
        files, caption, i = [], '', 0
        while i < len(argv):
            if argv[i] == '-f' and i + 1 < len(argv):
                files.append(argv[i + 1])
                i += 2
            else:
                caption = ' '.join(argv[i:])
                break
        for path in files:
            send_file(token, chat, path, caption)
        return
    text = ' '.join(argv) if argv else sys.stdin.read()
    if not text.strip():
        raise SystemExit(__doc__)
    send_text(token, chat, text)


if __name__ == '__main__':
    main(sys.argv[1:])
