"""What the hub knows about itself: the service cards (read from the
dashboard build's services.json, so a new card appears in the bot without
touching it) and the logins for those services.

Logins come from two places:
  env     VAULT_<ID>_USER plus VAULT_<ID>_PASS or VAULT_<ID>_PASS_FILE, mapped
          in compose from .env / secrets/. Read-only from the bot.
  store   added by the owner with /setpass. For accounts whose password exists
          nowhere else in plaintext (3x-ui, Immich, n8n -- they keep hashes).
A store entry overrides an env one with the same id.
"""
import json
import logging
import os
import re
from urllib.parse import urlsplit

log = logging.getLogger("hub")
SERVICES = os.environ.get("HUB_SERVICES", "/hub/services.json")


def base_domain():
    return os.environ.get("BASE_DOMAIN", "").strip()


def hub_url():
    b, p = base_domain(), os.environ.get("DASHBOARD_PATH", "").strip("/")
    return f"https://{b}/{p}/" if b and p else ""


def services():
    try:
        with open(SERVICES) as f:
            return [s for s in json.load(f) if s.get("url")]
    except (OSError, ValueError) as e:
        log.warning("services.json unreadable (%s) -- rebuild the dashboard", e)
        return []


def service_name(sid):
    for s in services():
        if s.get("id") == sid:
            return s.get("name") or sid
    return sid


def service_url(sid):
    for s in services():
        if s.get("id") == sid:
            return s.get("url", "")
    return ""


def own_hosts():
    """(host, port) of every card on our own domain -- the things whose
    certificates, routes and DNS records we are responsible for."""
    b = base_domain()
    out = []
    for s in services():
        u = urlsplit(s["url"])
        if u.scheme == "https" and b and (u.hostname == b or (u.hostname or "").endswith("." + b)):
            out.append((s.get("id"), s.get("name"), u.hostname, u.port or 443, s["url"]))
    return out


def _env_vault():
    out = {}
    for k, v in os.environ.items():
        m = re.fullmatch(r"VAULT_([A-Z0-9_]+?)_USER", k)
        if not m or not v:
            continue
        key = m.group(1)
        pw = os.environ.get(f"VAULT_{key}_PASS", "")
        pf = os.environ.get(f"VAULT_{key}_PASS_FILE", "")
        if not pw and pf:
            try:
                with open(pf) as f:
                    pw = f.read().strip()
            except OSError:
                pw = ""
        if pw:
            out[key.lower()] = {"id": key.lower(), "login": v, "password": pw,
                                "note": os.environ.get(f"VAULT_{key}_NOTE", ""), "source": "env"}
    return out


def vault(store):
    v = _env_vault()
    for vid, e in store.vault().items():
        v[vid] = {**e, "source": "bot"}
    return v
