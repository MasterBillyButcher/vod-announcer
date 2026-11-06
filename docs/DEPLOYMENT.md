# VOD Announcer — Complete Step-by-Step Setup Guide

No coding knowledge needed. Follow the steps in order. Total time: ~20 minutes.
After step 10 you can **shut your PC down and stay offline forever** — GitHub's
computers run everything.

---

## Step 0 — Download and unzip the project

1. Download **`vod-announcer.zip`** (sent to you with this guide).
2. Right-click the zip → **Extract All…** (Windows) or double-click (Mac/Linux).
3. You get a folder `vod-announcer/` — keep it on your Desktop, you'll need it
   in Step 2 or 3.

---

## Step 1 — Create the accounts (one-time)

1. **GitHub** (free): go to <https://github.com/signup> → sign up with your
   email → verify.
2. **Discord**: you surely have this already.

---

## Step 2 — Create the GitHub repository (pick ONE option)

### Option A — with the website only (no installs; easiest)

1. Log in at <https://github.com> → click the **+** (top right) →
   **New repository**.
2. Repository name: `vod-announcer`
3. Visibility: **Private** ← important (keeps your automation private)
4. Do NOT tick "Add a README".
5. Click **Create repository**.
6. On the new page click **"uploading an existing file"**.
7. Open your unzipped `vod-announcer` folder, select **everything inside it**
   (all files and folders: `app`, `tests`, `scripts`, `docs`, `state`,
   `.github`, `README.md`, `requirements.txt`, `conftest.py`,
   `.gitignore`…) and **drag them** into the browser upload area.
   - Tip: `.github` starts with a dot — it may be hidden on Windows.
     In File Explorer click **View → Show → Hidden items**.
   - The drag-and-drop upload preserves folders, so all files land in the
     right place. Wait until every file is listed, then click
     **Commit changes**.

> ⚠️ If you use Option A, first **delete the state file content trick**:
> nothing to do — the shipped `state/state.json` is already clean.

### Option B — with Git (power users)

```bash
# after unzipping:
cd vod-announcer
git init -b main
git add .
git commit -m "VOD Announcer initial commit"

# create the empty PRIVATE repo on github.com first (same as Option A
# steps 1-5, but do NOT upload files), then:
git remote add origin https://github.com/<YOUR-USERNAME>/vod-announcer.git
git push -u origin main
```

---

## Step 3 — Create the Discord webhook (2 minutes)

This is the "inbox" where the VOD announcements will be posted.

1. Open your Discord server (or create one: **+** → *Create My Own* →
   *For me and my friends*).
2. Click the **channel** where VOD links should appear (e.g. `#vods`), or
   make a new one (**+** next to a category).
3. Click the **gear icon** next to the channel (Edit Channel) →
   **Integrations** → **Webhooks** → **New Webhook**.
4. (Optional) rename it, e.g. `VOD Announcer`.
5. Click **Copy Webhook URL**.
   It looks like:
   `https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOp…`
6. Paste that URL somewhere safe (Notepad) — you need it in the next step.

---

## Step 4 — ⭐ PUT THE WEBHOOK LINK HERE ⭐

**This is the exact place where your webhook link goes:**

1. Open your repository on github.com:
   `https://github.com/<YOUR-USERNAME>/vod-announcer`
2. **Settings** (top bar) → left sidebar **Secrets and variables** →
   **Actions**.
3. Click the green **New repository secret** button.
4. Fill in EXACTLY:
   - **Name:** `DISCORD_WEBHOOK_URL`
   - **Secret:** *(paste your Discord webhook URL from Step 3)*
5. Click **Add secret**. ✅ Done — that's where the webhook lives.

### All secrets (add the rest the same way — these are OPTIONAL)

| Name | Value | Required? |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | your webhook URL | **YES** |
| `STREAMERS` | `beaststatsofficial` | no (already the default) |
| `WAIT_MINUTES` | `60` | no (already the default) |
| `BACKLOG_DAYS` | `7` | no (already the default) |
| `CONTACT` | `@bobmasterbillie` | no (already the default) |
| `STREAMRECORDER_COOKIE` | cookie string, see Step 8 | recommended for FULL downloads |

You only truly need the first one. The others already have correct defaults.

---

## Step 5 — Enable GitHub Actions

