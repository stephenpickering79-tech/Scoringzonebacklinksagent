# Scoring Zone — Daily Backlink Research + Submission Agent (Current Implementation)

**Status:** The agent is fully implemented and hosted on GitHub Actions in this repo. It runs daily with a strict **human oversight model** (no automatic submissions). Research + LLM-powered review happens automatically and produces a GitHub Issue for review. Submissions are only executed when you manually trigger them after approving targets.

**Core Principles (non-negotiable):**
- Authority and relevance first (see `authority_guidelines.md`)
- Only use Steel.dev for browser automation (CAPTCHA/stealth already solved)
- Respect rate limits and target site terms
- Strict human-in-the-loop: Scheduled runs only *propose* (create review Issue). You explicitly approve and trigger submissions.
- Everything logged to our existing trackers (`directory-submissions.md`, `backlink-targets.md`) + committed data/logs in the repo
- Uses OpenRouter (defaulting to Grok via `x-ai/grok-3`) for scoring when `OPENROUTER_API_KEY` is provided; falls back to rule-based scoring otherwise

---

## High-Level Architecture (What Was Actually Built)

**Language:** Python

**Hosting:** GitHub Actions (in this repo). Scheduled daily "propose" job + manual `workflow_dispatch` for both modes. Uses `gh` CLI for issues, commits changes back to the repo.

**Key Files (current structure):**
- `.github/workflows/daily-backlinks.yml` — The GitHub Action. Two jobs:
  - `propose`: Runs on schedule (daily 09:00 UTC) **or** manual `workflow_dispatch` with `mode=propose`. Does research + scoring, generates report, creates GitHub Issue for human review. Commits artifacts. Never submits.
  - `submit`: Only runs on manual `workflow_dispatch` with `mode=submit` + `approved_targets` input. Runs the agent for approved items (current orchestrator submit path is a stub that logs; real submissions use the separate `submit_*.py` scripts or manual execution). Commits tracker updates.
- `backlink_agent/orchestrator.py` — Main entry point. 
  - Loads `.env` (via python-dotenv) for local runs.
  - Supports `AGENT_MODE=propose` (research + score → report + shortlist JSON) or `submit` (for approved targets).
  - Research: Calls `research.py` (scrapes known golf/sports-tech roundup pages + naive filter for "directory/submit/list/resource/tools" links).
  - Scoring: If `OPENROUTER_API_KEY` present, calls OpenRouter (model `x-ai/grok-3` by default) using the prompt in `prompts/scoring_prompt.txt` (now includes the full `authority_guidelines.md` content for self-contained LLM scoring). Otherwise falls back to simple rule-based scorer (golf-niche biased, min score 75).
  - Generates `data/daily_report_*.md` and `data/shortlist_*.json`.
  - Submit path currently logs approved targets (placeholder/TODO for full wiring to submission scripts).
  - Configurable via env: `DRY_RUN`, `MAX_CANDIDATES_PER_DAY`, `MAX_SUBMISSIONS_PER_DAY`.
- `backlink_agent/research.py` — Basic discovery: Scrapes a hardcoded list of golf app roundups (golfinsideruk, todays-golfer, etc.) using `steel_utils.cheap_scrape`, extracts candidate links matching keywords. Returns list of `{url, source, ...}`. (Includes TODO for live web search expansion.)
- `backlink_agent/prompts/scoring_prompt.txt` — LLM prompt for scoring (expert backlink strategist role, outputs JSON with scores/justifications/actions per the guidelines).
- `steel_utils.py` + `requirements.txt` — Steel.dev browser automation (for any form submissions that need real browser + CAPTCHA solving) + deps.
- `authority_guidelines.md` — The scoring rules (tiers, red flags, framework) that the LLM prompt now includes verbatim.
- `.env.example` + local `.env` (gitignored) — For local testing (keys + mode).
- `backlink-strategy.md`, `backlink-targets.md`, `directory-submissions.md` — Trackers and strategy (updated as part of runs).
- Submission helpers: `submit_eatsleepgolf.py`, `submit_tinylaunch.py`, `indie-hackers-launch-draft.md`, etc. (used for actual submissions; agent submit path can call/reference them).

**Secrets (GitHub repo) + Local .env:**
- `STEEL_API_KEY` (Steel.dev for browser automation)
- `OPENROUTER_API_KEY` (for LLM scoring via OpenRouter; defaults to Grok `x-ai/grok-3`)
- `GH_TOKEN` (custom fine-grained PAT with Contents: Read&write + Issues: Read&write) — preferred for reliability. Falls back to default `GITHUB_TOKEN` (with workflow permissions set to Read+Write in repo Settings > Actions > General).
- The `.env` (local only) + repo secrets use matching names. Orchestrator auto-loads `.env` for local runs.

