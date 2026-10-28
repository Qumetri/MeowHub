"""One-off announcement to members through the member bot, each in their own language.

  docker exec helper_bot python3 /app/app/announce.py <i18n key> [service] [--dry-run] [--force]

Recipients: active members who have `service` (default vpn). The text is the i18n key, rendered
per recipient (`i18n.lang_of`); `{contact}` in it becomes OWNER_CONTACT. A key is sent once (settings `announced_<key>`) unless --force.
The owner gets a one-line summary through the helper bot; the run is logged as event `broadcast`.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import i18n                      # noqa: E402
import tokens                    # noqa: E402
from members import Members      # noqa: E402
from store import Store          # noqa: E402
from tg import Bot, TelegramError  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry, force = "--dry-run" in sys.argv, "--force" in sys.argv
    if not args or args[0] not in i18n.T:
        sys.exit("usage: announce.py <i18n key> [service] [--dry-run] [--force]")
    key, service = args[0], (args[1] if len(args) > 1 else "vpn")
    st = Store(os.environ.get("HELPER_DB", "/data/helper.db"))
    if st.get("announced_" + key) and not force:
        sys.exit(f"{key} was already sent ({st.get('announced_' + key)}); --force to send again")
    mem = Members(st)
    v = os.environ.get("HELPER_OWNER_ID", "").strip() or st.get("owner_id") or ""
    owner = int(v) if v.lstrip("-").isdigit() else None
    who = [m for m in mem.list() if mem.status(m) == "active" and mem.has(m["id"], service) and m["id"] != owner]
    tok = tokens.resolve(st, "member")[0] or tokens.resolve(st, "helper")[0]
    bot = Bot(tok)
    ok = fail = 0
    contact = (os.environ.get("OWNER_CONTACT") or "").strip()
    for m in who:
        text = i18n.tr(i18n.lang_of(st, m["id"]), key, contact=contact)
        if dry:
            print(m["id"], i18n.lang_of(st, m["id"]))
            continue
        try:
            bot.send(m["id"], text)
            ok += 1
        except TelegramError as e:
            print(f"{m['id']}: {e}")
            fail += 1
        time.sleep(0.1)
    if dry:
        print(f"{len(who)} recipients (dry run)\n---\n{i18n.tr('ru', key, contact=contact)}")
        return
    st.set("announced_" + key, time.strftime("%Y-%m-%d %H:%M"))
    mem.log(None, "broadcast", f"{key} ok={ok} fail={fail}")
    helper_tok = tokens.resolve(st, "helper")[0]
    if owner and helper_tok:
        try:
            Bot(helper_tok).send(owner, f"📣 <code>{key}</code>: sent to {ok}, failed {fail}.")
        except TelegramError:
            pass
    print(f"sent {ok}, failed {fail}")


if __name__ == "__main__":
    main()