1. In your repo click the **Actions** tab.
2. If you see a banner *"Workflows aren't being run on this forked repository"*
   or similar → click **"I understand my workflows, go ahead and enable them"**.
3. You should see **VOD Announcer** in the left list. Scheduled runs start
   automatically (every 15 minutes).

---

## Step 6 — First run (manual test)

1. **Actions** tab → **VOD Announcer** → **Run workflow** ▼ (right side).
2. Leave both checkboxes (`dry_run`, `diagnose`) **unticked** for a real run —
   or tick **dry_run** first if you just want to check discovery.
3. Click **Run workflow**.
4. Click the new run in the list → **run** job → watch the live log.
5. What you want to see in the log:
   - `streamrecorder: 7 recordings in backlog window`
   - `NEW recording …` lines (first run only)
   - `downloaded …` → `renamed -> BS_…` → `savedly: uploaded -> https://savedly.net/f/…`
   - `session … ANNOUNCED with N part link(s)`
6. Check your Discord channel — the announcement should be there 🎉

Note: on the first real run the newest day's session waits ~60 minutes
(the anti-split timer) before announcing. Older days announce immediately.
Older-than-newest recordings may be reported as *blocked* until you do
Step 8 (they have no public download URL without a session cookie).

---

## Step 7 — That's it. Go offline. 🛋️

GitHub now runs the pipeline every ~15 minutes, forever:

- new recordings → downloaded → renamed `BS_YYYY-MM-DD_Title(_P01…)` →
  uploaded to savedly → links posted to your Discord channel.
- Your PC is never involved. Laptop closed, Wi-Fi off — doesn't matter.

---

## Step 8 — Full-length downloads (recommended, 5 minutes)

Anonymous downloads are **preview-capped (~200 MB)** by streamrecorder.io.
To unlock full recordings:

1. Create a free account at <https://streamrecorder.io> (Sign up) and log in.
2. In your browser press **F12** → **Application** tab (Chrome/Edge) or
   **Storage** (Firefox) → **Cookies** → `https://streamrecorder.io`.
3. Select **all** cookie rows. You need a copy-paste string of them:
   press **F12 → Console**, paste this, press Enter, and copy the output:
   ```js
   document.cookie
   ```
4. On GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**
   - **Name:** `STREAMRECORDER_COOKIE`
   - **Secret:** *(the copied cookie string)*
5. Re-run the workflow once (Step 6). Blocked parts now become full
   downloads. Every announcement then shows `Date / Duration / Parts / Links`.

If downloads still look capped, use the F12 capture in Step 9 and send me the
output — I'll adapt the downloader.

---

## Step 9 — If something misbehaves: the F12 console capture

If a GitHub run shows errors from streamrecorder.io or savedly.net (site
layouts change over time), capture what the sites actually do:

1. Open the site in your browser, press **F12** → **Console**.
2. Paste the whole content of **`scripts/f12_capture.js`** (from the project
   folder) and press Enter. It prints: *"running! Now do ONE manual action…"*
3. Do ONE manual action:
   - streamrecorder.io: click **Download** on a recording, or
   - savedly.net: upload any small file.
4. Copy every green **[VODCAPTURE]** line from the console and send it to me.
5. I patch the app to match the site's current behavior — you only re-upload
   the two changed files.

---

## Step 10 — Everyday life & troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| Run log: `config problem: DISCORD_WEBHOOK_URL is empty` | Secret missing/typo | Redo Step 4 (Name must be exactly `DISCORD_WEBHOOK_URL`) |
| No Discord message, log shows `discord: webhook rejected 404` | Webhook was deleted | Create a new webhook (Step 3) and update the secret |
| `streamrecorder: 0 recordings` | Page layout changed or channel renamed | Run Step 9 and send me the output |
| `savedly: 429 rate limited` | Upload speed limit hit | Nothing to do — it auto-retries next run |
| Old links died | savedly deletes files with **no views for 7 days** | Expected; open links soon. The app always posts fresh links for new VODs |
| Runs stopped after ~60 days | GitHub disables idle schedules | Open Actions → Run workflow once manually |
| I changed `state/state.json` by hand | …don't 🙂 | The bot commits it back every run |

**Cost: ₹0** — everything runs on GitHub's free tier.
**Privacy:** keep the repo **private**; all credentials live in GitHub
Secrets and are redacted from logs.