**Human Oversight (Strict — No Auto-Submissions):**
- Scheduled/propose runs: Research + score → GitHub Issue for review. `DRY_RUN=true` by default in scheduled context.
- You review the Issue (reply with approvals or note names).
- You manually trigger submit (Actions → Run workflow → mode=submit + approved_targets list).
- This matches the "human-in-the-loop" requirement. No auto-approval or auto-submit logic is active.

**Current Limitations / Notes (as-built):**
- Research is starting-point scraping of known roundups (not full live search yet).
- Submit path in orchestrator is a logging stub (real execution via the `submit_*.py` scripts or manual for now; can be wired further).
- No full monitoring/live-status checking in the agent yet (manual or future addition).
- Uses cheap Steel scrape where possible for research; full browser sessions only when needed for submissions.
- Commits artifacts back (reports, shortlists, tracker updates) for audit trail.
- Node.js 24 forced via env var (to avoid deprecation warnings for checkout/setup-python).
- No pip caching in setup-python (avoids requirements file lookup issues).
- .gitignore protects secrets, logs, data, temp files.

---

## Daily Workflow (Actual Current Behavior)

**Scheduled (automatic "propose" run — 09:00 UTC):**
1. Checkout + Python setup (no pip cache).
2. Install from requirements.txt (fallback if needed).
3. Run `orchestrator.py` with `AGENT_MODE=propose`, `DRY_RUN=true`.
4. Research scrapes known roundups → candidates.
5. Scoring via OpenRouter/Grok (or rules) → filtered shortlist (min 75 score).
6. Generate report + shortlist JSON.
7. Create GitHub Issue with the proposals (for your review).
8. Commit artifacts back to repo.
9. (Non-fatal errors are logged but do not stop the job.)

**Manual "submit" run (you trigger after review):**
- Same setup.
- Run with `AGENT_MODE=submit` + `APPROVED_TARGETS=...`.
- Executes (stub for now) + commits tracker updates.
- Use the separate submission scripts for actual form/browser work as needed.

**Local testing (from this folder):**
```bash
# Load keys
source .env  # or let python-dotenv handle it

# Propose (research + review, generate local report)
AGENT_MODE=propose python3 backlink_agent/orchestrator.py

# Submit (approved targets only)
AGENT_MODE=submit APPROVED_TARGETS="Eat Sleep Golf - Get Listed" DRY_RUN=true python3 backlink_agent/orchestrator.py
```

**GitHub Actions Usage:**
- Scheduled: Automatic propose (creates Issue).
- Manual: Actions tab → Daily Backlink Agent → Run workflow. Choose mode + approved_targets (for submit).
- Output: Proposals → GitHub Issue (review there). Submissions → logs + committed tracker updates.

**Files Generated/Committed:**
- `data/daily_report_*.md` (human-readable proposals)
- `data/shortlist_*.json`
- `logs/daily_*.log`
- Updates to `directory-submissions.md` / `backlink-targets.md` (on submit)

---

## Updated Files & How They Mirror the Code

- `backlink_automation_plan.md` (this file): Now describes the *as-built* system (GitHub Actions hosting, OpenRouter + Grok, strict human oversight, current file structure, actual behavior vs. aspirational phases).
- `backlink-strategy.md`: Automation section updated to reference the implemented GitHub agent (instead of future phases or old XAI direct integration).
- `.env.example`: Matches current required vars (STEEL_API_KEY, OPENROUTER_API_KEY, GH_TOKEN, AGENT_MODE, DRY_RUN, etc.) + notes on repo secrets vs. local.
- `.gitignore`: Added (protects .env, logs/, data/, temp files, __pycache__, etc.).
- `requirements.txt`: Added (steel-sdk, python-dotenv, requests) — used by the workflow.
- Workflow + orchestrator: Already updated for OpenRouter, GH_TOKEN support, Node 24, no cache, import fixes, self-contained LLM prompt (guidelines embedded), etc. (No further doc changes needed beyond this plan.)

The "Implementation Phases" section above has been replaced with a description of the **current implemented state**. The agent is production-ready for the propose/review flow with your human oversight. Full auto-submission wiring in the orchestrator submit path and advanced monitoring can be added later if desired.

**Next Steps (if you want to continue):**
- Test a manual "propose" run (Actions tab) → review the resulting Issue.
- Manually trigger "submit" for approved targets.
- (Optional) Enhance research (add more roundups or live search), wire real submit calls into orchestrator, add email/Slack notifications for new Issues, etc.

The system is now fully mirrored in the docs and ready to run daily on GitHub with the human oversight you specified. Let me know the next piece to tackle! 

(Everything stays in this repo. The agent runs in the background via the scheduled workflow.)