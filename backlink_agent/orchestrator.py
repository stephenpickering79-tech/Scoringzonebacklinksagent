#!/usr/bin/env python3
"""
Backlink Agent Orchestrator - Human Oversight Model

Daily runner for Scoring Zone backlink automation.
See backlink_automation_plan.md for full design.

HUMAN OVERSIGHT MODEL (as requested):
- Scheduled / "propose" runs: ONLY research + review. Never submit.
  Generates a Markdown report + shortlist JSON.
  The GitHub Action will turn this into a GitHub Issue for human review.
- "submit" runs: Only executed when a human manually triggers the workflow
  (via workflow_dispatch) and provides a list of approved targets.
  This keeps full human control over what actually gets submitted.

Environment variables (set in GitHub repo secrets + workflow):
    STEEL_API_KEY (required for any Steel calls)
    AGENT_MODE=propose | submit          (set by the workflow)
    APPROVED_TARGETS="Target1,Target2"   (only for submit mode)
    DRY_RUN=true | false

Usage (local testing):
    AGENT_MODE=propose python3 backlink_agent/orchestrator.py
    AGENT_MODE=submit APPROVED_TARGETS="Eat Sleep Golf - Get Listed" DRY_RUN=true python3 backlink_agent/orchestrator.py
"""

import os
import json
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

# Load .env file if present (for local development with keys)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on real env vars

# Import our modules (they live in the same package).
# Handle both "python -m backlink_agent.orchestrator" (package) and direct script run.
try:
    from . import research as research_mod
    from . import approvals as approvals_mod
except ImportError:
    # Direct script run: add dirs to path for "import research" and "import steel_utils" (from inside research).
    import sys
    pkg_dir = Path(__file__).parent
    sys.path.insert(0, str(pkg_dir))           # for "import research"
    sys.path.insert(0, str(pkg_dir.parent))    # for "import steel_utils" (sibling to backlink_agent)
    import research as research_mod
    import approvals as approvals_mod

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

# How many newly-discovered targets to surface for human review each run. Discoveries are NOT
# auto-dropped by score (Stephen reviews them); this just keeps the daily list manageable.
MAX_DISCOVERIES_SHOWN = 25

LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


def log(message: str):
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOGS_DIR / f"daily_{datetime.now().strftime('%Y-%m-%d')}.log", "a") as f:
        f.write(line + "\n")


def load_config():
    return {
        "max_candidates_per_day": int(os.getenv("MAX_CANDIDATES_PER_DAY", "50")),
        "max_submissions_per_day": int(os.getenv("MAX_SUBMISSIONS_PER_DAY", "5")),
        "dry_run": os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes"),
        "min_authority_score": 75,
        "mode": os.getenv("AGENT_MODE", "propose").lower(),
        "approved_targets": os.getenv("APPROVED_TARGETS", "").strip(),
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", "").strip(),
        # Dashboard "Approve" queue → auto-submit controls (see process_approvals).
        # Master switch — OFF by default so nothing is ever auto-submitted until Stephen
        # deliberately turns it on in Railway. While off, approvals just queue for manual action.
        "auto_submit_enabled": os.getenv("AUTO_SUBMIT_ENABLED", "false").strip().lower() in ("1", "true", "yes"),
        # Go-live date (YYYY-MM-DD). If unset, defaults to launch_date + 7 days (the manual phase).
        "auto_submit_after": os.getenv("AUTO_SUBMIT_AFTER", "").strip(),
        # Safe default: dry-run ON unless explicitly disabled (real submit needs DRY_RUN=false).
        "auto_submit_dry_run": os.getenv("AUTO_SUBMIT_DRY_RUN", "true").strip().lower() not in ("0", "false", "no"),
    }


