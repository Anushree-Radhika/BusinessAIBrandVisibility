Contributors :- @RiddhiAgrawal , @MrigajShaw and @AnushreeChoudhary

# brand-ai-readiness-audit

An Agent Skill Marketplace that audits any public website for **AI
discoverability** (getting found and cited by AI assistants) and **on-site
engagement** (keeping the visitor once they arrive), and emits a single
structured report of evidence-backed, severity-ranked, prioritized findings
and mechanism-sound fixes — plus a proactive content strategy grounded in
published Generative Engine Optimization research, not generic SEO
intuition. Recommend-only: nothing in this marketplace ever writes to,
authenticates against, or alters the target site — every check is a plain
HTTP GET.

## Grounding in published research

Two evidence bases underpin this marketplace's checks:

1. **The Round-2 appendix's systems-level reasoning** (how crawlers, RAG
   retrieval, and page-reading actually work) — the basis for
   `crawl-render-audit`, `spec-hallucination-audit`'s explicit-vs-implied
   fact reasoning, and `freshness-corroboration`'s entity-disambiguation
   checks.
2. **Aggarwal, Murahari, Rajpurohit, Kalyan, Narasimhan & Deshpande, "GEO:
   Generative Engine Optimization"** (KDD '24, arXiv:2311.09735) — the first
   paper to empirically measure, via a 10,000-query benchmark (GEO-bench)
   and a live test on a deployed generative engine (Perplexity.ai), which
   *content-level* changes actually move a source's visibility inside a
   generative-engine response. Its headline, directly actionable findings:
   - Adding **statistics, quotations, and citations to a source's own
     content** produced the largest measured visibility gains (reported in
     the 20-40% relative-improvement range on their benchmark metric), and
     these gains held up on a real, deployed generative engine, not just
     their own evaluation harness.
   - **Keyword stuffing** — the workhorse of classical SEO — showed little
     to no benefit, and in the live deployed-engine test actually
     underperformed making no change at all.
   - **Persuasive/authoritative tone alone**, with no added substance,
     likewise showed no significant benefit — generative engines proved
     comparatively robust to rhetorical style changes by themselves.
   - The most effective content strategy **varies by content domain** —
     their data linked citation- and statistics-style additions most
     strongly to factual, Law & Government, and debate-style content, and
     quotation-style additions most strongly to History, People & Society,
     and explanatory content.

   `freshness-corroboration` and the orchestrator's `geo_content_strategy`
   block operationalize these findings as deterministic, evidence-cited
   checks and a domain-matched proactive recommendation — see
   `skills/audit-orchestrator/scripts/geo_signals.py` for the implementation
   and exact attribution. Every finding that draws on this paper says so
   explicitly and reports it at low/info severity or as a strategic
   suggestion, never as a certain prediction for any specific site.

## Skills

| Skill | Concern | Entrypoint? |
|---|---|---|
| `audit-orchestrator` | Builds one shared page corpus, runs the checks below against it, merges/sorts findings, assigns ids, computes the proactive `geo_content_strategy` block, emits the final report | **Yes** |
| `crawl-render-audit` | Can a non-rendering AI crawler reach and read the page at all? (robots.txt, CSR-blank HTML — real raw-vs-rendered diff if the agent supplies one, else a heuristic — UA-based cloaking, JSON-LD presence/validity, sitemap, network-vs-HTTP error distinction) | No |
| `freshness-corroboration` | Are facts current, corroborated, and substantive? (Last-Modified/ETag, dateModified, sameAs, sitemap staleness, **statistics/quotations/citations vs. keyword-stuffing per published GEO research**, domain-matched content strategy) | No |
| `spec-hallucination-audit` | Are attributes explicit facts or vague prose an LLM has to guess-fill? (spec tables, JSON-LD additionalProperty booleans, specifications endpoint, **schema.org disambiguatingDescription for entity disambiguation, negative-grounding statements**) | No |
| `engagement-audit` | Does an AI-referred visitor land somewhere oriented to their intent? (**semantically-named** deep-link anchors, referrer-aware logic, interstitial friction, page weight, llms.txt) | No |
| `community-corroboration` *(optional)* | Is the brand corroborated or contradicted by independent third-party discussion? Degrades transparently to an `info` note without a live search tool | No |

## How the entrypoint composes them

`audit-orchestrator`'s `scripts/compose.py` is the only orchestration logic
in the marketplace, and it works in two stages:

