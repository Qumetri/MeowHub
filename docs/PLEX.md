# Plex libraries through Nextcloud

Drop a film into the **Plex** folder in Nextcloud and it shows up in Plex a few
seconds later, with poster, summary, cast and ratings. There's nothing to
import by hand.

```
PLEX_MEDIA (default /srv/media/plex)        Nextcloud: /Plex
├── Movies/      → Plex library "Movies"    (Plex Movie agent)
├── TV Shows/    → Plex library "TV Shows"  (Plex TV Series agent)
└── Anime/       → Plex library "Anime"     (Plex TV Series agent)
```

Plex runs on the host (the official package), not in this compose project. The
folder is the only thing the two share.

## Setup

`bootstrap.sh` creates the folders and sets their ownership. Then run once,
after Nextcloud is up:

```bash
scripts/plex-library.sh
```

It does two things, and checks each one before changing anything:

1. In Nextcloud it enables the **External storage** app and mounts
   `/mnt/plex` as `/Plex` for the `admin` group. To add more people, put them
   in that group, or add a group under *Settings → External storage*.
2. In Plex it creates the three libraries and turns on **scan when files
   change** plus an hourly rescan.

Plex accepts API calls from localhost without a token only while it's
**unclaimed**. On a server that's already signed in, pass the token:
`PLEX_TOKEN=… scripts/plex-library.sh`. Either way, sign the server in to your
plex.tv account afterwards (open `http://<server-ip>:32400/web` from the LAN),
or the Plex apps outside your network won't see it.

## Naming — this is what gets you the metadata

Plex matches on **folder and file names**. Get these right and the metadata is
fetched by itself:

| Library | Layout |
|---|---|
| Movies | `Movies/Interstellar (2014)/Interstellar (2014).mkv` |
| TV Shows | `TV Shows/Severance (2022)/Season 01/Severance - S01E01.mkv` |
| Anime | `Anime/Frieren Beyond Journey's End (2023)/Season 01/Frieren - S01E01.mkv` |

- **Put the year in brackets.** It separates remakes and same-name shows.
- **Specials and OVAs** go in `Season 00` as `S00E01`, `S00E02`, and so on.
- **Subtitles** sit next to the video under the same name, ending in a language
  code: `Interstellar (2014).en.srt`.
- **Release names** like `Interstellar.2014.2160p.WEB-DL.x265-GRP.mkv` usually
  match anyway. If one doesn't, rename it, or use *Fix Match…* in Plex.

## Anime

Anime gets its **own library** so it doesn't mix into TV Shows. It uses the same
built-in Plex TV agent (TMDB/TVDB data), which handles most series well,
including Japanese and English titles, episode names and artwork. The weak spots:

- **Absolute episode numbers** (`One Piece - 1071.mkv`) don't line up with
  seasons. Either rename to `SxxEyy`, or set the show to
  *Edit → Advanced → Episode ordering → TheTVDB (Absolute)*.
- **Season splits** differ between TMDB and TVDB, especially for cours and split
  seasons. If episodes land in the wrong season, switch the show's episode
  ordering to the other source.
- **Films from a series** go in the Movies library, not under the show.

The old HAMA/AniDB agent was built on Plex's legacy plugin system, which Plex
has retired. The built-in agent is the supported path.

## Behaviour worth knowing

- **Full scan on change, not partial.** With partial scans, a brand-new
  `Show/Season 01/` folder sometimes finished scanning without the show being
  added. A full scan of these libraries takes about a second, so the script
  turns partial scans off.
- **Plex won't empty a library whose folder is suddenly empty.** It assumes the
  disk is unmounted and keeps the entries. Removing the last item leaves it
  listed until you delete it in Plex (*… → Delete*).
- **Moving a file from your own Nextcloud files into Plex is a copy plus a
  delete**, because they're different storages. Big files take as long as a
  copy on the same disk, and the progress bar shows it.
- **Uploads** go through Nextcloud's chunked uploader. The limit is
  `NEXTCLOUD_UPLOAD_LIMIT` (16G by default); the desktop client has no practical
  limit.
- **Permissions:** the files are owned by `www-data` (Nextcloud writes them)
  with group `plex`, and the setgid bit keeps the group on new folders. Plex
  only needs to read them.
