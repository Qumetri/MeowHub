"""Setup from the command line -- the bot has no web UI.

  The token normally goes in .env as HELPER_BOT_TOKEN. Without .env access:
  docker compose exec -it helper python3 /app/app/ctl.py token
        paste the token from @BotFather (input hidden). Verified, then stored
        in the bot's sqlite (used only when HELPER_BOT_TOKEN is empty). Then
        offers to make whoever sends /start next the owner.
  docker compose exec helper python3 /app/app/ctl.py owner <telegram user id>
  docker compose exec helper python3 /app/app/ctl.py status
"""
import getpass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from store import Store          # noqa: E402
from tg import Bot, TelegramError  # noqa: E402


def main():
    st = Store(os.environ.get("HELPER_DB", "/data/helper.db"))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "token":
        tok = (getpass.getpass("Bot token: ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
        try:
            me = Bot(tok).call("getMe")
        except TelegramError as e:
            sys.exit(f"token rejected: {e}")
        st.set("tg_token", tok)
        st.set("offset", "0")
        print(f"ok: @{me['username']}")
        if os.environ.get("HELPER_OWNER_ID") or st.get("owner_id") or not sys.stdin.isatty():
            print("restart to apply: docker compose restart helper")
            return
        print(f"Now send /start to @{me['username']} from your own Telegram account (2 min)...")
        bot, t0, off = Bot(tok), time.time(), None
        while time.time() - t0 < 120:
            for u in bot.call("getUpdates", _timeout=40, timeout=30, offset=off):
                off = u["update_id"] + 1
                f = (u.get("message") or {}).get("from") or {}
                if f.get("id") and input(f"Make {f.get('first_name')} (id {f['id']}) the owner? [y/N] ").lower() == "y":
                    st.set("owner_id", f["id"])
                    print("owner set. restart: docker compose restart helper")
                    return
        print("no /start received; set it later with: ctl.py owner <id>")
    elif cmd == "owner" and len(sys.argv) > 2 and sys.argv[2].lstrip("-").isdigit():
        st.set("owner_id", sys.argv[2])
        print("owner set. restart: docker compose restart helper")
    elif cmd == "status":
        src = ("from .env" if os.environ.get("HELPER_BOT_TOKEN", "").strip()
               else "from ctl.py (sqlite)" if st.get("tg_token") else "MISSING")
        print("token:", src)
        print("owner:", os.environ.get("HELPER_OWNER_ID") or st.get("owner_id") or "MISSING")
        print("users:", len(st.users()), " bot-stored logins:", len(st.vault()))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
