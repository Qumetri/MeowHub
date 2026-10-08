"""Russian / English: the resolve rule, stored preferences, the catalog, /api/me + /api/lang,
localized error messages, the bots' /lang flow and keyboards, notifications in the
recipient's language, and the command lists."""
import os
import string
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import downloads as dlm                   # noqa: E402
import health                             # noqa: E402
import i18n                               # noqa: E402
import members as mm                      # noqa: E402
import test_bot as tb                     # noqa: E402
import test_members as tmem               # noqa: E402
import test_webapp as tw                  # noqa: E402
import webapp                             # noqa: E402
from store import Store                   # noqa: E402

DAY = 86400
OWNER = tw.OWNER


def user_en(uid, code="en", first="Bob"):
    return {"id": uid, "first_name": first, "username": "bob", "language_code": code}


# ------------------------------------------------------------------ rules --
class TestResolve(unittest.TestCase):
    def test_pref_beats_language_code(self):
        self.assertEqual(i18n.resolve("en", "ru"), "en")
        self.assertEqual(i18n.resolve("ru", "de"), "ru")

    def test_auto_follows_telegram(self):
        for code in ("ru", "uk", "be", "kk", "ru-RU", "RU", "uk_UA"):
            self.assertEqual(i18n.resolve("auto", code), "ru", code)
        for code in ("en", "en-GB", "de", "fr", "es", "zh-hans", "ar"):
            self.assertEqual(i18n.resolve("auto", code), "en", code)

    def test_empty_means_russian(self):
        for code in ("", None, "  "):
            self.assertEqual(i18n.resolve("auto", code), "ru")
        self.assertEqual(i18n.resolve("garbage", None), "ru")

    def test_norm(self):
        self.assertEqual(i18n.norm("EN"), "en")
        self.assertEqual(i18n.norm("ru-RU"), "ru")
        for bad in ("", None, "de", "auto", "x"):
            self.assertIsNone(i18n.norm(bad))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "h.db")
        self.store = Store(self.path)


class TestPrefs(StoreCase):
    def test_default_and_set(self):
        self.assertEqual(i18n.get_pref(self.store, 5), "auto")
        i18n.set_pref(self.store, 5, "en")
        self.assertEqual(i18n.get_pref(self.store, 5), "en")
        i18n.set_pref(self.store, 5, "auto")
        self.assertEqual(i18n.get_pref(self.store, 5), "auto")
        with self.assertRaises(ValueError):
            i18n.set_pref(self.store, 5, "de")

    def test_persists_across_reopen(self):
        i18n.set_pref(self.store, 5, "en")
        again = Store(self.path)
        self.assertEqual(i18n.get_pref(again, 5), "en")
        self.assertEqual(i18n.lang_of(again, 5, "ru"), "en")

    def test_table_shape(self):
        cols = [r["name"] for r in self.store.q("PRAGMA table_info(prefs)")]
        self.assertEqual(cols[:2], ["uid", "lang"])
        self.assertIn("updated_ts", cols)

    def test_lang_of_precedence(self):
        self.assertEqual(i18n.lang_of(self.store, 7), "ru")                     # nothing known
        self.assertEqual(i18n.lang_of(self.store, 7, "en"), "en")               # Telegram says en
        i18n.set_pref(self.store, 7, "ru")
        self.assertEqual(i18n.lang_of(self.store, 7, "en"), "ru")               # pref wins
        self.assertEqual(i18n.lang_of(self.store, None, "en"), "en")

    def test_remembered_code_is_a_fallback_and_never_overwrites_the_pref(self):
        i18n.remember_code(self.store, 8, "en")
        self.assertEqual(i18n.get_pref(self.store, 8), "auto")
        self.assertEqual(i18n.lang_of(self.store, 8), "en")
        i18n.set_pref(self.store, 8, "ru")
        i18n.remember_code(self.store, 8, "de")
        self.assertEqual(i18n.get_pref(self.store, 8), "ru")
        self.assertEqual(i18n.lang_of(self.store, 8), "ru")

    def test_members_row_is_the_last_fallback(self):
        mm.Members(self.store).grant(9, {"first_name": "A", "language_code": "en"}, 5)
        self.assertEqual(i18n.lang_of(self.store, 9), "en")
        i18n.set_pref(self.store, 9, "ru")
        self.assertEqual(i18n.lang_of(self.store, 9), "ru")


class TestCatalog(unittest.TestCase):
    def test_every_key_has_both_languages_with_the_same_placeholders(self):
        fmt = string.Formatter()
        for key, v in i18n.T.items():
            self.assertTrue(v["ru"] and v["en"], key)
            ph = lambda s: {f for _, f, _, _ in fmt.parse(s) if f} if "{" in s else set()      # noqa: E731
            if key != "dl.err.clip_obj":                         # literal braces, never formatted
                self.assertEqual(ph(v["ru"]), ph(v["en"]), key)

    def test_tr(self):
        self.assertEqual(i18n.tr("en", "common.back"), "« Back")
        self.assertEqual(i18n.tr("ru", "common.back"), "« Назад")
        self.assertEqual(i18n.tr("de", "common.back"), "« Назад")             # unknown language -> ru
        self.assertEqual(i18n.tr("en", "no.such.key"), "no.such.key")
        self.assertEqual(i18n.tr("en", "dl.err.clip_obj"), "Clip: {start, end} is expected.")

    def test_dates(self):
        ts = time.mktime((2026, 11, 12, 14, 5, 0, 0, 0, -1))
        self.assertEqual(i18n.fmt_date("ru", ts), "12 ноября")
        self.assertEqual(i18n.fmt_date("en", ts), "12 Nov 2026")
        self.assertEqual(i18n.fmt_when("ru", ts), "12 ноября в 14:05")
        self.assertEqual(i18n.fmt_when("en", ts), "12 Nov 2026, 14:05")


