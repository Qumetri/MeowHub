# Downloader for several users: research (2026-10-08)

> Research report, dated 2026-10-08. Kept permanently; the "What we did" section at the end records decisions.

**[live]** = checked on this server (GET/HEAD requests or `yt-dlp -s` simulate runs; nothing was queued). **[src]** = read in the running container's source (`/app/app`, v2026.07.18). **[doc]** = docs or GitHub.

## 1. MeTube API [live + src]

Every route sits under `URL_PREFIX`. On loopback, `/history` returns 404 and `/<METUBE_PATH>/history` returns 200.

| Route | Notes |
|---|---|
| POST `add` | returns `{"status":"ok"}`, `{"status":"ok","msg":"Already in queue: …"}` or `{"status":"error","msg":…}` |
| POST `delete` | `{"ids":[<item url>],"where":"queue"\|"done"}`. **ids are item URLs.** `queue` cancels; `done` clears, and with `DELETE_FILE_ON_TRASHCAN=true` it also **deletes the file, chapter files and subtitle files** |
| POST `start` / `cancel-add` | start items added with `auto_start:false` / stop a playlist expansion |
| GET `history` | `{"queue","pending","done"}`. Items carry `url,title,status,msg,percent,speed,eta,size,filename,folder,timestamp(ns),download_type,format,quality,clip_*,chapter_files,subtitle_files` |
| GET `version` | `{"yt-dlp":"2026.07.04","version":"2026.07.18"}` |
| GET `presets`; `subscribe`, `subscriptions/*`; `upload-cookies`, `delete-cookies`, `cookie-status` | cookies are **one global jar** |
| GET `download/<rel>`, `audio_download/<rel>` | aiohttp static. Range is supported; **no Content-Disposition, no CORS header** [live] |
| socket.io | `all, added, updated, completed, canceled, cleared, configuration, custom_dirs` (push progress). Polling `/history` also works |

```json
{"url":"https://youtu.be/X","download_type":"video","format":"mp4","quality":"1080","codec":"h264",
 "folder":"u/123456","playlist_item_limit":10,"auto_start":true,"clip_start":90,"clip_end":150,
 "subtitle_language":"ru","subtitle_mode":"prefer_manual","split_by_chapters":false}
```

- **video:** `format` is `any`/`mp4`/`ios`; `quality` is `best`, `2160`…`240` or `worst`; `codec` is `auto`/`h264`/`h265`/`av1`/`vp9`.
- **audio:** `format` is `m4a`/`mp3`/`opus`/`wav`/`flac`. `quality` is `best`; mp3 also takes `320`/`192`/`128`, m4a `192`/`128`. Cover art and tags are embedded automatically (not for wav).
- **captions:** `srt`/`vtt`/`txt`/`ttml`/`sbv`/`scc`/`dfxp`, as a separate file only.
- **thumbnail:** `jpg`.
- `clip_start`/`clip_end` (seconds; `?t=` is honoured) become yt-dlp `download_ranges`.
- `folder` needs `CUSTOM_DIRS`; it is created on demand and confined with realpath. `custom_name_prefix` rejects `..` and a leading `/`.
- `ytdl_options_overrides` is refused unless `ALLOW_YTDL_OPTIONS_OVERRIDES=true`. Keep it off.
- `MAX_CONCURRENT_DOWNLOADS` defaults to 3; the playlist limit defaults to 0, which is unlimited.

**Multi-user gotcha [src]:** the queue and history are **keyed by URL**. If a second user sends a URL that is still queued, they get `"Already in queue"` and no item, even with a different format. A re-add of a finished URL *replaces* the earlier `done` record. The bot must dedupe per URL and fan out, or serialize.

## 2. Versions

