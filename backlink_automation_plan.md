# Scoring Zone — Daily Backlink Research + Submission Agent Plan (2026)

**Objective:** Create a reliable, mostly autonomous system that:
- Discovers new high-authority, relevant backlink opportunities daily
- Reviews and scores them against our authority guidelines
- Attempts submissions (with human oversight initially)
- Tracks results and reports daily
- Runs in the background with minimal daily intervention

**Core Principles (non-negotiable):**
- Authority and relevance first (see `authority_guidelines.md`)
- Only use Steel.dev for browser automation (CAPTCHA/stealth already solved)
- Respect rate limits and target site terms
- Human-in-the-loop for the first 2-4 weeks (agent proposes, human approves submissions)
- Everything logged to our existing trackers (`directory-submissions.md`, `backlink-targets.md`) + simple logs

---

## High-Level Architecture

**Language:** Python (leverage existing `steel_utils.py`, `discover_targets.py`, `submit_*.py`)

**Key Components (modular agents):**
1. **Research Agent** — Daily discovery of candidates
2. **Review & Scoring Agent** — Filter + rank using authority guidelines + LLM
3. **Submission Agent** — Automate form/profile submissions via Steel
4. **Monitoring Agent** — Check previous submissions for "live" status + basic traffic signals
5. **Orchestrator + Reporter** — Runs the daily loop and produces a summary

**Scheduling Options (pick one):**
- Simple & reliable: `cron` (Mac/Linux) or Task Scheduler (Windows) running `orchestrator.py` once per day
- More robust: Python `schedule` library inside a long-running script or Docker container
- Future: Prefect, Airflow, or even a small FastAPI + Celery setup if this grows

**State & Memory:**
- Primary: Our existing Markdown trackers (parse + append)
- Secondary: `data/candidates.json`, `data/submissions_log.json`, `logs/daily_YYYY-MM-DD.log`
- Optional: Lightweight SQLite for querying later

**LLM Usage:**
- The review/scoring step can use **Grok** (xAI), Claude (Anthropic), OpenAI, or a local model.
- We have a dedicated prompt at `backlink_agent/prompts/scoring_prompt.txt` written for this.
- You can switch models by setting the appropriate API key secret (e.g. `XAI_API_KEY` for Grok).
- The orchestrator falls back to the simple rule-based scorer if no LLM key is provided or if the call fails.

---

## Daily Workflow (what the agent should do every day)

**Time:** Suggested 8-10am local (after any manual review from previous day)

**Step-by-step:**

1. **Research Phase** (15-30 min)
   - Run enhanced version of `discover_targets.py` against known roundup pages + new search queries
   - Perform targeted web searches: "golf directory submit", "best golf apps [current year]", "sports tech directory", "golf coach tools list", etc.
   - Scrape promising pages using cheap `client.scrape()` first, then full Steel pages only when needed
   - Output: 30-100 raw candidate URLs

2. **Review & Scoring Phase** (20-40 min)
   - For each candidate:
     - Visit page with Steel (or scrape)
     - Extract: title, description, submission method, any DR/traffic signals, dofollow hints, contact/submit form details
     - Run LLM scoring prompt against `authority_guidelines.md` criteria
     - Apply hard filters (minimum relevance, no obvious spam, has real submission path)
   - Output: Ranked shortlist of 5-12 high-quality targets with scores + justification + recommended submission approach

3. **Submission Phase** (variable, 0-60+ min)
   - For each approved target in shortlist:
     - If form-based and we have a template → run Steel submission (like current Eat Sleep / Tinylaunch scripts)
     - If profile-based → prepare content + either auto-fill (if possible) or flag for manual
     - Always: Take screenshots before/after, extract confirmation
     - Log to trackers immediately
   - Safety: `DRY_RUN=true` flag by default. Human must approve before real submissions in early phase.

4. **Monitoring Phase** (10-15 min)
   - Check yesterday's submissions for "live" status (visit the directory page, search for "Scoring Zone")
   - Check a rotating sample of older live listings
   - Optional: Basic GSC or Analytics pull for new referring domains (can be manual or via API later)

