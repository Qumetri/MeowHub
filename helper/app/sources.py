"""Links that yt-dlp (inside MeTube) cannot take as they are, turned into ones it can.

Most sites need nothing: X/Twitter, TikTok, Twitch, Bluesky, Dailymotion, Facebook,
OK, Coub, Streamable, Pinterest, SoundCloud... go straight to MeTube (checked
2026-11-14 with yt-dlp 2026.11.04).

Reddit is the exception: from this IP its JSON API answers 403 and yt-dlp's Reddit
extractor asks for an account. The post's official RSS feed still answers, and it
names the post's media: a v.redd.it id (Reddit's own video, fetched through its
public DASH playlist; the HLS one has no m4a audio, so MeTube's mp4 format filter
finds nothing) or the external link the post points at (YouTube, Streamable,
Imgur...), which MeTube then downloads like any other link. We identify ourselves
with our own User-Agent; no browser is imitated and no account is used. Reddit
allows this IP about one feed request every 30-60 s, hence the cache and the
worker-side retry in downloads.py.

resolve(url) -> Source(url=what MeTube gets, title=None|str, src=the link the user
gave, prefix=MeTube file-name prefix) or None when the link needs nothing. Raises
SourceError(dl.job.* key): final for a post without a video, with `retry_in` when
Reddit is rate limiting us. downloads.Downloads calls it from its worker, not from
the request, so a rate limit only delays the job.
"""
import html
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

UA = "meowhub-downloader/1.0 (+self-hosted helper bot)"
TIMEOUT = 8
REDDIT = {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "m.reddit.com",
          "np.reddit.com", "sh.reddit.com"}
POST_RE = re.compile(r"/comments/([a-z0-9]{4,12})(?:/|$)", re.I)
VREDD_RE = re.compile(r"https?://v\.redd\.it/([a-z0-9]{6,20})", re.I)
ID_RE = re.compile(r"[a-z0-9]{4,12}\Z", re.I)
# hosts whose links are images or Reddit itself: never "the video" of a post
NOT_MEDIA = ("reddit.com", "redd.it", "redditmedia.com", "redditstatic.com")


class SourceError(Exception):
    """`key` is an i18n dl.job.* key; `retry_in` (seconds) is set when Reddit only said "not now"
    (rate limit, network): the job then stays queued and is tried again."""

    def __init__(self, key, retry_in=None):
        super().__init__(key)
        self.key, self.retry_in = key, retry_in


@dataclass
class Source:
    url: str
    title: str | None
    src: str
    prefix: str = ""


def _host(u):
    try:
        return (urlsplit(u).hostname or "").lower()
    except ValueError:
        return ""


def is_reddit(u):
    h = _host(u)
    return h in REDDIT or h in ("redd.it", "www.redd.it", "v.redd.it")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):           # we read Location ourselves
        return None


def _location(url):
    """One redirect hop (Reddit share links /r/x/s/<code>, redd.it/<id>) -> target or None."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.headers.get("Location") if 300 <= e.code < 400 else None
    except (urllib.error.URLError, OSError):
        return None


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/atom+xml"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(2_000_000).decode("utf-8", "replace")


def reddit_post_id(url, location=_location):
    """Post id from any Reddit link shape, following at most 3 redirects (share links)."""
    for _ in range(4):
        h, path = _host(url), urlsplit(url).path
        if h in ("redd.it", "www.redd.it"):
            seg = path.strip("/").split("/")[0]
            return seg if ID_RE.match(seg) else None
        m = POST_RE.search(path)
        if m:
            return m.group(1)
        if h not in REDDIT or "/s/" not in path:
            return None
        nxt = location(url)
        if not nxt:
            return None
        url = nxt if nxt.startswith("http") else "https://www.reddit.com" + nxt
    return None


def _first_entry(feed):
    m = re.search(r"<entry>(.*?)</entry>", feed, re.S)
    return m.group(1) if m else ""


def reddit(url, fetch=_get, location=_location):
    """Source for a Reddit link, None when it is not a post link we understand."""
    if _host(url) == "v.redd.it":
        m = VREDD_RE.match(url)
        if not m:
            return None
        vid = m.group(1)
        return Source(f"https://v.redd.it/{vid}/DASHPlaylist.mpd", None, url, f"reddit-{vid}")
    pid = reddit_post_id(url, location)
    if not pid:
        return None
    try:
        entry = _first_entry(fetch(f"https://www.reddit.com/comments/{pid}/.rss"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SourceError("dl.job.reddit_novideo") from None
        try:                                          # 429: Reddit allows ~1 feed a minute from here
            wait = int(float(e.headers.get("x-ratelimit-reset") or 0))
        except ValueError:
            wait = 0
        raise SourceError("dl.job.reddit_down", retry_in=min(max(wait, 15), 120)) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise SourceError("dl.job.reddit_down", retry_in=30) from None
    if not entry:
        raise SourceError("dl.job.reddit_novideo")
    t = re.search(r"<title>(.*?)</title>", entry, re.S)
    title = html.unescape(html.unescape(t.group(1))).strip()[:200] if t else None
    content = html.unescape(re.search(r"<content[^>]*>(.*?)</content>", entry, re.S).group(1)) \
        if "<content" in entry else ""
    m = VREDD_RE.search(content)
    if m:
        return Source(f"https://v.redd.it/{m.group(1)}/DASHPlaylist.mpd", title or None, url, f"reddit-{pid}")
    # a link post: "[link]" points at the external page (YouTube, Streamable, Imgur...)
    for href, text in re.findall(r'<a href="([^"]+)">\s*([^<]*?)\s*</a>', content):
        if text == "[link]":
            href = html.unescape(href)
            h = _host(href)
            if href.startswith(("http://", "https://")) and h and not any(
                    h == d or h.endswith("." + d) for d in NOT_MEDIA):
                return Source(href, None, url)
            break
    raise SourceError("dl.job.reddit_novideo")


_cache = {}                     # link -> (Source | SourceError, ts): Reddit's budget is tiny
CACHE_S = 3600


def resolve(url, fetch=_get, location=_location, now=time.time):
    """Source for `url`, None when MeTube can take it as it is. Raises SourceError."""
    if not is_reddit(url):
        return None
    hit = _cache.get(url)
    if hit and now() - hit[1] < CACHE_S:
        if isinstance(hit[0], SourceError):
            raise hit[0]
        return hit[0]
    try:
        out = reddit(url, fetch, location)
    except SourceError as e:
        if e.retry_in is None:                      # a final answer ("no video") is worth keeping
            _cache[url] = (e, now())
        raise
    _cache[url] = (out, now())
    if len(_cache) > 500:
        for k, (_v, ts) in list(_cache.items()):
            if now() - ts >= CACHE_S:
                del _cache[k]
    return out
