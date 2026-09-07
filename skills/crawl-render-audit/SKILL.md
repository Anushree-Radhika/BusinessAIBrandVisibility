---
name: crawl-render-audit
description: Checks whether a non-rendering AI crawler (GPTBot, ClaudeBot, PerplexityBot, OAI-SearchBot) can reach and read a page — robots.txt blocks, client-side-rendering gaps where raw HTML is blank, User-Agent-based cloaking, missing or invalid JSON-LD entity markup, network-vs-HTTP error distinction, and missing sitemap. Use as part of an AI-discoverability audit, or standalone when asked why a page "doesn't show up" in AI assistants.
license: MIT
allowed-tools: Bash(python3:*), Read
---

# Crawl & Render Audit (off-site discoverability, gates 1 & 2)

## When to use
As part of `audit-orchestrator`, or standalone when a user asks specifically why a page seems invisible to AI assistants despite being visible to a human.

## Inputs
- `url` (required) — used only in standalone mode to build a small corpus.
- `--corpus <path>` (preferred) — a pre-built shared corpus from `audit-orchestrator/scripts/crawl.py`, so the site is only crawled once across all sub-skills.
- `--rendered-corpus <path>` (optional) — a JSON `{url: rendered_visible_text}` map from the invoking agent's own browser-rendering tool, enabling a genuine (non-heuristic) raw-vs-rendered diff. This script never launches a browser itself.

## Procedure (deterministic)
1. Load the corpus (either given, or self-built in standalone mode via a bounded 5-page crawl).
2. Check `robots.txt` for a Disallow affecting any major AI-bot user agent (GPTBot, ClaudeBot, PerplexityBot, OAI-SearchBot, anthropic-ai, Google-Extended, ChatGPT-User, CCBot). This is gate 1 (reach); a block here makes everything downstream moot.
3. Split fetch failures into **network-level** (DNS/timeout/connection error — no HTTP response at all) and **HTTP-level** (a real status code, just not 200). These are different problems with different fixes and must never be reported as the same finding.
4. Detect client-side-rendering gaps. If the invoking agent supplies `--rendered-corpus <path>` (a JSON `{url: rendered_visible_text}` it produced with its own browser-rendering tool), perform a genuine raw-vs-rendered text diff per page and report a `critical` finding with 0.95 confidence when the raw HTML contains under 30% of the rendered text. Without a rendered corpus (the common case), fall back to a heuristic (very little visible text alongside a heavy inline-script payload and/or an empty `#root`/`#app`/`#__next` mount div) at lower confidence. This is gate 2 (read) of Appendix A.
5. Compare the homepage fetched with a standard browser User-Agent against the same URL fetched with a real AI-bot User-Agent (`GPTBot/1.0`); a large response-length or status difference is a cloaking signal worth flagging, even though it needs human confirmation.
6. Check JSON-LD presence, parse validity, and whether any recognized schema.org `@type` (Product/Organization/Brand/Article/etc.) is used — aggregated as a fraction of the sampled pages, not just the homepage.
7. Check for an XML sitemap at a declared or conventional location.

## Do not report as a defect
- A missing sitemap on a genuinely small, fully link-connected site (weight this lower, not absent, when the sampled page count itself is small).
- Blocking a named AI-*training* crawler while AI-*search/citation* crawlers (OAI-SearchBot, PerplexityBot) remain allowed — these are different opt-outs with different consequences; only flag the one relevant to citation/discoverability, and name which bots specifically are blocked.
- A single non-200 page that's clearly an authenticated area (login/cart/checkout) rather than part of the public content journey.
- A UA-response difference explained by ordinary A/B testing or personalization rather than a systematic bot-vs-human split — say so as a limitation rather than asserting deliberate cloaking.

## Output
A JSON array of finding objects (see the marketplace root `references/severity-rubric.md` for severity/confidence conventions), each with `title`, `severity`, `confidence`, `evidence`, `affected_urls`, `suggested_action`, `limitations`. The orchestrator assigns final `id`s.