5. **Reporting**
   - Generate daily summary (file + optional email/Slack):
     - New candidates researched
     - Shortlist with scores
     - Submissions attempted + results
     - Newly live listings
     - Any issues or items needing human review
   - Append to `logs/daily_YYYY-MM-DD.log` and update the main trackers

---

## Implementation Phases (Recommended Rollout)

### Phase 1 — Foundation (Week 1)
- Create `backlink_agent/` directory
- `research.py` — improved discovery (expand on current `discover_targets.py`)
- `review.py` — scoring engine + LLM prompt
- `monitor.py` — live status checker
- `orchestrator.py` — simple daily runner
- `authority_guidelines.md` (already created)
- Update existing `backlink-strategy.md` to reference the agent

**Deliverable:** Agent can research + review daily and produce a ranked shortlist + report. No auto-submission yet.

### Phase 2 — Submission Automation (Week 2)
- Generalize submission logic (support multiple form templates)
- Create `templates/` for common submission types (golf directory, SaaS profile, etc.)
- Add `submit.py` that can handle the most common patterns using Steel
- Add human approval step (e.g. agent creates a "pending_submissions.json" file)

**Deliverable:** Agent can propose + (with approval) execute submissions for form-based targets.

### Phase 3 — Monitoring + Polish (Week 3+)
- Improve monitoring (detect when listings go live automatically)
- Add basic traffic attribution tracking (UTM + GSC)
- Add simple "as featured in" suggestions for the website
- Increase autonomy (lower human review threshold for high-scoring targets)
- Add weekly summary report

---

## Technical Details & Existing Assets

**Reuse heavily:**
- `steel_utils.py` (the `steel_page` context manager is gold)
- `discover_targets.py` (base for research)
- `submit_eatsleepgolf.py` and `submit_tinylaunch.py` (patterns for submission scripts)
- Existing visual assets and submission text files

**New files to create:**
- `backlink_agent/research.py`
- `backlink_agent/review.py`
- `backlink_agent/submit.py` (or per-type scripts)
- `backlink_agent/monitor.py`
- `backlink_agent/orchestrator.py`
- `backlink_agent/prompts/scoring_prompt.txt`
- `backlink_agent/templates/`

**Environment / Secrets (GitHub repo + local .env):**
- STEEL_API_KEY (Steel browser)
- OPENROUTER_API_KEY (for LLM scoring via Grok or other models on OpenRouter)
- GH_TOKEN (custom PAT with Contents: Read&write + Issues: Read&write) OR rely on default GITHUB_TOKEN (with workflow permissions set)
- The .env.example documents all. Copy to .env for local runs (orchestrator loads it automatically).

**Example cron (Mac/Linux):**
```bash
0 9 * * * cd "/Users/stephenpickering/Documents/Grok/Scoring Zone Backlinks" && python3 backlink_agent/orchestrator.py >> logs/daily_$(date +\%Y-\%m-\%d).log 2>&1
```

---

## Human Oversight Model (Important)

**Weeks 1-2 (High oversight):**
- Agent produces daily shortlist + draft submission text
- Human reviews shortlist (approve/reject/adjust)
- Human triggers submission (or agent does it in DRY_RUN and human reviews the plan)

**Week 3+ (Lower oversight):**
- Agent auto-submits anything scoring 85+ that matches known form patterns
- Human still reviews weekly summary and any low-confidence decisions
- Human can pause the agent easily (simple flag file)

---

## Risks & Mitigations

- **Over-submission / spam signals** → Strict daily limits (max 5-8 submissions/day), strong relevance filter
- **Site changes breaking forms** → Make submission scripts resilient + log failures clearly for quick fixes
- **Steel credit usage** → Use cheap `client.scrape()` for research wherever possible; full browser only when needed
- **Low-quality targets slipping through** → LLM scoring + hard rules from `authority_guidelines.md`
- **Google penalty risk** → Only pursue relevant, non-spammy targets. Diversify. Monitor GSC.

---

## Success Metrics for the Agent Itself

- Number of high-quality (75+ score) targets discovered per week
- % of shortlist that actually gets submitted and goes live
- Reduction in manual research/submission time (target: <30 min/day of human time after month 1)
- Measurable improvement in referring domains from golf/sports tech sites

