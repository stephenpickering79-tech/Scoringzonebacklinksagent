# Deploying the Backlink Agent on Railway

This runs the whole agent — the **daily research job** and the **dashboard website** — on
[Railway](https://railway.com) in one small always-on service. After setup you only ever look at:

- **your dashboard URL** (Output / Sessions / Submissions), and
- Railway's **Deploy Logs** (to confirm the daily run happened).

No GitHub tabs needed for day-to-day use. Cost: **free first month**, then **$5/month** (our
service fits inside Railway's included $5 usage).

---

## One-time setup (about 5 minutes, all clicking — no terminal)

1. **Sign up** at [railway.com](https://railway.com) → **"Login with GitHub"** (use the same
   GitHub account that owns the repo).

2. **Create the project from the repo:**
   - Click **New Project → Deploy from GitHub repo**.
   - Pick **`Scoringzonebacklinksagent`**.
   - Railway reads the repo, installs Python, and starts it using the `Procfile`
     (`web: python serve.py`). The first build takes a couple of minutes.

3. **Give it a web address:**
   - Open the service → **Settings → Networking → Generate Domain**.
   - You'll get a URL like `something.up.railway.app` — that's your dashboard.

4. **Add your keys (Variables):**
   - Open the service → **Variables** → add:
     - `STEEL_API_KEY` = your Steel key (required for research/submissions)
     - `SERPER_API_KEY` = your serper.dev key *(enables web-search discovery of NEW directories;
       without it the agent still runs on your curated list + roundup scrape)*
     - `OPENROUTER_API_KEY` = your OpenRouter key *(optional — better scoring; skip to use the
       built-in scorer)*
     - `RUN_ON_START` = `true` *(optional, just for the first deploy — runs one job immediately
       so the dashboard fills in without waiting for 9am. Remove it afterwards.)*
     - `RUN_HOUR` = `9` *(optional — hour of day, 0–23, for the daily run; default is 9)*

5. **Add storage so data survives restarts:**
   - Open the service → **Variables/Settings → + Volume** (or the **Volumes** tab).
   - Set the **mount path** to exactly **`/app/data`**.

6. **Done.** Open your `*.up.railway.app` URL — the dashboard loads. To watch a run, open the
   service → **Deployments → View Logs** and look for `Propose cycle starting…`.

---

## How it works day-to-day

- **Every day** at `RUN_HOUR` (default 09:00, Railway server time) the service researches + scores
  new backlink targets and refreshes the dashboard. It **never submits anything automatically**.
- **You review** proposals on the dashboard's **Output** tab.
- **Updating the submissions table:** edit `directory-submissions.md` in GitHub (the same file as
  before). When you commit it, Railway **auto-redeploys** and the dashboard updates within a minute
  or two.
- **Running a real submission** (Eat Sleep Golf, Tinylaunch, etc.) stays manual — it uses a browser
  via Steel. Run it on demand either locally or with the Railway CLI:
  `railway run python submit_eatsleepgolf.py`.

## Notes
- Code still lives in **GitHub** (for safekeeping + version history). Railway just *runs* it and
  redeploys whenever `main` changes. You don't need the GitHub **Actions** or **Issues** tabs.
- The old GitHub Actions daily schedule is **turned off** so the job doesn't run in two places. The
  manual "Run workflow" button still exists as a backup.
- GitHub Pages (the old dashboard) is now redundant — Railway is the live dashboard. You can leave
  Pages on or turn it off; either is fine.
- Keep the service small (it's a tiny static server + a once-a-day script) so it stays within the
  $5 included usage.