# ------------------------------------------------------------- pure helpers --
class TestLocalizedHelpers(unittest.TestCase):
    def test_link_names_and_groups(self):
        self.assertEqual(webapp.link_name("vless://u@h:1", "mh-1", 3, "en"), "Config 3")
        self.assertEqual(webapp.link_name("vless://u@h:1", "mh-1", 3), "Конфиг 3")
        self.assertEqual(webapp.link_name("tg://proxy?server=a&port=1&secret=b", "mh-1", 2, "en"), "MTProto proxy")
        _, groups = webapp.build_links(["vless://u@h:443?x=1#A", "tg://proxy?server=a&port=1&secret=b"],
                                       "mh-1", "d", lang="en")
        self.assertEqual([g["title"] for g in groups], ["VLESS and Shadowsocks", "Telegram proxy"])
        self.assertIn("Tap it", groups[1]["hint"])
        _, ru = webapp.build_links(["tg://proxy?server=a&port=1&secret=b"], "mh-1", "d")
        self.assertEqual(ru[0]["title"], "Прокси для Telegram")

    def test_awg_link_names(self):
        out = webapp.awg_links([{"name": "n", "url": "vpn://" + "A" * 8}], "./dl/x", "en")
        self.assertEqual([l["action"] for l in out][:2], ["copy", "download"])
        self.assertIn(".conf file", [l["name"] for l in out])

    def test_plural_inbounds(self):
        self.assertEqual(webapp.plural_inbounds(2), "2 инбаунда")
        self.assertEqual(webapp.plural_inbounds(1, "en"), "1 inbound")
        self.assertEqual(webapp.plural_inbounds(3, "en"), "3 inbounds")

    def test_dl_error_and_job_error(self):
        e = dlm.DlError(429, "limit", "dl.err.limit_hour", n=10)
        self.assertEqual(e.message, "Лимит: не больше 10 загрузок в час. Попробуй позже.")
        self.assertEqual(e.render("en"), "Limit: at most 10 downloads per hour. Try again later.")
        self.assertEqual(dlm.DlError(400, "x").render("en"), "x")
        self.assertEqual(i18n.localize_job_error("en", "файл не найден на диске"), "file not found on disk")
        self.assertEqual(i18n.localize_job_error("ru", "файл не найден на диске"), "файл не найден на диске")
        self.assertEqual(i18n.localize_job_error("en", "HTTP Error 403"), "HTTP Error 403")
        p = dlm.presets_payload("en")
        self.assertEqual([x["label"] for x in p["video"]][-1], "Best")
        self.assertIn("VK Video", p["sites"])
        self.assertEqual([x["label"] for x in dlm.presets_payload()["video"]][-1], "Лучшее")

    def test_health_report_in_english(self):
        f = [health.F("disk:disk", "crit", "health.disk", name="SSD", p="91", free="3.0"),
             health.F("dns", "crit", "health.dns_none")]
        self.assertEqual(f[0].text, "SSD заполнен на 91% (свободно 3.0 GB)")
        self.assertEqual(f[0].t("en"), "SSD is 91% full (3.0 GB free)")
        out = health.render(f, {"uptime": 90000, "running": 3, "total": 4, "stopped": ["x"]}, "en")
        self.assertIn("Problems: 2", out)
        self.assertIn("Uptime 1 d 1 h", out)
        self.assertIn("3 of 4 running", out)
        self.assertIn("Stopped manually: x", out)
        ru = health.render(f, {"uptime": 90000}, "ru")
        self.assertIn("Проблем: 2", ru)
        self.assertIn("Аптайм 1 д 1 ч", ru)
        plain = health.Finding("k", "warn", "готовый текст")
        self.assertEqual(plain.t("en"), "готовый текст")


# ------------------------------------------------------------------ web API --
class Web(tw.Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"CRYPTO_TG_TOKEN": ""})
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def h_user(uid, code="en"):
        return {"X-Tg-Init-Data": tw.init_data(tw.TOKEN, user_en(uid, code))}

    def lang_headers(self, uid, code="en", lang=None):
        h = self.h_user(uid, code)
        if lang:
            h["X-Lang"] = lang
        return h


