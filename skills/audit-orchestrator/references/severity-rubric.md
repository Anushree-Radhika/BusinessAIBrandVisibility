# Severity & confidence rubric (shared convention across all sub-skills)

## Severity

- **critical** — the page/site is invisible or unreachable to AI crawlers outright (robots.txt block, fully client-side-rendered blank HTML on sampled pages). Nothing else matters until this is fixed.
- **high** — the page is reachable but a major fact-mapping or trust mechanism is missing or broken across most of the sample (no JSON-LD entity anywhere, no dateModified anywhere, vague prose with no spec table, non-200 responses on sampled pages). Directly causes wrong or missing answers.
- **medium** — a supporting signal is missing that degrades ranking/freshness/orientation but doesn't block extraction outright (no sitemap, no sameAs, stale lastmod majority, no deep-link anchors, interstitial friction, no specifications endpoint).
- **low** — a proactive improvement with smaller expected impact (no llms.txt, no canonical sameAs beyond a generic one, large page weight, no referrer-aware logic).
- **info** — audit coverage notes, skipped checks, or a check that requires a tool the invoking agent may not have (e.g. community-corroboration without a live search tool). Never counted in the required critical/high/medium/low summary totals.

## Confidence (0.0–1.0)

Every finding also carries a `confidence` score so the orchestrator's sort and the final report don't present a heuristic guess with the same certainty as a direct, unambiguous observation:

- **0.85–1.0** — direct, unambiguous evidence (an HTTP status code, presence/absence of a specific header, a robots.txt rule, a JSON parse failure).
- **0.6–0.84** — a reliable heuristic with some room for false positives (script/text-ratio CSR detection, keyword-based vague-language detection, id-attribute-name matching for engagement anchors).
- **below 0.6** — a weaker signal offered as a proactive suggestion rather than an asserted defect (referrer-awareness detection via inline-script regex, cloaking inferred from a single homepage length comparison).

## Shared conventions

- Every finding includes `affected_urls` and a `limitations` list (which may be empty) so a reader can see exactly what was checked and where the check's own blind spots are, rather than trusting a bare assertion.
- Sub-skills assign severity and confidence at the point of detection using this shared scale so the orchestrator's sort and summary counts are meaningful across skills, not just within one.
- A finding should never be manufactured just because an optional, non-universal convention (llms.txt, IndexNow, a specific schema type) is absent on an otherwise healthy site — these are `low`/proactive, not `high`/`critical`, and should say so in their own `suggested_action.expected_impact` framing.
