---
name: community-corroboration
description: Optionally checks bounded, public, off-site community evidence (Reddit and general web discussion) for brand/entity mentions, recurring questions, stale claims, and contradictions with first-party facts, using the invoking agent's own web-search tool if one is available. Use only when off-site community visibility is explicitly requested, or when audit-orchestrator is invoked with --community.
license: MIT
allowed-tools: Bash(python3:*), Read, WebSearch
---

# Community Corroboration (optional, off-site)

## When to use
Only when explicitly requested by the user, or when `audit-orchestrator` is invoked with `--community`. Never run by default — this check's most valuable half depends on a live web/Reddit search tool the invoking agent may not have, and it must degrade transparently rather than silently producing nothing.

This directly reflects Appendix D: machines trust a fact more when many independent places repeat it consistently, and a brand name shared with something else can be confused unless disambiguated. Independent third-party discussion (not just schema.org markup) is part of that corroboration signal.

## Inputs
- `url` (required).

## Procedure
1. Run `scripts/check.py <url>` first. In script-only mode (no live search tool available to the invoking agent), it returns a single `info`-severity coverage note — never a fabricated finding — naming exactly which bounded, read-only search queries it *would* run (e.g. `"<brand>" reddit review`, `"<brand>" scam OR complaints`, `site:reddit.com "<brand>"`).
2. If the invoking agent has its own web/Reddit search tool available, run those same bounded query templates and report:
   - Consensus: facts repeated consistently across multiple independent, unrelated sources.
   - Contradiction: a fact repeated externally that conflicts with what the site's own pages state (report this as its own finding, referencing both the external claims and the specific first-party page it contradicts).
   - Entity confusion: discussion clearly about a different entity sharing the brand's name.
3. Never post, vote, comment, message, or authenticate anywhere as part of this check.

## Do not report as a defect
- Absence of Reddit/community discussion entirely — for a small or new brand this is normal, not a defect; report it as `info`, not as a negative finding.
- Treating a repeated community claim as a verified fact — community corroboration is a trust *signal*, not ground truth; state it as "commonly claimed" or "widely repeated," never as confirmed.
- Running unbounded or repeated scraping of any search engine or Reddit's HTML directly from this skill's own script — that is explicitly out of scope; only the invoking agent's own sanctioned search tool may be used for the live-search half of this check.

## Output
A JSON array of finding objects. In script-only mode, exactly one `info` finding documenting the query templates and the reason live search wasn't run. With a live search tool, additional findings following the shared severity rubric (`title`, `severity`, `confidence`, `evidence`, `affected_urls`, `suggested_action`, `limitations`), clearly labeled as community-sourced evidence, kept separate from first-party evidence.
