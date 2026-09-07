---
name: engagement-audit
description: Checks whether a page orients a visitor who just arrived from an AI assistant — deep-linkable section anchors, referrer/query-param-aware landing logic, interstitial/popup friction, page weight, and llms.txt presence — versus dumping them on a generic, high-friction homepage. Use as part of an AI-discoverability audit, or standalone when asked why AI-referred visitors bounce immediately.
license: MIT
allowed-tools: Bash(python3:*), Read
---

# Engagement Audit (on-site, post-referral)

## When to use
As part of `audit-orchestrator`, or standalone when a user asks why visitors who click through from an AI answer leave quickly without converting.

This skill explicitly does **not** claim to measure real bounce rate, conversion, or any private analytics — it checks observable, static, page-level conditions that plausibly cause bounce, and says so.

## Inputs
- `url` (required) — used only in standalone mode.
- `--corpus <path>` (preferred) — the shared corpus.

## Procedure (deterministic)
1. Check for **semantically-named** `id` attributes on sampled pages that describe a spec/pricing/review/FAQ/shipping/warranty section by name, versus only generic framework-default ids (`section-1`, `div3`, `root`, `app`) — a generated deep link is only self-explanatory if the fragment name itself says what's there.
2. Check for any referrer- or query-param-aware logic (`document.referrer`, `utm_*`, `ref=`) in the page's own inline HTML/scripts.
3. Check for high-friction interstitial patterns (modal/overlay/popup/newsletter-signup/cookie-banner classes) appearing more than once on a page.
4. Check raw HTML payload size as a coarse proxy for page weight/friction.
5. Check for an `llms.txt` file at the site root.

## Do not report as a defect
- Absence of `llms.txt` or referrer-aware logic on an otherwise well-functioning site — these are proactive, `low`-severity opportunities, never `critical`/`high`; the marketplace does not treat missing `llms.txt` as a defect.
- A single cookie-consent banner alone (required by law in many jurisdictions) — only flag when 2+ distinct interstitial patterns stack together, creating compounded friction.
- Referrer-aware logic that lives in an external JS bundle this check can't see — say so as a limitation rather than asserting its absence with full confidence.
- Claiming this check measures actual bounce rate or conversion — it does not, and must not imply it does in its findings.

## Output
A JSON array of finding objects (`title`, `severity`, `confidence`, `evidence`, `affected_urls`, `suggested_action`, `limitations`) per the shared severity rubric.