def generate_proposal_report(scored_targets, date_str):
    """Generate a human-friendly Markdown report for the GitHub Issue."""
    report_lines = [
        f"# Backlink Proposals – {date_str}",
        "",
        "**Human Review Required** – This run only researched and scored targets. Nothing was submitted.",
        "",
        "Review the shortlist below. Reply to this issue with the targets you approve (e.g. `APPROVE: 1,3,5` or list names).",
        "Then manually trigger the workflow with `mode=submit` and the approved list in the `approved_targets` input.",
        "",
        f"**Total candidates researched:** {len(scored_targets)} (top {len(scored_targets)} shown after filtering)",
        "",
        "## Scored Shortlist (sorted by authority score)",
        "",
        "| # | Target | Score | Relevance | Why it fits | Recommended Action |",
        "|---|--------|-------|-----------|-------------|--------------------|",
    ]

    for idx, t in enumerate(scored_targets, 1):
        name = t.get("name", t.get("url", "Unknown"))
        score = t.get("score", 0)
        rel = t.get("topical_relevance", "N/A")
        why = t.get("justification", "")[:80].replace("\n", " ")
        action = t.get("recommended_action", "Review manually")
        report_lines.append(f"| {idx} | {name} | {score} | {rel} | {why} | {action} |")

    report_lines.extend([
        "",
        "## How to approve & submit",
        "1. Reply here with the numbers or names you want to proceed with.",
        "2. Go to the **Actions** tab → **Daily Backlink Agent** → **Run workflow**.",
        "3. Choose `mode=submit` and paste the approved names/IDs into `approved_targets` (comma separated).",
        "4. The submit job will only run what you explicitly approved.",
        "",
        "Full shortlist JSON (for reference): `data/shortlist_{}.json`".format(date_str),
        "",
        "_Generated by the Backlink Agent (human oversight mode). See `backlink_automation_plan.md` for details._",
    ])

    return "\n".join(report_lines)


