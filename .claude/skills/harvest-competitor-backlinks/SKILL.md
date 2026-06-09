---
name: harvest-competitor-backlinks
description: Harvest competitor backlink prospects via the Ubersuggest MCP and commit competitor_backlink_candidates.json for the Railway agent. Use when the user says "harvest competitor backlinks", "refresh competitor backlink candidates", "run the backlink harvest", or the agent logs warn the harvest file is stale.
---

# Harvest competitor backlinks

Build/refresh `competitor_backlink_candidates.json` (repo root) from Ubersuggest's backlink-gap
data: referring domains that link to competitor golf apps but NOT to scoringzone.net. The Railway
agent ingests this file as a discovery source (`research.discover_from_competitor_file`), so it
must be committed and pushed — `data/` is gitignored and never reaches Railway.

Requires the Ubersuggest MCP tools (only available in Claude Code sessions). Check
`mcp__ubersuggest__auth_status` first; if not authenticated, tell the user and stop.

## Steps

1. **Load config.** Read `competitors.json` at the repo root: `site` (negative target) and the
   `competitors` list. If the user asked to harvest specific competitors only, filter to those.

2. **Pull data per competitor.** For each competitor domain call
   `mcp__ubersuggest__backlink_opportunity` with:
   - `positive_targets`: `[{"target": "<competitor domain>", "scope": "domain"}]`
   - `negative_targets`: `[{"target": "<site>", "scope": "domain"}]`
   - `limit`: 25, paginate by passing the response's `nextKey` as `offset`.

   Fetch up to **6 pages (~150 rows) per competitor**; stop early when a page is empty or repeats
   prior rows. Each row: `{backlink (page URL), domain_authority, page_authority, competitors_to}`.
   You may also batch several competitors into one call's `positive_targets` to save calls, but
   per-competitor calls give better coverage of the smaller competitors.

3. **Hard junk filter.** Drop any row whose URL host matches (registrable domain or pattern):
   - App stores: `apps.apple.com`, `itunes.apple.com`, `play.google.com`, `download.cnet.com`
   - Shorteners/aggregators: `bit.ly`, `goo.gl`, `t.co`, `ow.ly`, `linktr.ee`, `feedburner.com`
   - Social/UGC: `facebook.com`, `twitter.com`, `x.com`, `instagram.com`, `youtube.com`,
     `reddit.com`, `linkedin.com`, `pinterest.com`, `medium.com`, `tiktok.com`, `quora.com`
   - Encyclopedias/search: `wikipedia.org`, `bing.com`, `google.com`
   - Big-media one-off editorial: `forbes.com`, `bbc.com`, `bbc.co.uk`, `techcrunch.com`,
     `theguardian.com`, `nytimes.com`, `cnn.com`, `businessinsider.com`, `time.com`, `wired.com`
   - Infrastructure subdomains: hosts starting `support.`, `docs.`, `help.`, `status.`, or ending
     `.zendesk.com`, `.helpscoutdocs.com`, plus throwaway hosts (`*.pages.dev`, `*.netlify.app`,
     `*.vercel.app`, tracking/hubspot redirect URLs)
   - Obvious scraper/auto-generated junk you recognise on sight: SEO-analysis pages
     (siteprice/websitedetection/knows.nl-style "stats for domain X"), "website-list-NNNN" link
     farms, APK mirror sites, keyword-stuffed `-k.html` content farms, podcast-platform episode
     pages, newsletter archive mirrors (campaign-archive, milled), and dead-link shorteners.
     Count these as junk-dropped rather than writing dozens of replicable:false rows.

4. **Aggregate to domains.** Group surviving rows by registrable domain. Per domain keep:
   - `domain_authority` / `page_authority`: the max seen
   - `url`: one representative page URL — prefer paths containing
     `directory|apps|tools|resources|best|review`, else the shortest path
   - `competitors_to`: union across rows (list of competitor domains)

5. **Classify replicability.** For each domain judge (mostly from domain name + URL path; use
   WebFetch only when genuinely ambiguous):
   - `category`: one of `directory | roundup | resource_page | review_site | blog | news | tool | other`
   - `replicable`: `true` if Scoring Zone could plausibly get the same link — directories,
     "best golf apps" roundups, resource/links pages, review sites covering multiple apps,
     golf blogs that accept guest posts or app coverage. `false` for one-off editorial news,
     personal blogs, corporate/partner pages, course websites that just mention an app once.
   - `replicability_reason`: one line, specific (this is shown to the scoring LLM and on the
     dashboard tooltip).

6. **Write the file.** `competitor_backlink_candidates.json` at the repo root. Include ALL kept
   domains (replicable or not — Python filters on the flag). Schema:

   ```json
   {
     "generated_at": "<ISO timestamp now>",
     "tool": "mcp__ubersuggest__backlink_opportunity",
     "competitors": ["thegrint.com", "..."],
     "candidates": [
       {
         "domain": "example.com",
         "url": "https://example.com/best-golf-apps/",
         "name": "example.com",
         "domain_authority": 54,
         "page_authority": 41,
         "competitors_to": ["thegrint.com", "hole19golf.com"],
         "replicable": true,
         "category": "roundup",
         "replicability_reason": "Best-golf-apps roundup covering multiple GPS apps"
       }
     ]
   }
   ```

   Sort candidates by `domain_authority` descending. Skip any domain already present in
   `directory-submissions.md` or matching `excluded_targets.json` (the agent would dedupe them
   anyway, but a clean file is easier to review).

7. **Summarize and commit.** Print a per-competitor table: rows fetched, dropped by junk filter,
   domains kept, replicable count. Then ask the user before running
   `git add competitor_backlink_candidates.json && git commit && git push` — the push is what
   deploys the file to Railway.

Remind the user to re-run this skill roughly monthly; the agent logs a staleness warning after
45 days (`COMPETITOR_FILE_STALE_DAYS` in `backlink_agent/research.py`).
