# VOD Announcer

A fully cloud-based Twitch VOD automation that runs on **GitHub Actions' free
tier**. Your computer stays **offline** - GitHub's servers do everything on a
schedule: discover new stream recordings, download them, rename them to the
`BS_` convention, upload them to a file host, and post the download links to
a Discord channel via webhook.

```
   (every 15 min, on GitHub's servers)
   streamrecorder.io  ──▶  discover recordings
        │
        ▼
   download (verified, sha256)
        │
        ▼
   rename  BS_YYYY-MM-DD_Title[_P01]      ← strict, validated
        │
        ▼
   upload to savedly.net  ──▶  link
        │
        ▼
   Discord webhook announcement
        │
        ▼
   state/state.json committed back to the repo  (memory between runs)
```

## What works today (verified against the live services)

| Stage | Status | How |
|---|---|---|
| Discovery | ✅ live-tested | Parses the public `streamrecorder.io/twitch/<channel>` page (latest recording panel + "Past Twitch Streams" carousel: recording id, title, date, duration) |
| Download (latest recording) | ✅ works anonymously | Signed `srcdn.net` MP4 URL extracted from the page. **Size-capped preview** for anonymous visitors (observed 200 MB) |
| Download (full recordings) | 🔑 needs your cookie | Set the `STREAMRECORDER_COOKIE` secret (free streamrecorder.io account). The app prefers full `/ddl/` URLs when a session cookie is present |
| Upload | ✅ live-tested | savedly.net API, fully implemented: direct upload (≤ 90 MB) **and** the chunked init/part/complete protocol used by their website for bigger files (protocol reverse-engineered from their own uploader and validated with a 95 MB test file) |
| Discord announcement | ✅ | Standard webhook POST; exact `October 8` date and `Xh Xm Xs` duration formats |
| State / recovery | ✅ unit-tested | Atomic `state/state.json`, committed to the repo after every run; interrupt-safe, duplicate-safe |

**Important - savedly retention:** savedly.net automatically deletes files
that receive **no views for 7 days**. Every announcement therefore carries a
"download soon" warning. Opening the link keeps the file alive.

## Business rules implemented

- **Session grouping (60-minute rule):** recordings of one stream day form a
  session (multi-part VODs). The announcement timer resets whenever a new
  recording joins; a session is announced once, after 60 minutes of quiet or
  as soon as a newer day appears.
- **Naming (hard rule):** `BS_YYYY-MM-DD_Title`, multi-part `_P01`, `_P02`… —
  download → verify → date/title → rename → **validate** → upload. Uploading
  an un-renamed file is structurally impossible.
- **Announcement format:** date `October 8`, duration `2h 22m 4s` (always
  three units), single-link or numbered multi-part list, 7-day-retention
  warning, contact `@bobmasterbillie`, messages split under Discord's limit.
- **Duplicate safety:** no double downloads, uploads or announcements; failed
  parts retry next run (max 5 attempts); recordings with no public download
  URL are marked `blocked` (visible in logs, no retry storm).

## Project layout

```
app/
  main.py               orchestration (one run = one GitHub Actions job)
  config.py             env-driven configuration
  sources/streamrecorder.py   discovery + download URLs (live-verified parser)
  downloader.py         streaming download, sha256, HTML-guard, retries
  renamer.py            BS naming + validation
  savedly.py            savedly.net client (direct + chunked protocol)
  discord_notify.py     webhook announcements, message splitting
  session_manager.py    60-minute session grouping + scheduling
  state_manager.py      atomic state.json persistence
  timeutil.py           IST helpers, `October 8`, `Xh Xm Xs` formatters
  logsetup.py           logging with secret redaction
tests/                  83 tests (all external services mocked)
scripts/
  commit_state.sh       git state commit/push with retry (used by workflow)
  run_local.sh          local test run
  f12_capture.js        browser console snippet for network capture
docs/DEPLOYMENT.md      ← the complete step-by-step setup guide
.github/workflows/announcer.yml
state/state.json        application memory (committed)
```

## Local usage (optional; production runs on GitHub)

```bash
./scripts/run_local.sh              # dry run: discovery only
./scripts/run_local.sh --diagnose   # print a streamrecorder report
RUN_LIVE=1 DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/…' ./scripts/run_local.sh
python3 -m pytest tests/ -q         # run the test suite
```

## Configuration

Everything is environment-driven; only `DISCORD_WEBHOOK_URL` is required.

| Variable | Default | Meaning |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | *(required)* | Discord webhook that receives announcements |
| `STREAMERS` | `beaststatsofficial` | comma-separated channel list |
| `WAIT_MINUTES` | `60` | session wait timer (minutes) |
| `BACKLOG_DAYS` | `7` | how far back to discover recordings |
| `CONTACT` | `@bobmasterbillie` | contact line in announcements |
| `STREAMRECORDER_COOKIE` | *(empty)* | optional session cookie for full downloads |

## Security

- No credentials in code; everything comes from environment/secrets.
- Logs are redacted (webhook tokens, cookies, signed URLs, upload ids).
- `state/state.json` holds no secrets; savedly delete-tokens are stored but
  never logged. Keep the repository **private**.
- `.gitignore` blocks local secrets (`.env`, cookies) and temp files.

## Honest limitations

- Anonymous downloads from streamrecorder.io are **preview-capped (~200 MB)**.
  Full-length recordings need the `STREAMRECORDER_COOKIE` secret (free
  account). How much a free account unlocks is visible in the logs after the
  first runs; `scripts/f12_capture.js` documents how to capture the exact
  logged-in download flow if the endpoints ever change.
- streamrecorder.io free plan stores recordings ~5 days; `BACKLOG_DAYS=7`
  therefore covers everything the source still exposes.
- savedly.net deletes files after 7 days without views (stated in every
  announcement).
- GitHub scheduled workflows can be delayed by a few minutes at busy times
  and get disabled after 60 days without repo activity - the state commits
  after every run count as activity, so a working pipeline stays alive.