def main():
    log("=== Scoring Zone Backlink Agent - Daily Run Starting ===")
    config = load_config()
    log(f"Config: {config}")

    mode = config["mode"]
    log(f"Running in mode: {mode}")

    if mode == "propose":
        # === RESEARCH + REVIEW ONLY (never submits) ===
        log("Phase 1: Research - Discovering new candidates...")
        try:
            candidates = research_mod.run(max_candidates=config["max_candidates_per_day"])
        except Exception as e:
            log(f"Research failed: {e}")
            candidates = []

        # Split: pre-vetted targets from the user's own list vs. newly discovered ones.
        seeded = [c for c in candidates if c.get("preapproved")]
        new_candidates = [c for c in candidates if not c.get("preapproved")]
        log(f"Research complete. {len(candidates)} candidates "
            f"({len(seeded)} from your list, {len(new_candidates)} newly discovered).")

        log("Phase 2: Review & Scoring against authority guidelines...")

        # Curated targets are already vetted by the user — include them directly (no score cutoff).
        scored_seeded = score_seeded_targets(seeded)

        # New discoveries are ALWAYS surfaced for human review — never auto-dropped by score.
        # The LLM (if available) only ranks them; Stephen decides via Approve/Dismiss. A keyword
        # rule can't judge a new directory's quality and would wrongly drop real SaaS/general
        # directories that lack a golf keyword (and Stephen wants those surfaced too).
        openrouter_key = config.get("openrouter_api_key")
        if openrouter_key and new_candidates:
            log("Using OpenRouter to score new discoveries (for ranking, not filtering)...")
            try:
                # min_score=0 → keep ALL discoveries with their score; no relevance cutoff.
                scored_new = score_candidates_with_openrouter(new_candidates, openrouter_key, 0)
            except Exception as e:
                log(f"OpenRouter scoring failed ({e}); presenting discoveries for manual review.")
                scored_new = score_discoveries_for_review(new_candidates)
        else:
            if new_candidates:
                log("No OPENROUTER_API_KEY — presenting discoveries for manual review.")
            scored_new = score_discoveries_for_review(new_candidates)

        # Mark every discovery as needing review (these are NOT auto-vetted like curated targets).
        for s in scored_new:
            sc = s.get("score", 0)
            s["recommended_action"] = "Review — strong find" if sc >= config["min_authority_score"] else "Review — new find"

        scored_new.sort(key=lambda x: x["score"], reverse=True)
        scored_new = scored_new[:MAX_DISCOVERIES_SHOWN]  # keep the daily list manageable

        # Curated targets first (already sorted by seed score), then qualifying discoveries.
        scored = scored_seeded + scored_new

        # Hard floor: a curated, on-disk seed means this should be impossible — but never write
        # an empty list. If it ever happens, log loudly and fall back to every curated target.
        if not scored:
            log("WARNING: shortlist is empty after seeding+scoring — falling back to full curated list.")
            try:
                scored = score_seeded_targets(research_mod.all_tracker_targets())
            except Exception as e:
                log(f"Hard-floor fallback failed: {e}")

        log(f"Review complete. {len(scored)} targets on the shortlist "
            f"({len(scored_seeded)} curated, {len(scored_new)} new).")

        # Save shortlist for reference
        shortlist_path = DATA_DIR / f"shortlist_{datetime.now().strftime('%Y-%m-%d')}.json"
        with open(shortlist_path, "w") as f:
            json.dump(scored, f, indent=2)
        log(f"Shortlist saved to {shortlist_path}")

        # Generate human-readable report for the GitHub Issue
        date_str = datetime.now().strftime("%Y-%m-%d")
        report_md = generate_proposal_report(scored, date_str)
        report_path = DATA_DIR / f"daily_report_{date_str}.md"
        with open(report_path, "w") as f:
            f.write(report_md)
        log(f"Proposal report saved to {report_path}")

        # Also save a summary
        summary = {
            "date": date_str,
            "mode": "propose",
            "candidates_found": len(candidates),
            "seeded": len(seeded),
            "discovered": len(new_candidates),
            "shortlist_size": len(scored),
            "report_file": str(report_path),
            "shortlist_file": str(shortlist_path),
        }
        summary_path = DATA_DIR / f"summary_{date_str}.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        # Process the dashboard Approve queue (manual phase = queue only; auto phase =
        # submit scripted sites, flag the rest). Never lets a queue error break the run.
        try:
            process_approvals(config)
        except Exception as e:
            log(f"process_approvals failed: {e}")

        log("PROPOSE run complete. A GitHub Issue should be created by the workflow for human review.")
        log("Nothing was submitted. Human must manually trigger with mode=submit after review.")

    elif mode == "submit":
        # === SUBMISSION ONLY - only runs when human explicitly triggers it ===
        approved_raw = config.get("approved_targets", "")
        if not approved_raw:
            log("No approved_targets provided for submit mode. Exiting.")
            return

        approved_list = [t.strip() for t in approved_raw.split(",") if t.strip()]
        log(f"Human-approved targets for submission: {approved_list}")

        if config["dry_run"]:
            log("DRY_RUN is true — would have submitted these targets but doing nothing.")
            for target in approved_list:
                log(f"  [DRY] Would submit: {target}")
        else:
            log("Submitting approved targets (real mode)...")
            # In a full implementation we would call submit logic here
            # For now we just log and update a simple tracker note
            for target in approved_list[: config["max_submissions_per_day"]]:
                log(f"  [SUBMIT] Executing submission for: {target}")
                # TODO: wire real submission using steel_utils + templates

            log("Submission phase finished. Update directory-submissions.md manually or via future submit module.")

    else:
        log(f"Unknown AGENT_MODE: {mode}. Valid values: propose, submit")

    log("=== End of Daily Backlink Agent Run ===")


# ---------------------------------------------------------------------------
# Approval queue → submission (scheduled backstop)
# ---------------------------------------------------------------------------
# The primary path is immediate: serve.py runs each approval on click via
# backlink_agent.submitter. This sweep is the backstop — it catches anything still
# "approved" (e.g. approved while the server was down, or a submission that never
# started). Same dispatcher (scripted site → its script; else generic submitter).


def process_approvals(config) -> None:
    """Submit any still-"approved" targets via the shared dispatcher (backstop sweep).

    Gated by AUTO_SUBMIT_ENABLED. Respects MAX_SUBMISSIONS_PER_DAY. Idempotent — only
    status=="approved" is processed, and each item's status moves to submitting →
    submitted / needs_manual_submit / error, so later runs skip it.
    """
    pending = [a for a in approvals_mod.load_approvals() if a.get("status") == "approved"]
    if not pending:
        log("Approvals: none pending.")
        return

    if not config.get("auto_submit_enabled"):
        log(f"Approvals: {len(pending)} queued — AUTO_SUBMIT_ENABLED not set; leaving for manual/UI.")
        return

    try:
        from . import submitter as submitter_mod
    except ImportError:
        import submitter as submitter_mod  # type: ignore

    dry = config.get("auto_submit_dry_run")
    cap = config["max_submissions_per_day"]
    log(f"Approvals: backstop sweep — {len(pending)} pending (cap {cap}, dry={dry}).")
    done = 0
    for a in pending:
        if done >= cap:
            log(f"  cap ({cap}) reached — leaving the rest for the next run.")
            break
        name, url = a.get("name", ""), a.get("url", "")
        status, detail = submitter_mod.run_approval_submission(name, url, dry_run=dry)
        if status in ("submitted", "needs_manual_submit", "error"):
            done += 1


