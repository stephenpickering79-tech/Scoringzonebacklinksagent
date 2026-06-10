# Backlink agent — future work

## Later phase: curated email outreach (NOT yet — Stephen decides when to start)
Current phase is self-serve submission only: the agent pursues directories and
listing pages it can submit to without human intervention. Articles, roundups,
newsletters and editorial coverage need personal outreach — deferred to here.

- **Seed list already exists**: `competitor_backlink_candidates.json` entries with
  category `roundup` / `blog` / `news` (~30 classified targets: best-golf-apps
  roundups like golf.com and The Manual, golf newsletters like Golfer's Odyssey
  and Caddyshanks, golf media). Research currently skips these
  (`research.SUBMITTABLE_CATEGORIES`); the rejected logs (`data/rejected_*.json`)
  also accumulate LLM-flagged "Outreach — later phase" finds.
- **To build when starting**: an outreach queue page on the dashboard, per-target
  pitch drafts (use the `sz-cold-email` skill), reply/result tracking in the
  submissions log. Keep sending manual — no automated mass email.
- Flip `SUBMITTABLE_ONLY=false` on Railway to let outreach-type discoveries
  surface again when ready.

## Other deferred items
- Per-site submit scripts beyond Eat Sleep Golf / Tinylaunch (grow the
  auto-submit allowlist as directories are vetted).
- GitHub Actions fallback scheduler (Railway is the only scheduler today).
- Live-link verification of email-confirmed listings (listing URLs arrive by
  email; no inbox access).