class TestMeAndLang(Web):
    def test_me_follows_language_code(self):
        _, d = self.j("GET", "/api/me", headers=self.lang_headers(555, "en"))
        self.assertEqual((d["lang"], d["lang_pref"]), ("en", "auto"))
        self.assertEqual(d["services"][0]["description"][:8], "Personal")
        self.assertEqual(d["services"][1]["name"], "Messenger")
        _, d = self.j("GET", "/api/me", headers=self.lang_headers(555, "uk"))
        self.assertEqual((d["lang"], d["lang_pref"]), ("ru", "auto"))
        self.assertEqual(d["services"][0]["description"][:6], "Личный")

    def test_post_lang_roundtrip(self):
        h = self.lang_headers(555, "en")
        st, d = self.j("POST", "/api/lang", {"lang": "ru"}, h)
        self.assertEqual((st, d), (200, {"lang": "ru", "lang_pref": "ru"}))
        _, me = self.j("GET", "/api/me", headers=h)
        self.assertEqual((me["lang"], me["lang_pref"]), ("ru", "ru"))
        self.assertEqual(me["services"][1]["name"], "Мессенджер")
        st, d = self.j("POST", "/api/lang", {"lang": "auto"}, h)
        self.assertEqual((st, d), (200, {"lang": "en", "lang_pref": "auto"}))          # back to Telegram's
        st, d = self.j("POST", "/api/lang", {"lang": "en"}, self.lang_headers(556, "ru"))
        self.assertEqual((st, d["lang"], d["lang_pref"]), (200, "en", "en"))
        self.assertEqual(i18n.get_pref(self.store, 556), "en")

    def test_post_lang_rejects_garbage(self):
        for body in ({"lang": "de"}, {"lang": ""}, {"lang": 5}, {}, {"lang": None}):
            st, d = self.j("POST", "/api/lang", body, self.lang_headers(555, "en"))
            self.assertEqual((st, d["error"]), (400, "bad_request"), body)
        self.assertEqual(i18n.get_pref(self.store, 555), "auto")
        st, d = self.j("POST", "/api/lang", {"lang": "de"}, self.lang_headers(555, "ru"))
        self.assertEqual(d["message"], "Поле lang: auto, ru или en.")
        st, d = self.j("POST", "/api/lang", {"lang": "de"}, self.lang_headers(555, "ru", "en"))
        self.assertEqual(d["message"], "Field lang: auto, ru or en.")

    def test_post_lang_needs_auth_and_is_open_to_every_role(self):
        self.assertEqual(self.j("POST", "/api/lang", {"lang": "en"})[0], 401)
        self.make_member(200)
        self.assertEqual(self.j("POST", "/api/lang", {"lang": "en"}, self.tgh(200))[0], 200)       # member
        self.assertEqual(self.j("POST", "/api/lang", {"lang": "en"}, self.tgh(OWNER))[0], 200)     # owner (tg)
        self.assertEqual(self.j("POST", "/api/lang", {"lang": "en"}, self.tgh(555))[0], 200)       # stranger

    def test_browser_admin_uses_the_owners_preference(self):
        _, me = self.j("GET", "/api/me", headers=self.admin())
        self.assertEqual((me["role"], me["lang"], me["lang_pref"]), ("owner", "ru", "auto"))
        st, d = self.j("POST", "/api/lang", {"lang": "en"}, self.admin())
        self.assertEqual((st, d["lang"]), (200, "en"))
        self.assertEqual(i18n.get_pref(self.store, OWNER), "en")             # the same setting as in Telegram
        _, me = self.j("GET", "/api/me", headers=self.tgh(OWNER))
        self.assertEqual((me["lang"], me["lang_pref"]), ("en", "en"))
        _, ov = self.j("GET", "/api/admin/services", headers=self.admin())
        self.assertEqual(ov[1]["name"], "Messenger")

    def test_hook_refreshes_chat_commands(self):
        seen, done = [], threading.Event()
        self.h.lang_changed = lambda uid: (seen.append(uid), done.set())
        self.j("POST", "/api/lang", {"lang": "en"}, self.lang_headers(555, "ru"))
        self.assertTrue(done.wait(5))
        self.assertEqual(seen, [555])

    def test_broken_hook_does_not_fail_the_request(self):
        def boom(uid):
            raise RuntimeError("telegram down")
        self.h.lang_changed = boom
        self.assertEqual(self.j("POST", "/api/lang", {"lang": "en"}, self.lang_headers(555, "ru"))[0], 200)


class TestMessageLanguage(Web):
    def forbidden(self, headers):
        st, d = self.j("GET", "/api/admin/overview", headers=headers)
        self.assertEqual((st, d["error"]), (403, "forbidden"))
        return d["message"]

    def test_header_pref_language_code(self):
        self.assertEqual(self.forbidden(self.lang_headers(555, "ru")), "Недостаточно прав.")
        self.assertEqual(self.forbidden(self.lang_headers(555, "en")), "Not enough permissions.")      # language_code
        self.assertEqual(self.forbidden(self.lang_headers(555, "ru", "en")), "Not enough permissions.")  # header
        i18n.set_pref(self.store, 555, "en")
        self.assertEqual(self.forbidden(self.lang_headers(555, "ru")), "Not enough permissions.")      # pref
        self.assertEqual(self.forbidden(self.lang_headers(555, "ru", "ru")), "Недостаточно прав.")      # header wins
        self.assertEqual(self.forbidden(self.lang_headers(555, "ru", "de")), "Not enough permissions.")  # bad header ignored

    def test_unauthenticated_errors_follow_the_header(self):
        st, d = self.j("GET", "/api/me", headers={"X-Lang": "en"})
        self.assertEqual((st, d["message"]), (401, "Authorization required."))
        st, d = self.j("GET", "/api/me")
        self.assertEqual(d["message"], "Нужна авторизация.")
        st, d = self.j("GET", "/api/me", headers={"X-Tg-Init-Data": "junk", "X-Lang": "en"})
        self.assertEqual(d["message"], "Your session is invalid, reopen the app.")

    def test_explicit_messages_with_variables(self):
        h = self.lang_headers(OWNER, "ru", "en")
        st, d = self.j("POST", "/api/admin/grant", {"uid": 5, "days": 0}, h)
        self.assertEqual((st, d["message"]), (400, "Field days: a number from 1 to 3650 is expected."))
        st, d = self.j("POST", "/api/admin/grant", {"uid": 5, "days": 0}, self.lang_headers(OWNER, "ru"))
        self.assertEqual(d["message"], "Поле days: ожидается число 1..3650.")

    def test_redeem_result_message(self):
        st, d = self.j("POST", "/api/redeem", {"code": "MEOW-XXXX-XXXX"}, self.lang_headers(555, "en"))
        self.assertEqual(d["message"], "Code not found. Check that you typed it correctly.")
        st, d = self.j("POST", "/api/redeem", {"code": "MEOW-XXXX-XXXX"}, self.lang_headers(555, "ru"))
        self.assertEqual(d["message"], "Код не найден. Проверь, что он введён без ошибок.")
        code = self.h.members.new_code(30)
        _, d = self.j("POST", "/api/redeem", {"code": code}, self.lang_headers(556, "ru", "en"))
        self.assertEqual(d["message"], "Access granted.")

    def test_service_errors(self):
        self.make_member(200, ["matrix"])
        st, d = self.j("GET", "/api/vpn", headers=self.lang_headers(200, "en"))
        self.assertEqual((st, d["message"]), (403, "This service isn't available: no active subscription."))

    def test_vpn_groups_follow_the_language(self):
        self.make_member(200)
        self.h.members.set_vpn_sub_id(200, "abc123")
        _, d = self.j("GET", "/api/vpn", headers=self.lang_headers(200, "en"))
        self.assertEqual([l["name"] for l in d["links"]], ["My Config", "Config 2"])
        self.assertEqual(d["groups"][0]["title"], "VLESS and Shadowsocks")
        _, d = self.j("GET", "/api/vpn", headers=self.lang_headers(200, "ru"))
        self.assertEqual(d["groups"][0]["title"], "VLESS и Shadowsocks")

    def test_not_found_and_bad_json(self):
        st, d = self.j("GET", "/api/admin/members/999", headers=dict(self.admin(), **{"X-Lang": "en"}))
        self.assertEqual((st, d["message"]), (404, "Member not found."))
        st, r, p = self.req("POST", "/api/lang", raw="[1]", headers=dict(self.lang_headers(555, "en")))
        self.assertIn(b"must be an object", p)

    def test_unhandled_error_is_localized(self):
        with mock.patch.object(webapp.App, "api_me", side_effect=RuntimeError("x")):
            st, d = self.j("GET", "/api/me", headers=self.lang_headers(555, "en"))
        self.assertEqual((st, d["message"]), (500, "Internal error."))