def _action_from_status(status):
    s = (status or "").lower()
    if not s or "not submitted" in s or "ready" in s or "prepared" in s:
        return "Submit"
    if "submitted" in s or "pending" in s or "applied" in s or "progress" in s:
        return "Awaiting / verify"
    return "Review"


# Cheap, no-network spam/quality signals from the text we already have. Used as a fallback when
# the LLM doesn't return spam_risk/quality_tier, and as a backstop so every candidate has a signal.
_SPAM_CUES = (
    "submit to", "directory submission service", "auto directory", "free directory", "link farm",
    "500 directories", "1000 directories", "100+ directories", "list of", "ultimate list",
)
_GOLF_CUES = ("golf", "putting", "short game", "chipping", "wedge", "sports tech", "fitness")


def _heuristic_quality(candidate) -> tuple[str, str]:
    """Return (spam_risk, quality_tier) from URL/title/source text. Conservative fallback."""
    import re as _re
    hay = " ".join(str(candidate.get(k, "")) for k in ("name", "url", "title", "source", "notes")).lower()
    listicle = bool(_re.search(r"\b\d{2,}\+?\s*(directories|sites|places|tools)\b", hay))
    high = listicle or any(c in hay for c in _SPAM_CUES)
    golf = any(c in hay for c in _GOLF_CUES)
    if high:
        return "high", ("Tier 3 filler" if golf else "Avoid")
    if golf:
        return "low", "Tier 1 niche"
    return "medium", "Tier 2 quality general"


def _ensure_quality_fields(scored: list) -> list:
    """Guarantee every scored item has spam_risk + quality_tier (LLM value wins; heuristic fills gaps)."""
    for s in scored:
        if not s.get("spam_risk") or not s.get("quality_tier"):
            hr, ht = _heuristic_quality(s)
            s["spam_risk"] = s.get("spam_risk") or hr
            s["quality_tier"] = s.get("quality_tier") or ht
    return scored