---

## Hosting the Agent (Where to Run This)

The agent is a Python script that:
- Calls Steel.dev (cloud browsers)
- Optionally calls LLM APIs
- Reads/writes local files (trackers, logs, JSON state)
- Needs to run on a schedule (daily)

Here are **5 practical hosting options**, ranked roughly from simplest to most "production":

### 1. GitHub Actions (Scheduled Workflows) — Recommended starting point
- **Pros**: Free for public repos (or 2000 min/month on free private), zero server management, easy secrets (STEEL_API_KEY, etc.), can commit changes back to the repo (perfect for updating your .md trackers), built-in cron scheduling.
- **Cons**: 6-hour job limit (fine for this), runners are ephemeral (use artifacts or commit for persistence), not ideal for very long-running interactive sessions.
- **How**: Create `.github/workflows/daily-backlinks.yml` with `on: schedule: - cron: '0 9 * * *'`. Use `actions/checkout`, set up Python, run the orchestrator, and commit any changes.
- **Best for**: You right now. Low friction, leverages your existing repo.

### 2. Railway.app
- **Pros**: Very developer-friendly, one-click deploys from Git, built-in cron jobs, persistent volumes, environment variables, easy logging, cheap.
- **Cons**: Not the absolute cheapest at scale.
- **How**: Deploy the repo as a "Worker" or use their Cron feature. Mount a volume for the `data/` and `logs/` folders so your .md files persist.
- **Best for**: Quick production feel without managing servers.

### 3. Render.com (Cron Jobs or Background Worker)
- **Pros**: Free tier available, excellent Git-based deploys, native cron job support, persistent disks, good Python support.
- **Cons**: Free web services sleep; use their dedicated cron or worker types.
- **How**: Create a "Cron Job" service that runs your Python script on a schedule. Use a disk for file persistence.
- **Best for**: Clean, reliable scheduled execution.

### 4. DigitalOcean Droplet (or Hetzner Cloud / Linode VPS)
- **Pros**: Full control, very cheap (~$4-6/month for a basic droplet), run real cron, persistent filesystem, can SSH in, easy to install Python + dependencies.
- **Cons**: You manage the server (updates, security, uptime).
- **How**: Spin up a small Ubuntu droplet, clone the repo, set up a systemd timer or cron, use `python-dotenv` or env vars for keys. Use `screen`/`tmux` or systemd for reliability.
- **Best for**: When you want something simple and "always on" with full Linux access.

### 5. AWS Lambda + EventBridge Scheduler (Serverless)
- **Pros**: Pay only for what you use, highly scalable, no server management, EventBridge gives precise cron scheduling.
- **Cons**: Cold starts, 15-minute execution limit (usually fine), ephemeral filesystem (use S3, DynamoDB, or EFS for state and your .md files), more complex IAM/secrets setup.
- **How**: Package the script as a Lambda, trigger via EventBridge rule. Store trackers in S3 (or sync back to Git). Use Lambda layers or container images for dependencies.
- **Best for**: Long-term cost efficiency and if you already live in AWS.

---

## Recommendation for You Right Now

**Start with #1 (GitHub Actions)** for the next 1-2 weeks while you iterate on the agent. It's the fastest way to get daily runs without new accounts or servers.

Once you're happy with the logic and want something more "always-on" with easier local file access, move to **Railway** or **Render** (easiest managed experience) or a cheap **DigitalOcean Droplet** (most control for the price).

Avoid over-engineering with Lambda until the agent is stable and you're sure about execution time and state needs.

The GitHub Actions setup is now ready. Since you have enabled "Allow all actions and reusable workflows", the only remaining pieces on your side are:

1. Make sure the local folder is pushed to the GitHub repo (including `.github/workflows/daily-backlinks.yml` and the `backlink_agent/` folder).
2. Add the `STEEL_API_KEY` secret (under Settings → Secrets and variables → Actions).
3. Go to the **Actions** tab, select "Daily Backlink Agent", and manually run it with `mode=propose` to test.

Everything else (daily scheduling + human oversight via Issues) is already configured. The scheduled job will only ever propose — it will never submit without you manually triggering the submit mode after reviewing the Issue.