- **MeTube:** we run 2026.07.18; the latest is **2026.09.29** [doc]. What matters in between:
  - 08.18: SponsorBlock toggle, plus a fix so downloads can reach the bundled PO-token provider. Releases **07.21–08.17 broke it** (#1064).
  - 08.20: yt-dlp **2026.08.19**.
  - 08.17: `DEFAULT_FOLDER`.
  - 09.26: "Auto" audio format.
  - 09.28: an "ask" mode for trash-deletes.
  - 09.25: a **CSRF guard**. Browser POSTs from foreign origins are refused unless listed in `CORS_ALLOWED_ORIGINS`; header-less clients like the bot's urllib still pass. A Mini App must therefore go through the bot backend, not call MeTube from the browser.
- **yt-dlp:** latest stable is **2026.08.19**, nightly **2026.09.27** [doc]. 2026.08.19 dropped `android_vr` from the default clients. Open YouTube issues: #17666 (mweb/web_embedded SABR-only in some sessions) and #17647 (403s).
- **Our 2026.07.04 still works [live]:** it got 1080p (`399+258`) through `android_vr`, with a GVS PO token from the bundled **bgutil-pot 0.8.1** and **deno 2.9.3**. The last real download, on 09-30, succeeded. **No cookies are needed from this residential IP today.** yt-dlp does warn that it is more than 90 days old.
- **Auto-update:** `YTDL_NIGHTLY_UPDATE_TIME=HH:MM` pip-upgrades yt-dlp to *nightly* at startup and daily. It needs a root-started container; ours qualifies [src: entrypoint].

**Recommendation:**
- Pin a dated tag (`ghcr.io/alexta69/metube:2026.09.29`) instead of `:latest`, and never use 07.21–08.17.
- After the bump, run `docker exec -u 1000 metube python3 -m yt_dlp -s -F <url>` and confirm 1080p formats and a PO-token line.
- Consider `YTDL_NIGHTLY_UPDATE_TIME` for YouTube breakages. Recreating the container reverts to the image's yt-dlp.

## 3. Feature feasibility

| Feature | Supported? | How |
|---|---|---|
| Quality presets | yes | `quality`. **Add `codec:"h264"` or `format:"ios"`**: "mp4 1080" picked AV1 (399) [live], which Telegram and older iPhones handle poorly |
| Audio + cover | yes | `download_type:"audio"`; the cover is embedded |
| Playlist with cap | yes | `playlist_item_limit`; the bot must enforce it (the default is unlimited) |
| Subtitles | separate file | `captions` + `subtitle_language`. Embedding needs a `YTDL_OPTIONS_PRESETS` entry (`FFmpegEmbedSubtitle`) |
| Trim range | yes | `clip_start`/`clip_end` |
| Thumbnail / chapters | yes | `thumbnail`; `split_by_chapters` |
| SponsorBlock | after the bump | or now via a preset (`SponsorBlock`+`ModifyChapters`) |
| RuTube | yes [live] | 1080p, no login |
| VK Video | yes [live] | 720p public video, no login |
| TikTok | **fails [live]** | "Unexpected response" on three extractor test URLs (RU restrictions or the stale yt-dlp; retest after the bump) |
| Instagram | **needs cookies [live]** | the global cookie jar would mean one shared account; skip |

## 4. Telegram delivery [doc]

- **Cloud Bot API (still true in 10.3, 2026-08-24):** uploads up to **50 MB**, sending by URL up to 20 MB, getFile up to 20 MB.
- **`sendVideo`** has `supports_streaming`, `thumbnail`, `cover` and `start_timestamp`. Send mp4/h264 with streaming on.
- **`sendAudio`** `thumbnail`: JPEG, under 200 kB, at most 320×320. The embedded cover is not reused, so the bot has to send it separately.
- **Local Bot API server** (`tdlib/telegram-bot-api --local`): **2000 MB** uploads, `file://` paths, unlimited getFile. Costs:
  - an `api_id`/`api_hash` from my.telegram.org;
  - one more always-on container whose working directory belongs on the HDD;
  - a `logOut` to migrate the bot to it, and the same procedure to move back;
  - a C++ build or a third-party image.
- **Mini App `WebApp.downloadFile({url,file_name},cb)`:** Bot API **8.0+** (Nov 2024). It shows a native confirm popup. Requirements:
  - the URL must be **HTTPS**;
  - Telegram asks for `Content-Disposition: attachment; filename=…` and `Access-Control-Allow-Origin: https://web.telegram.org` ("especially on web platforms"). MeTube sends neither [live].
  - The docs give no platform matrix, so **iOS is unverified**. Gate it with `isVersionAtLeast('8.0')` and fall back to `openLink`.
- **Big files:** the bot serves `/dl/<token>` itself. Each token is HMAC-signed over (path, user, expiry), single-use, valid about 1 h, and served with the headers above. Never hand out raw `/<METUBE_PATH>/download/…` links.

## 5. Multi-user risks

1. **The secret path is the only credential [live].** Over the public domain, `/<METUBE_PATH>/` exposes the UI, `add`, `delete` and `upload-cookies`. With `DOWNLOAD_DIRS_INDEXABLE=true`, `/download/` and `/download/keep/` are **browsable directory listings** (200); dotfiles under `.metube/` return 404. Every tap-to-save link the bot sends today leaks this path. **Fix:**
   - set `INDEXABLE=false`;
   - add basic_auth to the route, or remove it (the bot uses `metube:8081` internally);
   - serve files through signed links.
2. **Isolation:** prefer `folder:"u/<tg_id>"` over name prefixes. The janitor's `-maxdepth 1` would **never purge user subfolders**. Switch it to `find /downloads -mindepth 1 -path '*/keep' -prune -o -type f -mmin +TTL -delete` plus an empty-dir sweep.
3. **Global state:** history, cookies and URL keys are shared, so the bot keeps per-user records of who asked for which URL and when.
4. **Concurrency:** keep `MAX_CONCURRENT_DOWNLOADS` at 2–3 (the server is on Wi-Fi), plus per-user quotas such as 2 active and 5 per hour.
5. **Disk:** the HDD had ample free space [live]. Before `add`, refuse when free space is under 50 GB or the user is over quota. Pre-check size with `-s --print filesize_approx`; 10 min at 1080p came to 155 MB [live]. A global cap is possible with `YTDL_OPTIONS {"max_filesize":…}`.
6. **SSRF:** keep MeTube's `url_guard`; never set `ALLOW_PRIVATE_ADDRESSES`.

## Sources

- Container source `/app/app/{main,ytdl,dl_formats}.py`, `/app/docker-entrypoint.sh`
- https://github.com/alexta69/metube/releases, issues #1064 and #1085, GHSA-cxj8-27g9-669f
- https://github.com/yt-dlp/yt-dlp/releases/tag/2026.08.19, https://github.com/yt-dlp/yt-dlp-nightly-builds/releases, yt-dlp issues #17666, #17647, #12482
- https://core.telegram.org/bots/api, https://core.telegram.org/bots/webapps, https://github.com/tdlib/telegram-bot-api, https://telegram.org/blog/fullscreen-miniapps-and-more

## What we did (2026-10-08)

- MeTube pinned to `ghcr.io/alexta69/metube:2026.09.29` (no longer `:latest`; 07.21-08.17 broke the PO-token helper).
- `DOWNLOAD_DIRS_INDEXABLE=false`, `MAX_CONCURRENT_DOWNLOADS=2`, `YTDL_NIGHTLY_UPDATE_TIME=04:30` (yt-dlp nightly; it was 2026.09.27 right after the bump).
- The public `/{METUBE_PATH}/` route is now behind basic_auth `botadmin` (same password as the Bots page, `BOT_ADMIN_PASSWORD` in `.env`).
- The janitor now deletes files older than 30 min by **ctime** (yt-dlp may set mtime to the upload date) everywhere except `keep/` and `.metube/`, every 2 min, and removes empty `u*` user folders.
- Owner service toggles exist; the YouTube downloader service for users is still being built (not documented here yet).