class TestNotificationsInRecipientLanguage(Web):
    def test_member_gets_the_owners_action_in_their_language(self):
        self.make_member(200)
        i18n.set_pref(self.store, 200, "en")
        self.j("POST", "/api/admin/members/200", {"action": "suspend"}, self.admin())
        chat, text = self.h.sent[-1]
        self.assertEqual(chat, 200)
        self.assertEqual(text, "⏸ The owner has paused your access. Questions: @owner.")
        self.j("POST", "/api/admin/members/200", {"action": "extend", "days": 5}, self.admin())
        self.assertIn("has been extended until", self.h.sent[-1][1])
        self.assertRegex(self.h.sent[-1][1], r"\d{4}\.$")                # English dates carry the year
        i18n.set_pref(self.store, 200, "ru")
        self.j("POST", "/api/admin/members/200", {"action": "resume"}, self.admin())
        self.assertEqual(self.h.sent[-1][1], "▶️ Доступ снова открыт.")

    def test_language_code_is_used_without_a_preference(self):
        self.h.members.grant(201, {"first_name": "Eve", "language_code": "de"}, 30)
        self.j("POST", "/api/admin/members/201", {"action": "resume"}, self.admin())
        self.assertEqual(self.h.sent[-1][1], "▶️ Your access is open again.")

    def test_owner_notice_uses_the_owners_language(self):
        i18n.set_pref(self.store, OWNER, "en")
        code = self.h.members.new_code(30, ["vpn", "matrix"])
        self.j("POST", "/api/redeem", {"code": code}, self.lang_headers(555, "ru"))
        chat, text = self.h.sent[-1]
        self.assertEqual(chat, OWNER)
        self.assertIn("redeemed the code", text)
        self.assertIn("+30 d, VPN + Messenger", text)


class TestPages(Web):
    SUB = urllib.parse.quote("https://example.org:2096/sub/abc", safe="")

    def test_go_page_language(self):
        _, _, p = self.req("GET", f"/go/happ?u={self.SUB}&l=en")
        body = p.decode()
        self.assertIn("Open in Happ", body)
        self.assertIn("If the app didn&#x27;t open, install Happ", body)
        self.assertIn('<html lang="en">', body)
        _, _, p = self.req("GET", f"/go/happ?u={self.SUB}&l=ru")
        self.assertIn("Открыть в Happ", p.decode())
        _, _, p = self.req("GET", f"/go/happ?u={self.SUB}")
        self.assertIn("Открыть в Happ", p.decode())                          # default stays Russian
        _, _, p = self.req("GET", f"/go/happ?u={self.SUB}&l=zz")
        self.assertIn("Открыть в Happ", p.decode())

    def test_go_page_error_language(self):
        st, r, p = self.req("GET", "/go/happ?l=en")
        self.assertEqual(st, 400)
        self.assertIn(b"Invalid link.", p)

    def test_awg_open_page_follows_the_uid_preference_and_query(self):
        import links as signed
        self.make_member(200)
        self.h.xui.client_links = lambda email: [
            "vpn://" + __import__("base64").urlsafe_b64encode(b"[Interface]\nEndpoint = h:1\n").decode()]
        tok = signed.sign("awg_open", "awg", 200, ttl=600, store=self.store)[len("./dl/"):]
        _, _, p = self.req("GET", "/dl/" + tok)
        self.assertIn("Скопировать ключ", p.decode())
        i18n.set_pref(self.store, 200, "en")
        _, _, p = self.req("GET", "/dl/" + tok, headers={"User-Agent": "Mozilla/5.0 (Android 14)"})
        body = p.decode()
        self.assertIn("Copy the key", body)
        self.assertIn("Open in AmneziaVPN", body)
        self.assertIn('<html lang="en">', body)
        self.assertIn("Copied", body)
        _, _, p = self.req("GET", "/dl/" + tok + "?l=ru")
        self.assertIn("Скопировать ключ", p.decode())                        # ?l= overrides the pref


# --------------------------------------------------------------------- bots --
class BotCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, tb.ENV)
        p.start()
        self.addCleanup(p.stop)
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.h = tb.botmod.Helper(Store(os.path.join(d, "t.db")))
        self.h.bot = self.b = tb.FakeBot()
        self.h.bot_username = "TestBot"
        self.h.pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a))        # run background work inline
        self.mm = self.h.members

    @staticmethod
    def msg(uid, text, code="ru"):
        return {"message_id": 7, "from": user_en(uid, code), "chat": {"id": uid, "type": "private"}, "text": text}

    @staticmethod
    def cbq(uid, data, code="ru", mid=500):
        return {"id": f"q{uid}{mid}", "from": user_en(uid, code), "data": data,
                "message": {"message_id": mid, "chat": {"id": uid, "type": "private"}}}

    def member(self, uid=50, code="ru", services=None):
        self.mm.grant(uid, user_en(uid, code), 30, services)
        return uid

    @staticmethod
    def labels(kb):
        return [b["text"] for r in kb["keyboard"] for b in r]


