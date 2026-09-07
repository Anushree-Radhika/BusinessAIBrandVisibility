---
name: audit-orchestrator
description: Entrypoint that audits a public website for AI-discoverability and on-site-engagement problems by building one shared page corpus, composing crawl-render-audit, freshness-corroboration, spec-hallucination-audit, engagement-audit (and optionally community-corroboration), and emitting a single structured audit report with evidence, severity, confidence, and prioritized suggested actions. Use when a user gives a URL/domain and asks why a brand is invisible, misrepresented, stale, or bouncing in AI assistants, or requests a general "AI readiness" / "GEO" audit.
license: MIT
allowed-tools: Bash(python3:*), Read
---

# Brand AI-Readiness Audit — Orchestrator (entrypoint)

## When to use
Use this skill whenever the user gives a website URL/domain and wants to know why it is:
- missing or poorly cited by AI assistants (ChatGPT, Perplexity, Claude, etc.),
- shown with stale/incorrect facts, or has facts an assistant appears to invent,
- causing AI-referred visitors to bounce or fail to find what they came for.

Also use it proactively — even without an explicit complaint — whenever a user asks for a general "AI readiness" or "GEO" audit of a site.

## Inputs
- `url` (required): a fully-qualified `https://` URL or bare domain (normalize bare domains to `https://<domain>/`).
- Optional: `--max-pages N` (default 10) to widen/narrow the sample size.
- Optional: `--community` to also run the community-corroboration skill (requires the invoking agent to separately supply web/Reddit search results — see that skill's own doc; never assumed by default because it needs a live search tool this marketplace's scripts don't perform on their own).
- Optional: `--rendered-corpus <path>` — a JSON `{url: rendered_visible_text}` map from the invoking agent's own browser-rendering tool, forwarded to `crawl-render-audit` for a genuine (non-heuristic) raw-vs-rendered-DOM diff instead of the text/script-ratio fallback.

## Procedure (deterministic)
1. Normalize the input into a single absolute URL.
2. Run `scripts/compose.py <url> [--max-pages N] [--community]`. This subprocess:
   a. Builds **one shared, robots.txt-respecting, multi-page corpus** via `scripts/crawl.py` — homepage plus a diverse same-origin sample (product/category/article pages via sitemap + internal links), fetched once. Pages `robots.txt` disallows are never added to the corpus, so every downstream skill inherits the same crawl-permission decision.
   b. Invokes each mandatory sub-skill's `scripts/check.py <url> --corpus <path>` as an independent read-only subprocess against that one shared corpus — no re-crawling, no duplicate requests to the target site.
   c. Collects each sub-skill's findings (`title`, `severity`, `confidence`, `evidence`, `affected_urls`, `suggested_action`, `limitations`), merges true duplicates (same skill + same title) by unioning `affected_urls`, and tags each with `source_skill`.
   d. Sorts findings critical → high → medium → low → info, using confidence as a tiebreaker, and assigns sequential `id`s (`F-001`, `F-002`, ...).
   e. Computes the `summary` counts-by-severity block (info findings are reported but never counted toward critical/high/medium/low totals — they are coverage notes or optional opportunities, not defects).
   f. Emits the final JSON report to stdout, including `coverage` (how many pages were actually sampled/blocked) and `diagnostics` (any sub-skill errors or cross-cutting limitations) for transparency.
3. Do not alter, edit, hallucinate, or invent any finding beyond what `compose.py` returns. If a sub-skill errored (see `diagnostics.sub_skill_errors`), surface that transparently rather than fabricating a finding to fill the gap. If `coverage.pages_fetched` is much smaller than requested, say so before presenting findings as comprehensive.
4. Present the JSON report to the user, followed by a short plain-language summary: lead with the highest-severity, highest-confidence findings, and group suggested actions by the discoverability/engagement split.

## Composing the sub-skills
Each sub-skill is scoped to one concern so it can be reasoned about, tested, and improved independently — and each is independently portable (it can also be run standalone against a bare URL, in which case it builds its own small corpus, for testing outside the marketplace):

- **crawl-render-audit** — can a non-rendering AI crawler even reach and read the page? (robots.txt, JS-rendering gap, User-Agent cloaking, JSON-LD presence/validity, sitemap existence, network-vs-HTTP error distinction)
- **freshness-corroboration** — are facts marked as current and corroborated elsewhere? (HTTP/schema freshness signals, sameAs entity links to Wikidata/Wikipedia/Crunchbase, sitemap lastmod staleness)
- **spec-hallucination-audit** — are concrete attributes stated explicitly enough that a retrieval system won't need to probabilistically guess them? (vague marketing prose without a spec table, boolean attributes mentioned without an explicit true/false fact, a dedicated specifications endpoint)
- **engagement-audit** — once a visitor arrives from an AI assistant, can they orient quickly? (deep-link anchors, referrer-aware landing logic, interstitial friction, page weight, llms.txt)
- **community-corroboration** (optional) — is the brand corroborated or contradicted by independent third-party discussion? Only meaningful with a live search tool; degrades transparently to an `info` coverage note otherwise.

This orchestrator does not duplicate their logic; it only builds the shared corpus, invokes, merges, sorts, and formats. Adding a new concern later requires one more skill folder, one line in `marketplace.json`, and one line in `compose.py`'s `MANDATORY_SUB_SKILLS` — no changes to the existing four.

## Output
A single JSON audit report matching the schema below (minimum required shape from the contest brief; every other field is a documented extra beyond the floor):

```json
{
  "site": "https://example.com",
  "audited_at": "2026-09-06T12:00:00+00:00",
  "summary": { "total_findings": 5, "critical": 0, "high": 1, "medium": 3, "low": 1, "info": 0 },
  "findings": [
    {
      "id": "F-001",
      "title": "No JSON-LD structured data found on any sampled page",
      "severity": "high",
      "confidence": 0.9,
      "evidence": "Scanned 6 pages; found 0 with a <script type=\"application/ld+json\"> block.",
      "affected_urls": ["https://example.com/", "..."],
      "suggested_action": { "summary": "...", "priority": "high", "expected_impact": "..." },
      "limitations": [],
      "source_skill": "crawl-render-audit"
    }
  ],
  "proactive_recommendations": ["... low/info-severity findings, surfaced separately for convenience ..."],
  "geo_content_strategy": {
    "detected_domain": "business",
    "recommended_geo_methods": [{ "method": "Cite Sources", "reported_relative_improvement": "20-30%" }],
    "subjective_impression_proxy": { "uniqueness_proxy": 42.1, "diversity_proxy": 60, "influence_proxy": 30, "relevance_proxy": 55 },
    "source": "Aggarwal et al., 'GEO: Generative Engine Optimization', KDD'24 (arXiv:2311.09735)"
  },
  "coverage": { "pages_requested": 10, "pages_fetched": 9, "blocked_by_robots": [], "elapsed_sec": 2.0 },
  "diagnostics": { "sub_skill_errors": [], "limitations": [] }
}
```

`geo_content_strategy` is always emitted, independent of whether any defect was found — it is the "beyond the problems detected" proactive half of the brief, grounded in the published GEO paper's own domain-to-method benchmark results rather than generic SEO intuition. `subjective_impression_proxy` is explicitly labeled as a static-text heuristic proxy for the paper's four qualitative sub-metrics (relevance, influence, uniqueness, diversity) — never presented as a measurement against a live generative engine.

## Guardrails
- Read-only, always. No skill invoked here ever writes to, authenticates against, or rate-abuses the target site.
- Respects `robots.txt` at the corpus-building stage, before any sub-skill runs — a disallow for AI bots is itself a finding, never something to route around.
- Runtime target: under 5 minutes for a typical site. The shared crawl (default 10 pages, 8s/request timeout, ~90s internal budget) plus five bounded subprocess checks comfortably fits this in practice (observed ~1-3s for a small site in testing).
- No pre-trained model weights or external services; everything is Python standard library.
