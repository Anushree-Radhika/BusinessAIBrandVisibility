---
name: freshness-corroboration
description: Checks whether a page pushes freshness signals (HTTP Last-Modified/ETag, dateModified in JSON-LD, sitemap lastmod recency), entity corroboration (sameAs links to Wikidata/Wikipedia/Crunchbase), and content-substance signals empirically linked to generative-engine visibility (statistics, quotations, external citations) versus ineffective keyword-repetition padding, per published GEO research (Aggarwal et al., KDD'24). Also proposes a domain-matched content strategy as a proactive, non-defect recommendation. Use as part of an AI-discoverability audit, or standalone when asked why an assistant repeats outdated pricing/specs, confuses the brand with something else, or simply doesn't cite the page's content substantively.
license: MIT
allowed-tools: Bash(python3:*), Read
---

# Freshness, Corroboration & Content-Substance Audit

## When to use
As part of `audit-orchestrator`, or standalone when a user reports an assistant citing outdated facts, confusing the brand with a different entity sharing its name, or simply not drawing on the page's content at all.

## Inputs
- `url` (required) — used only in standalone mode.
- `--corpus <path>` (preferred) — the shared corpus from `audit-orchestrator/scripts/crawl.py`.

## Procedure (deterministic)
1. Check HTTP-level freshness headers (`Last-Modified`, `ETag`) across the sampled pages.
2. Check for `dateModified` in each page's JSON-LD — only meaningful, and only checked, on pages that have JSON-LD at all (if none do, that's already `crawl-render-audit`'s finding; don't double-report it here as a freshness gap).
3. Collect `sameAs` entity links across the sample; check whether any point to a canonical, high-authority source (Wikidata, Wikipedia, Crunchbase, LinkedIn company page) as opposed to only lower-authority or no external links at all.
4. Check the shared sitemap's `<lastmod>` coverage and staleness (more than ~13 months old, across a majority of entries).
5. **Content-substance check (published-research-grounded, via `geo_signals.py`):** scan each sampled page for quantitative statistics, quoted/attributed statements, and links to external sources. Published GEO research (Aggarwal et al., "GEO: Generative Engine Optimization," KDD'24) measured these three content properties as the strongest levers for a source's visibility inside generative-engine responses on their benchmark, with reported relative gains in the 20-40% range — substantially ahead of stylistic changes alone. A substantial page (150+ words) with none of the three is flagged.
6. **Keyword-stuffing check:** flag pages showing high single-word repetition density with no accompanying statistics or quotations. The same published research specifically measured keyword-repetition padding as providing little to no generative-engine visibility benefit, underperforming an unmodified baseline in one live test on a deployed generative engine — so this is reported as low-severity wasted effort to redirect, not as a technical defect.
7. **Proactive, domain-matched strategy (always emitted, `info` severity):** classify the homepage's apparent content domain via a simple keyword-bucket heuristic, and surface the published research's top-performing content-addition method(s) for that domain (e.g. their data linked Cite Sources/Statistics Addition most strongly to factual/Law & Government-style content, and Quotation Addition most strongly to History/People & Society/explanatory content). This is a strategic suggestion, never asserted as a defect.

## Do not report as a defect
- Missing `dateModified` on a site with no JSON-LD at all — that's a discoverability gap already owned by `crawl-render-audit`, not a freshness gap; only report it here when JSON-LD exists but omits the date.
- A single old `lastmod` entry among an otherwise fresh sitemap — only flag when a majority of sampled entries are stale.
- Absence of `sameAs` on a clearly unambiguous, singular brand name with no plausible naming collision — still worth a low-severity proactive note, but don't inflate it to `high` on the reasoning that every brand needs Wikidata regardless of ambiguity risk.
- A brand/product name repeated frequently — that's normal, not keyword stuffing; only flag high repetition when it comes with zero accompanying statistics or quotations.
- Treating the domain classification or the published research's reported percentages as a guaranteed outcome for this specific site — they are the paper's own aggregate benchmark results, always presented with that attribution and at `info`/low severity, never as a certain prediction.

## Output
A JSON array of finding objects (`title`, `severity`, `confidence`, `evidence`, `affected_urls`, `suggested_action`, `limitations`) per the shared severity rubric in `audit-orchestrator/references/severity-rubric.md`.