class TestLangCommand(BotCase):
    def test_picker(self):
        uid = self.member(50)
        self.h.on_message(self.msg(uid, "/lang"))
        out = self.b.sent(uid)[-1]
        self.assertEqual(out[2], "🌐 Язык / Language")
        rows = out[3]["reply_markup"]["inline_keyboard"]
        self.assertEqual([b["callback_data"] for r in rows for b in r], ["lang:ru", "lang:en", "lang:auto"])
        self.assertEqual([b["text"] for r in rows for b in r], ["Русский", "English", "Авто (как в Telegram)"])

    def test_picker_in_english_has_the_english_auto_label(self):
        uid = self.member(50, "en")
        self.h.on_message(self.msg(uid, "/lang", "en"))
        rows = self.b.sent(uid)[-1][3]["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows[1][0]["text"], "Auto (Telegram)")

    def test_button_opens_the_picker_in_both_languages(self):
        uid = self.member(50)
        for label in ("🌐 Язык", "🌐 Language"):
            self.h.on_message(self.msg(uid, label))
            self.assertEqual(self.b.sent(uid)[-1][2], "🌐 Язык / Language")
        self.assertEqual(len(self.b.sent(uid)), 2)

    def test_callback_saves_and_resends_the_keyboard(self):
        uid = self.member(50)
        self.h.on_callback(self.cbq(uid, "lang:en"))
        self.assertEqual(i18n.get_pref(self.h.store, uid), "en")
        out = self.b.sent(uid)[-1]
        self.assertEqual(out[2], "Language: English")
        labels = self.labels(out[3]["reply_markup"])
        self.assertIn("🔐 VPN", labels)
        self.assertIn("🗓 Subscription", labels)
        self.assertIn("❓ Help", labels)
        self.assertIn("🌐 Language", labels)
        self.assertNotIn("🗓 Подписка", labels)
        self.assertEqual(self.b.answers()[-1][2], "Language: English")
        self.h.on_callback(self.cbq(uid, "lang:ru"))
        out = self.b.sent(uid)[-1]
        self.assertEqual(out[2], "Язык: русский")
        self.assertIn("🗓 Подписка", self.labels(out[3]["reply_markup"]))

    def test_auto_follows_telegram_again(self):
        uid = self.member(50, "en")
        self.h.on_callback(self.cbq(uid, "lang:ru", "en"))
        self.h.on_callback(self.cbq(uid, "lang:auto", "en"))
        self.assertEqual(i18n.get_pref(self.h.store, uid), "auto")
        self.assertEqual(self.b.sent(uid)[-1][2], "Language: auto (as in Telegram), now English")
        self.assertIn("🗓 Subscription", self.labels(self.b.sent(uid)[-1][3]["reply_markup"]))

    def test_bad_callback_value(self):
        uid = self.member(50)
        self.h.on_callback(self.cbq(uid, "lang:zz"))
        self.assertEqual(i18n.get_pref(self.h.store, uid), "auto")
        self.assertTrue(self.b.answers()[-1][3]["alert"])

    def test_stranger_can_switch_too(self):
        self.h.on_message(self.msg(77, "/lang", "en"))
        self.assertEqual(self.b.sent(77)[-1][2], "🌐 Язык / Language")
        self.h.on_callback(self.cbq(77, "lang:ru", "en"))
        self.assertEqual(i18n.get_pref(self.h.store, 77), "ru")
        self.assertEqual(self.b.sent(77)[-1][2], "Язык: русский")
        self.assertIsNone(self.b.sent(77)[-1][3]["reply_markup"])          # strangers have no reply keyboard
        self.h.on_message(self.msg(77, "hello", "en"))
        self.assertIn("закрытый сервис", self.b.sent(77)[-1][2])           # and the pref applies to the lock text

    def test_owner_keyboard_has_the_button_and_help_mentions_it(self):
        labels = self.labels(self.h.keyboard(tb.OWNER))
        self.assertIn("🌐 Язык", labels)
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        labels = self.labels(self.h.keyboard(tb.OWNER))
        self.assertIn("🌐 Language", labels)
        self.assertIn("🩺 Health", labels)
        self.assertIn("/lang", self.h.help_text("owner", "en"))
        self.assertIn("/lang", self.h.help_text("owner", "ru"))


class TestRoutingBothLabelSets(BotCase):
    def test_english_labels_route_for_an_english_member(self):
        uid = self.member(50, "en")
        self.h.on_message(self.msg(uid, "🗓 Subscription", "en"))
        out = self.b.sent(uid)[-1][2]
        self.assertIn("<b>Subscription</b>", out)
        self.assertIn("Status: 🟢 active until", out)
        self.h.on_message(self.msg(uid, "❓ Help", "en"))
        self.assertIn("an invite-only service", self.b.sent(uid)[-1][2])
        self.assertIn("Personal VPN", self.b.sent(uid)[-1][2])

    def test_old_russian_keyboard_still_works_after_switching(self):
        uid = self.member(50, "ru")
        i18n.set_pref(self.h.store, uid, "en")
        self.h.on_message(self.msg(uid, "🗓 Подписка"))                      # label from the old keyboard
        self.assertIn("<b>Subscription</b>", self.b.sent(uid)[-1][2])        # answered in the new language
        self.h.on_message(self.msg(uid, "❓ Помощь"))
        self.assertIn("invite-only", self.b.sent(uid)[-1][2])

    def test_old_english_label_with_a_russian_preference(self):
        uid = self.member(50, "en")
        i18n.set_pref(self.h.store, uid, "ru")
        self.h.on_message(self.msg(uid, "🗓 Subscription", "en"))
        self.assertIn("<b>Подписка</b>", self.b.sent(uid)[-1][2])

    def test_owner_buttons_in_english(self):
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        with mock.patch.object(tb.botmod.health, "full", return_value=([], {})) as full:
            self.h.on_message(self.msg(tb.OWNER, "🩺 Health", "en"))
        full.assert_called_once()
        self.assertIn("The server is fine", self.b.log[-1][2])
        self.h.on_message(self.msg(tb.OWNER, "🎟 Code", "en"))
        self.assertIn("<b>New code</b>", self.b.sent(tb.OWNER)[-1][2])
        self.h.on_message(self.msg(tb.OWNER, "👥 Members", "en"))
        self.assertIn("<b>Members</b>", self.b.sent(tb.OWNER)[-1][2])
        self.h.on_message(self.msg(tb.OWNER, "⚙️ Services", "en"))
        self.assertIn("with access", self.b.sent(tb.OWNER)[-1][2])

    def test_member_cannot_use_an_owner_button_in_either_language(self):
        uid = self.member(50)
        self.h.on_message(self.msg(uid, "🔑 Пароли"))
        self.assertIn("только владельцу", self.b.sent(uid)[-1][2])
        i18n.set_pref(self.h.store, uid, "en")
        self.h.on_message(self.msg(uid, "🔑 Passwords"))
        self.assertIn("for the owner only", self.b.sent(uid)[-1][2])
        self.h.on_message(self.msg(uid, "🔑 Пароли"))                       # the old keyboard still routes
        self.assertIn("for the owner only", self.b.sent(uid)[-1][2])

    def test_every_label_maps_to_one_key(self):
        keys = {i18n.tr(lg, "btn." + k): k for k in tb.botmod.BTN_KEYS for lg in i18n.LANGS}
        self.assertEqual(len({v for v in keys.values()}), len(tb.botmod.BTN_KEYS))
        for label, key in keys.items():
            self.assertEqual(tb.botmod.btn_key(label), key)
        self.assertIsNone(tb.botmod.btn_key("hello"))


class TestBotTextsInEnglish(BotCase):
    def test_stranger_lock_and_owner_notice(self):
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        self.h.on_message(self.msg(77, "hi", "en"))
        self.assertIn("invite-only service", self.b.sent(77)[0][2])
        self.assertIn("Contact @boss", self.b.sent(77)[0][2])
        note = self.b.sent(tb.OWNER)[0]
        self.assertIn("is writing to the bot", note[2])
        self.assertEqual([b["text"] for r in note[3]["reply_markup"]["inline_keyboard"] for b in r],
                         ["✅ Give 30 days of access", "Ignore"])

    def test_code_redemption_flow(self):
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        self.h.on_message(self.msg(78, "MEOW-AAAA-BBBB", "en"))
        self.assertEqual(self.b.sent(78)[-1][2], "❌ No such code. Check that you typed it correctly.")
        code = self.mm.new_code(30, ["vpn", "matrix"])
        self.h.on_message(self.msg(79, code, "en"))
        texts = "\n".join(e[2] for e in self.b.sent(79))
        self.assertIn("Welcome to MeowHub", texts)
        self.assertIn("Services: VPN, Messenger", texts)
        self.assertIn("Menu — use the buttons below.", texts)
        self.assertIn("redeemed the code", self.b.sent(tb.OWNER)[-1][2])
        self.assertIn("+30 d", self.b.sent(tb.OWNER)[-1][2])

    def test_paused_member_message(self):
        uid = self.member(50, "en")
        self.mm.suspend(uid)
        self.h.on_message(self.msg(uid, "hi", "en"))
        out = self.b.sent(uid)[-1][2]
        self.assertIn("Access paused", out)
        self.assertIn("the owner paused your access", out)

    def test_owner_actions_notify_the_member_in_their_language(self):
        uid = self.member(50, "en")
        self.h.on_callback(self.cbq(tb.OWNER, f"m:sus:{uid}"))
        self.assertEqual(self.b.sent(uid)[-1][2], "⏸ The owner has paused your MeowHub access. Contact @boss.")
        self.assertEqual(self.b.answers()[-1][2], "Приостановлен")           # the owner reads Russian
        i18n.set_pref(self.h.store, uid, "ru")
        self.h.on_callback(self.cbq(tb.OWNER, f"m:res:{uid}"))
        self.assertEqual(self.b.sent(uid)[-1][2], "▶️ Доступ к MeowHub восстановлен.")

    def test_owner_screens_follow_the_owners_pref(self):
        uid = self.member(50)
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        self.h.on_callback(self.cbq(tb.OWNER, f"m:open:{uid}"))
        card = [e for e in self.b.log if e[0] == "edit"][-1]
        self.assertIn("Status: 🟢 active", card[2])
        self.assertIn("VPN client: no", card[2])
        data = {b["callback_data"]: b["text"] for r in card[3]["reply_markup"]["inline_keyboard"]
                for b in r if "callback_data" in b}
        self.assertEqual(data[f"m:sus:{uid}"], "⏸ Pause")
        self.assertEqual(data["m:pg:0"], "« Back")
        self.h.on_message(self.msg(tb.OWNER, "/code 90 vpn", "en"))
        self.assertIn("Gives 90 d: VPN", self.b.sent(tb.OWNER)[-1][2])
        self.h.on_message(self.msg(tb.OWNER, "/code abc", "en"))
        self.assertIn("Usage: <code>/code [days]", self.b.sent(tb.OWNER)[-1][2])

    def test_vpn_and_matrix_screens(self):
        uid = self.member(50, "en")
        self.mm.set_vpn_sub_id(uid, "abc")
        self.h.on_message(self.msg(uid, "/vpn", "en"))
        self.assertIn("<b>Your VPN</b>", self.b.sent(uid)[-1][2])
        self.h.on_message(self.msg(uid, "/matrix", "en"))
        out = self.b.sent(uid)[-1]
        self.assertIn("No accounts yet.", out[2])
        self.assertEqual(out[3]["reply_markup"]["inline_keyboard"][0][0]["text"], "➕ Create account")
        uid2 = self.member(51, "en", services=["matrix"])
        self.h.on_message(self.msg(uid2, "/vpn", "en"))
        self.assertIn("isn't part of your subscription", self.b.sent(uid2)[-1][2])

    def test_downloader_handoff(self):
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        self.h.on_message(self.msg(tb.OWNER, "🎬 Download video", "en"))
        self.assertIn("Send me a link", self.b.sent(tb.OWNER)[-1][2])
        self.h.on_message(self.msg(tb.OWNER, "https://youtu.be/abcdefghijk", "en"))
        out = self.b.sent(tb.OWNER)[-1]
        self.assertIn("You can download it in MeowHub", out[2])
        self.assertEqual(out[3]["reply_markup"]["inline_keyboard"][0][0]["text"], "Open the downloader")

    def test_passwords(self):
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        self.h.store.vault_set("immich", "me", "secret")
        self.h.on_message(self.msg(tb.OWNER, "/pass", "en"))
        self.assertIn("Pick a service:", self.b.sent(tb.OWNER)[-1][2])
        with mock.patch.object(tb.botmod.threading, "Timer"):
            self.h.on_callback(self.cbq(tb.OWNER, "pw:immich"))
        out = self.b.sent(tb.OWNER)[-1][2]
        self.assertIn("Login: <code>me</code>", out)
        self.assertIn("will be deleted in", out)

    def test_callback_alerts(self):
        self.h.on_callback(self.cbq(77, "m:open:1"))
        self.assertEqual(self.b.answers()[-1][2], "Нет доступа")
        uid = self.member(50, "en")
        self.h.on_callback(self.cbq(uid, "m:open:1", "en"))
        self.assertEqual(self.b.answers()[-1][2], "Owner only")

    def test_tools_gate(self):
        uid = self.member(50, "en", services=["vpn"])
        self.h.on_message(self.msg(uid, "/health", "en"))
        self.assertEqual(self.b.sent(uid)[-1][2], "⛔ This feature isn't part of your subscription.")


class TestMonitorAlerts(BotCase):
    def test_alerts_and_resolution_use_the_owners_language(self):
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        mon = tb.botmod.Monitor(self.h)
        bad = [health.F("disk:disk", "crit", "health.disk", name="SSD", p="95", free="1.0")]
        mon.process(bad, ("disk:",))
        self.assertEqual(self.b.sent(tb.OWNER), [])                          # debounced: seen once
        mon.process(bad, ("disk:",))
        self.assertEqual(self.b.sent(tb.OWNER)[-1][2], "🔴 SSD is 95% full (1.0 GB free)")
        mon.process([health.F("disk:disk", "ok", "health.disk", name="SSD", p="50", free="9")], ("disk:",))
        self.assertEqual(self.b.sent(tb.OWNER)[-1][2], "✅ Resolved: SSD is 50% full (9 GB free)")
        i18n.set_pref(self.h.store, tb.OWNER, "ru")
        mon.process([health.F("ct:x", "event", "health.ct_restarted", name="x", rc=2)], ("ct:",))
        self.assertEqual(self.b.sent(tb.OWNER)[-1][2], "⚠️ контейнер x упал и был перезапущен (раз: 2)")


class TestCommandLists(BotCase):
    def test_default_scope_is_english_and_russian_codes_get_russian(self):
        self.h._commands()
        calls = [e for e in self.b.log if e[0] == "setMyCommands"]
        default = [e for e in calls if "language_code" not in e[3] and "scope" not in e[3]]
        self.assertEqual(len(default), 1)
        self.assertEqual({c["command"]: c["description"] for c in default[0][3]["commands"]}["help"], "Help")
        ru = {e[3]["language_code"]: e[3]["commands"] for e in calls if "language_code" in e[3]}
        self.assertEqual(set(ru), {"ru", "uk", "be", "kk"})
        self.assertEqual({c["command"]: c["description"] for c in ru["ru"]}["help"], "Помощь")
        self.assertIn("lang", [c["command"] for c in ru["ru"]])

    def test_owner_chat_list_uses_the_owners_language(self):
        self.h._commands()
        owner = [e for e in self.b.log if e[0] == "setMyCommands" and e[3].get("scope")][-1]
        self.assertEqual(owner[3]["scope"], {"type": "chat", "chat_id": tb.OWNER})
        self.assertEqual({c["command"]: c["description"] for c in owner[3]["commands"]}["members"], "Участники")
        i18n.set_pref(self.h.store, tb.OWNER, "en")
        self.b.log.clear()
        self.h._commands()
        owner = [e for e in self.b.log if e[0] == "setMyCommands" and e[3].get("scope")][-1]
        self.assertEqual({c["command"]: c["description"] for c in owner[3]["commands"]}["members"], "Members")

    def test_switching_refreshes_the_chat_list(self):
        uid = self.member(50)
        self.h.on_callback(self.cbq(uid, "lang:en"))
        sets = [e for e in self.b.log if e[0] == "setMyCommands" and e[3].get("scope")]
        self.assertEqual(sets[-1][3]["scope"], {"type": "chat", "chat_id": uid})
        self.assertEqual({c["command"]: c["description"] for c in sets[-1][3]["commands"]}["help"], "Help")
        self.assertNotIn("health", [c["command"] for c in sets[-1][3]["commands"]])   # not the owner's list
        self.h.on_callback(self.cbq(uid, "lang:auto"))
        self.assertEqual(self.b.log[-1][0] if self.b.log[-1][0] == "deleteMyCommands" else
                         [e[0] for e in self.b.log if e[0] == "deleteMyCommands"][-1], "deleteMyCommands")

    def test_owner_switching_gets_the_full_list_in_the_new_language(self):
        self.h.on_callback(self.cbq(tb.OWNER, "lang:en"))
        sets = [e for e in self.b.log if e[0] == "setMyCommands" and e[3].get("scope")]
        cmds = {c["command"]: c["description"] for c in sets[-1][3]["commands"]}
        self.assertEqual(cmds["health"], "Server status")
        self.assertIn("setpass", cmds)

    def test_web_hook_goes_through_the_right_bot(self):
        uid = self.member(50)
        i18n.set_pref(self.h.store, uid, "en")
        self.h.lang_changed(uid)
        sets = [e for e in self.b.log if e[0] == "setMyCommands" and e[3].get("scope")]
        self.assertEqual(sets[-1][3]["scope"]["chat_id"], uid)

    def test_telegram_errors_are_swallowed(self):
        uid = self.member(50)
        self.b.call = mock.Mock(side_effect=tb.botmod.TelegramError("blocked"))
        i18n.set_pref(self.h.store, uid, "en")
        self.h.refresh_commands(uid)                                        # must not raise


class TestTwoBots(unittest.TestCase):
    def setUp(self):
        import test_round2 as t2
        self.t2 = t2
        p = mock.patch.dict(os.environ, dict(tb.ENV, **t2.CLEAN_ENV))
        p.start()
        self.addCleanup(p.stop)
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.h = tb.botmod.Helper(Store(os.path.join(d, "t.db")))
        self.h.bot = self.hb = tb.FakeBot()
        self.hb.token = t2.TOKEN
        self.h.bot_username = "HelperBot"
        self.mb = tb.FakeBot()
        self.mb.token = t2.MTOKEN
        self.h.attach_member_bot(self.mb, "MemberBot")
        self.front = self.h.member_front
        self.h.pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a))

    def test_redirect_text_follows_language_code(self):
        self.h.on_message(BotCase.msg(77, "hi", "en"))
        self.assertEqual(self.hb.sent(77)[0][2], "🔒 This is a service bot. MeowHub lives in @MemberBot.")
        self.assertEqual(self.hb.sent(77)[0][3]["reply_markup"]["inline_keyboard"][0][0]["text"], "Open @MemberBot")

    def test_member_bot_lang_flow_and_commands(self):
        self.h.members.grant(50, user_en(50), 30)
        self.front.on_callback(BotCase.cbq(50, "lang:en"))
        out = self.mb.sent(50)[-1]
        self.assertEqual(out[2], "Language: English")
        self.assertIn("🗓 Subscription", [b["text"] for r in out[3]["reply_markup"]["keyboard"] for b in r])
        sets = [e for e in self.mb.log if e[0] == "setMyCommands" and e[3].get("scope")]
        self.assertEqual(sets[-1][3]["scope"]["chat_id"], 50)
        self.assertEqual(self.hb.sent(50), [])                              # nothing leaked to the ops bot

    def test_member_bot_default_commands_in_both_languages(self):
        self.front._commands()
        calls = [e for e in self.mb.log if e[0] == "setMyCommands"]
        self.assertEqual(len(calls), 1 + len(i18n.RU_CODES))
        self.assertEqual([c["command"] for c in calls[0][3]["commands"]],
                         ["start", "help", "vpn", "matrix", "sub", "lang"])