def score_seeded_targets(seeded):
    """Convert pre-vetted curated targets into shortlist entries directly (no score cutoff).

    These come from the user's own list, so they are always included — the rule-based scorer's
    golf-keyword heuristic must not drop them. Ordered by their seed score (priority).
    """
    out = []
    for c in seeded:
        status = (c.get("status") or "").strip()
        out.append({
            "name": c.get("name") or c.get("url", "") or "Unknown",
            "url": c.get("url", ""),
            "score": int(c.get("seed_score", 85)),
            "topical_relevance": "High",
            "justification": (c.get("notes") or "From your curated target list.")[:200].replace("\n", " "),
            "recommended_action": _action_from_status(status),
            "source": c.get("source", "Your target list"),
            "submission_method": "",
            "spam_risk": "low",          # curated = you vetted it
            "quality_tier": "Curated",
            "notes": status,
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def score_discoveries_for_review(candidates):
    """Present newly-discovered candidates for human review (no auto-judging).

    A keyword rule can't reliably assess a brand-new directory, so discoveries are surfaced at a
    modest, uniform score (below curated targets) and flagged for review on the dashboard. The
    human decides; nothing is ever auto-submitted.
    """
    out = []
    for c in candidates:
        why = (c.get("description") or c.get("title") or "").strip().replace("\n", " ")
        spam_risk, quality_tier = _heuristic_quality(c)
        out.append({
            "name": c.get("name") or c.get("url", "") or "Unknown",
            "url": c.get("url", ""),
            "score": 78,  # above the 75 floor (so it shows), below curated (80+)
            "topical_relevance": "Review",
            "justification": why[:200] or "New discovery — review for relevance.",
            "recommended_action": "Review — new find",
            "source": c.get("source", "web search"),
            "submission_method": "",
            "spam_risk": spam_risk,
            "quality_tier": quality_tier,
            "notes": c.get("method", ""),
        })
    return out


def score_candidates_rule_based(candidates, min_score):
    """Fallback simple rule-based scoring (golf-niche biased)."""
    scored = []
    for c in candidates:
        url = c.get("url", "")
        source = c.get("source", "")
        score = 60

        if any(kw in url.lower() for kw in ["golf", "eat sleep", "coach", "short game", "putting"]):
            score += 25
        if "eatsleepgolf" in url.lower() or "golf" in source.lower():
            score += 10
        if any(bad in url.lower() for bad in ["submit-your-site", "free-directory", "link-farm"]):
            score -= 30

        scored.append({
            "name": c.get("name", url.split("/")[-1] if url else "Unknown"),
            "url": url,
            "score": max(0, min(100, score)),
            "topical_relevance": "High" if score > 75 else "Medium",
            "justification": f"Source: {source}. Matched golf keywords." if score > 70 else "Generic directory.",
            "recommended_action": "Form submission via Steel" if score > 75 else "Manual review",
            "source": source,
        })

    scored = [s for s in scored if s["score"] >= min_score]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def _readable_host(url: str) -> str:
    """A human-friendly host label for a URL (netloc minus www), for when no name is available."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def score_candidates_with_openrouter(candidates, api_key, min_score):
    """Score candidates using OpenRouter (can route to Grok, Claude, etc.)."""
    import requests

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/stephenpickering79-tech/Scoringzonebacklinksagent",
        "X-Title": "Scoring Zone Backlink Agent",
    }

    scored = []
    prompt_template = open(BASE_DIR / "backlink_agent/prompts/scoring_prompt.txt").read()
    guidelines = open(BASE_DIR / "authority_guidelines.md").read()

    # Model is configurable via the OPENROUTER_MODEL env var (set it in Railway to switch without
    # a code change). Default: Google Gemini 3.1 Flash Lite — fast + cheap for relevance scoring.
    # Other examples: "x-ai/grok-3", "anthropic/claude-3.5-sonnet", "openai/gpt-4o".
    model = os.getenv("OPENROUTER_MODEL", "google/gemini-3.1-flash-lite").strip()

    for c in candidates:
        url_c = c.get("url", "")
        title = c.get("title") or c.get("source", "")
        desc = c.get("description") or c.get("source", "") or url_c
        snippet = c.get("snippet", "")[:500]

        # Make the prompt self-contained by including the guidelines (the template references the .md but the LLM can't read files).
        prompt = f"""Here are the official Scoring Zone Backlink Authority Guidelines:

{guidelines}

---

Now follow the instructions below and score this candidate.

{prompt_template.format(
            url=url_c,
            title=title,
            description=desc,
            snippet=snippet
        )}"""

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 700,
            "response_format": {"type": "json_object"}  # Ask for JSON if the model supports it
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=90)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Try to parse JSON (the prompt asks for pure JSON)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Fallback: try to extract JSON from the response
            import re
            match = re.search(r'\{.*\}', content, re.DOTALL)
            data = json.loads(match.group(0)) if match else {}

        score = int(data.get("overall_score", 50))
        if score >= min_score:
            scored.append({
                # Never blank: prefer LLM name, else the research-supplied name, else a readable host.
                "name": (data.get("name") or c.get("name") or _readable_host(url_c) or url_c).strip(),
                "url": url_c,
                "score": score,
                "topical_relevance": data.get("topical_relevance_score", "N/A"),
                "justification": data.get("justification", "Scored via OpenRouter"),
                "recommended_action": data.get("recommended_action", "Review manually"),
                "source": c.get("source", ""),
                "submission_method": data.get("submission_method", ""),
                "spam_risk": str(data.get("spam_risk", "")).strip().lower(),
                "quality_tier": str(data.get("quality_tier", "")).strip(),
                "notes": data.get("notes", "")
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return _ensure_quality_fields(scored)


if __name__ == "__main__":
    main()
