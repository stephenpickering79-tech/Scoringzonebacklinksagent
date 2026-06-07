# Scoring Zone — Backlink Agent

An automated, mostly-hands-off system that finds and tracks backlink opportunities for
[Scoring Zone](https://www.scoringzone.net) (a golf short-game app), scores them, and shows them on
a live dashboard for human review. It runs daily in the cloud; **nothing is submitted automatically
today** — submissions are still done manually (auto-submit is on the roadmap).

---

## 🟢 Running in production

- **Host:** [Railway](https://railway.com) — one always-on service, auto-deploys from the GitHub
  `main` branch.
- **Live dashboard:** https://web-production-d3894.up.railway.app/
- **Entry point:** [`serve.py`](serve.py) — a single process that (1) serves the dashboard from
  `docs/` on `$PORT`, (2) runs the daily research+score job on an internal scheduler at `RUN_HOUR`
  (default 09:00), and (3) regenerates the dashboard data on boot.
- **Persistence:** a Railway **volume mounted at `/app/data`** holds run data (proposals, sessions)
  across redeploys. The committed dashboard JSON lives in `docs/data/` and is regenerated each run.
- **Repo:** `stephenpickering79-tech/Scoringzonebacklinksagent` (branch `main`). Direct pushes to
  `main` are blocked — all changes go through a PR; Railway redeploys on merge.

### Environment variables (set in Railway → service → Variables)
| Var | Purpose | Notes |
|-----|---------|-------|
| `STEEL_API_KEY` | Steel.dev — scraping + browser submissions | required for discovery/submits |
| `SERPER_API_KEY` | Serper.dev — Google web-search discovery | enables "find new targets" |
| `OPENROUTER_API_KEY` | LLM relevance scoring of discoveries | optional |
| `OPENROUTER_MODEL` | scoring model | default `google/gemini-3.1-flash-lite` |
| `RUN_HOUR` | hour of day for the daily run (0–23) | default `9` |
| `TZ` | timezone for the schedule | set `Asia/Dubai` for 9am local |
| `RUN_ON_START` | run one cycle on boot | set `true` once to populate now, then remove |
| `DRY_RUN` | safety — never submit when `true` | keep `true` until auto-submit ships |
| `MAX_CANDIDATES_PER_DAY` / `MAX_SUBMISSIONS_PER_DAY` | limits | defaults 50 / 5 |

Full setup walkthrough: [`DEPLOY_RAILWAY.md`](DEPLOY_RAILWAY.md).

---

## How it works (daily)

1. **Seed** — read the curated target lists on disk (`directory-submissions.md` + fallback
   `backlink-targets.md`) for *open* targets. Network-free, so the candidate pool is **never empty**.
2. **Discover (additive)** — actively **web-search via Serper** for new submission/directory pages
   ("submit your golf app directory", etc.), filtered to real listing opportunities; plus a light
   scrape of known golf roundups via Steel. Excludes anything already tracked and anything in
   `excluded_targets.json`.
3. **Score** — curated targets are shown directly (already vetted); new discoveries are scored by
   Gemini (if `OPENROUTER_API_KEY` set) or presented for manual review.
4. **Publish** — write `docs/data/*.json`; the dashboard updates. A "research" session is logged.
5. **Review** — you triage on the dashboard. **Submitting is still manual.**

---

## Key files
- [`serve.py`](serve.py) — Railway entry point (web server + daily scheduler).
- [`backlink_agent/orchestrator.py`](backlink_agent/orchestrator.py) — run controller (propose/submit), scoring, report/shortlist output.
- [`backlink_agent/research.py`](backlink_agent/research.py) — candidate pool: seed + Serper search + roundup scrape + exclusions.
- [`backlink_agent/dashboard.py`](backlink_agent/dashboard.py) — builds `docs/data/*.json` (parses `directory-submissions.md`).
- [`steel_utils.py`](steel_utils.py) — Steel browser/scrape helpers + session recorder.
- [`submit_eatsleepgolf.py`](submit_eatsleepgolf.py), [`submit_tinylaunch.py`](submit_tinylaunch.py) — per-site Steel submission scripts (run manually).
- [`docs/`](docs/) — the static Console dashboard (`index.html`, `app.js`, `styles.css`, `tokens.css`, `data/*.json`).
- `directory-submissions.md` — **the source of truth** for the submissions table + DR (hand-edited).
- `excluded_targets.json` — targets the agent must never propose (e.g. Reddit, Product Hunt).
- [`.github/workflows/daily-backlinks.yml`](.github/workflows/daily-backlinks.yml) — legacy GitHub Actions runner; daily cron **disabled** (Railway owns scheduling), kept as a manual backup.

---

## ✅ Implemented
- Daily research that **seeds from the curated list** (never-zero candidates) + dedupes + exclusions.
- **Web-search discovery** (Serper) of new directories, filtered + scored.
- LLM scoring via OpenRouter (`google/gemini-3.1-flash-lite`), with rule-based / review fallback.
- **Claude-styled dashboard** (Output / Sessions / Submissions) with DR-sorted, sortable tables.
- **Steel session recording** surfaced on the dashboard (viewer URLs redacted on the public page).
- **Railway hosting** (always-on service + volume) with auto-deploy from `main`.
- Per-site manual submission scripts for Eat Sleep Golf and Tinylaunch.

## 🔜 Not yet implemented (roadmap)
1. **Approve → submit button on the dashboard** *(designed, not built — see
   `~/.claude/plans/i-also-want-to-purring-puffin.md`)*: an Approve button per proposal that queues a
   target; **7-day manual phase, then auto-submit**. Auto-submit covers only scripted sites
   (Eat Sleep Golf, Tinylaunch); all other approved targets are flagged "needs manual submit".
2. **More per-site submission scripts** — to widen what auto-submit can cover.
3. **Wire the orchestrator's `submit` path** to actually call the submit scripts (currently a stub;
   real submits are the standalone `submit_*.py`).
4. **Live-link monitoring** — automatically check whether submitted backlinks have gone live.
5. **Email/notification on new proposals** (optional, so the dashboard isn't the only touchpoint).
6. **Automatic DR enrichment** for discovered targets (currently DR is manual in the tracker).

## ⚠️ Outstanding tasks
- **Merge PR #7** ("Exclude Reddit + Product Hunt") — adds `excluded_targets.json`; not yet on `main`.
- **Confirm Railway vars:** `OPENROUTER_API_KEY` (for Gemini scoring) and `TZ=Asia/Dubai`; trigger a
  fresh run (`RUN_ON_START=true`, then remove) so web-search finds appear.
- **Rotate secrets** exposed early in development: the Steel API key and the `ghp_` GitHub PAT.

---

## Operating it day-to-day
- **View output:** the Railway dashboard URL (Output = proposals, Sessions = runs, Submissions =
  full history with DR). Run logs: Railway → service → **Deployments → Logs**.
- **Add/update a submission or its DR:** edit `directory-submissions.md` in GitHub → Railway
  auto-redeploys → dashboard updates.
- **Stop proposing a target:** add its name to `excluded_targets.json`.
- **Run a real submission:** manually run `submit_eatsleepgolf.py` / `submit_tinylaunch.py` (locally
  or via `railway run`) — these drive a Steel cloud browser. Then log the result in the tracker.

## Security notes
- The dashboard is **public** (Railway). Steel session `viewer_url`/`session_id` are redacted in the
  committed JSON. Secrets live only in Railway Variables / local `.env` (gitignored) — never committed.