# ----------------------------------------------------------------- reminders --
class TestRemindersInRecipientLanguage(tmem.Base):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.rec = mm.Reconciler(self.m, None, None,
                                 lambda chat, text, markup=None: self.sent.append((chat, text, markup)),
                                 lambda: 1000)

    def member(self, uid, code):
        self.m.grant(uid, {"first_name": "Bob", "language_code": code}, 30)

    def test_24h_reminder_in_english(self):
        self.member(7, "en")
        self.m.set_expiry(7, int(time.time()) + 3600)
        self.rec.reminders()
        chat, text, _ = self.sent[0]
        self.assertEqual(chat, 7)
        self.assertIn("ends in 24 hours", text)
        self.assertRegex(text, r"\d{1,2} [A-Z][a-z]{2} \d{4}, \d\d:\d\d")

    def test_3_day_and_expired_in_english(self):
        self.member(7, "de")
        self.m.set_expiry(7, int(time.time()) + 2 * DAY + 100)
        self.rec.reminders()
        self.assertIn("ends in 3 days (until", self.sent[0][1])
        self.m.set_expiry(7, int(time.time()) - 10)
        with mock.patch.dict(os.environ, {"OWNER_CONTACT": "@boss"}):
            self.rec.reminders()
        self.assertIn("Your MeowHub subscription has ended", self.sent[1][1])
        self.assertIn("@boss", self.sent[1][1])

    def test_no_contact_configured_says_the_owner(self):
        import i18n
        with mock.patch.dict(os.environ, {"OWNER_CONTACT": ""}):
            self.assertIn("владельцу", i18n.tr("ru", "vpn.no_access", c=""))
            self.assertIn("the owner", i18n.tr("en", "vpn.no_access", c=""))
            self.assertIn("у владельца", i18n.tr("ru", "n.closed", c=""))
            self.assertNotIn("{", i18n.tr("en", "help.member_q", c=""))

    def test_russian_stays_exactly_as_before(self):
        self.member(8, "ru")
        self.m.set_expiry(8, int(time.time()) + 2 * DAY + 100)
        self.rec.reminders()
        self.assertRegex(self.sent[0][1], r"^⏳ Подписка MeowHub закончится через 3 дня \(до \d+ [а-я]+\)\. "
                                          r"Чтобы продлить, пришли новый код\.$")

    def test_pref_beats_the_members_language_code(self):
        self.member(7, "en")
        i18n.set_pref(self.store, 7, "ru")
        self.m.set_expiry(7, int(time.time()) + 3600)
        self.rec.reminders()
        self.assertIn("закончится через 24 часа", self.sent[0][1])

    def test_owner_notice_follows_the_owners_pref(self):
        self.member(7, "ru")
        i18n.set_pref(self.store, 1000, "en")
        self.m.set_expiry(7, int(time.time()) - 10)
        self.rec.reminders()
        chat, text, markup = self.sent[-1]
        self.assertEqual(chat, 1000)
        self.assertIn("subscription expired", text)
        self.assertEqual([b["text"] for b in markup["inline_keyboard"][0]], ["➕ 30 days", "👤 Open"])

    def test_new_inbound_notice_for_an_english_owner(self):
        self.m.set_member_inbounds([1])
        self.m.set_known_inbounds({1})
        xui = tmem.FakeXUI([{"id": 1, "remark": "Speed", "protocol": "vless", "port": 443, "enable": True},
                            {"id": 6, "remark": "New", "protocol": "trojan", "port": 8443, "enable": True}])
        self.rec.xui = xui
        i18n.set_pref(self.store, 1000, "en")
        self.rec.detect_new_inbounds()
        chat, text, markup = self.sent[-1]
        self.assertIn("New inbound in 3x-ui", text)
        self.assertEqual([b["text"] for b in markup["inline_keyboard"][0]], ["✅ Share", "Skip"])


if __name__ == "__main__":
    unittest.main()