1. **One shared crawl.** `scripts/crawl.py` builds a single corpus for the
   target site: parses `robots.txt` once (pages it disallows are excluded
   from the corpus entirely, so every downstream skill inherits the same
   crawl-permission decision — no skill can accidentally check a page it
   shouldn't have fetched), fetches the homepage with both a browser UA and
   a real AI-bot UA (for cloaking detection), discovers a diverse same-origin
   sample via the sitemap and internal links (up to 10 pages by default,
   skipping login/cart/checkout/admin/crawl-trap paths), and checks for a
   sitemap and `llms.txt`. This corpus is written to a temp file once and
   reused by every sub-skill — the site is never crawled twice.
2. **Four independent, bounded checks against that one corpus.** Each
   mandatory sub-skill's `scripts/check.py --corpus <path>` runs as its own
   subprocess (own timeout, own error handling — one sub-skill failing
   doesn't crash the audit; it's surfaced in `diagnostics.sub_skill_errors`),
   and returns findings with `title`, `severity`, `confidence`,  `evidence`,
   `affected_urls`, `suggested_action`, and `limitations`.

The orchestrator then merges true duplicates (same skill + same finding
title, unioning `affected_urls`), sorts by severity then confidence, assigns
sequential `id`s, computes the severity summary, and emits one JSON report.

Each sub-skill only knows how to detect and describe problems in its own
concern — it has no knowledge of the other three, and no knowledge of report
formatting. That separation is what let each one be written and tested
independently (see "Testing" below), and it's what makes the marketplace
extensible: a new concern is one more skill folder, one line in
`marketplace.json`, and one line in `compose.py`'s sub-skill list.

## Why these checks (mapped to the Round-2 appendix)

- **Appendix A** (reach → read → extract, in order) → `crawl-render-audit`'s
  robots.txt check (reach), CSR-blank-HTML check (read), and JSON-LD check
  (extract) directly mirror the three gates, in order — a robots block is
  `critical` precisely because nothing downstream matters if it fails.
- **Appendix C** (explicit text beats implied/buried facts) →
  `spec-hallucination-audit` is the one check that isn't "does structured
  data exist" but "does the *prose itself* force guessing" — a page can pass
  every JSON-LD check and still cause hallucinated specs if its copy is all
  vague marketing language with no fallback table.
- **Appendix D** (cross-source agreement, entity disambiguation) →
  `freshness-corroboration`'s `sameAs`/canonical-source checks, plus the
  optional `community-corroboration` skill, which is the only check in
  either half of this marketplace that looks *off* the target site at all.
- **Appendix A/C again, applied to the "does it look the same to a bot"
  question** → `crawl-render-audit`'s UA-cloaking check (comparing a
  browser-UA fetch against a real `GPTBot` UA fetch) — a check neither
  common SEO tooling nor a naive AI-generated skill tends to include,
  because it requires understanding that crawlers and browsers can be
  served different content by the same URL.
- **The engagement half of the brief** (visitors who arrive don't stay) →
  `engagement-audit`'s anchor/referrer/interstitial checks model the idea
  that an AI assistant's citation is only half the funnel; the landing page
  has to carry the visitor's context forward or the click is wasted.

## Testing

Every script is standalone and can be run directly, either against a bare
URL (self-crawls a small 5-page sample) or against a shared corpus:

```bash
# Build a corpus once and inspect it
python3 skills/audit-orchestrator/scripts/crawl.py https://example.com > /tmp/corpus.json

# Run one check against that corpus
python3 skills/crawl-render-audit/scripts/check.py https://example.com --corpus /tmp/corpus.json

# Run a check standalone, with no shared corpus (self-crawls)
python3 skills/engagement-audit/scripts/check.py https://example.com

# Run the full composed audit
python3 skills/audit-orchestrator/scripts/compose.py https://example.com

# Include the optional community-corroboration skill (degrades to an info
# note without a live search tool, as documented in its own SKILL.md)
python3 skills/audit-orchestrator/scripts/compose.py https://example.com --community

# Supply a real rendered-DOM corpus (if the invoking agent has a browser
# tool) for a genuine, non-heuristic raw-vs-rendered diff
python3 skills/audit-orchestrator/scripts/compose.py https://example.com \
    --rendered-corpus /tmp/rendered.json   # {url: rendered_visible_text}
```

All scripts use only the Python standard library (`urllib`, `re`, `json`,
`html.parser`, `xml.etree.ElementTree`) — no external dependencies, no
pre-trained model weights, portable to any environment that can run
`python3` and reach the target URL over plain HTTP(S). Verified end-to-end
against live sites during development (a full composed audit against a
9-page real-world corpus completed in under 2 seconds).

## Guardrails

- Read-only throughout: every check is a `GET`; nothing is written to, or
  authenticated against, the target site.
- `robots.txt` is parsed once, before any page enters the shared corpus — a
  disallowed page is reported as a finding, never fetched anyway.
- Runtime: default 10-page shared crawl with an 8s per-request timeout and a
  90s internal budget, plus five bounded subprocess checks — well under the
  5-minute budget for a typical site (observed ~1-3s in testing).
- Every finding carries a `confidence` score and a `limitations` list so
  heuristic checks (CSR detection, vague-language keyword matching,
  UA-cloaking inference) are never presented with the same certainty as a
  direct HTTP/robots.txt/JSON-parse observation.
- `info`-severity findings (coverage notes, optional/degraded checks) are
  reported for transparency but never counted toward the required
  critical/high/medium/low summary totals.
- No pre-trained model weights or external services bundled; total package
  size is a few tens of KB of Python/Markdown.
