---
name: spec-hallucination-audit
description: Checks whether product/brand attributes are stated as explicit, structured facts (spec tables, JSON-LD additionalProperty booleans, a dedicated specifications endpoint, schema.org disambiguatingDescription for entity disambiguation) rather than vague marketing prose that forces a retrieval system to probabilistically guess and hallucinate incorrect attributes. Also checks for negative grounding -- explicit statements of what is NOT true/included -- since silence on an attribute is a gap a retrieval system may fill incorrectly. Use as part of an AI-discoverability audit, or standalone when asked why an assistant invents wrong product specs, attributes, or confuses the brand with an unrelated entity of the same name.
license: MIT
allowed-tools: Bash(python3:*), Read
---

# Spec-Hallucination & Entity-Disambiguation Audit (probabilistic gap-filling)

## When to use
As part of `audit-orchestrator`, or standalone when a user reports an assistant stating a wrong or invented product attribute (e.g. claiming something is waterproof when it isn't, or vice versa), or confusing the brand with a different entity of the same name.

This skill is intentionally distinct from `crawl-render-audit`'s JSON-LD *presence* check: a page can have perfectly valid schema.org markup and still force hallucination if its actual prose is vague about attributes the schema doesn't cover, or if the entity markup itself doesn't disambiguate the brand from anything else sharing its name.

## Inputs
- `url` (required) — used only in standalone mode.
- `--corpus <path>` (preferred) — the shared corpus.

## Procedure (deterministic)
1. Scan each sampled page's visible text for common vague/unquantified marketing phrases ("engineered for", "premium quality", "best-in-class", etc.) and check whether the page separately provides a structured `<table>` or `<dl>` spec block. Vague language alone isn't a problem; vague language *with no structured fallback anywhere on the page* is.
2. Scan for a fixed list of commonly-asked, commonly-hallucinated boolean attributes (waterproof, vegan, machine washable, BPA-free, etc.) mentioned in prose without an explicit yes/no nearby and without a matching JSON-LD `additionalProperty`.
3. Check for a dedicated `/specifications`-style endpoint as a machine-queryable fallback.
4. **Entity disambiguation:** where an Organization/Brand/LocalBusiness JSON-LD entity exists, check whether it declares schema.org's own `disambiguatingDescription` property -- the field schema.org defines specifically to distinguish an entity from other things sharing its name. An entity that exists but omits this is a cheap, concrete, non-obvious fix.
5. **Negative grounding:** check whether the sampled content ever explicitly states what is *not* true or included (e.g. "does not include", "not compatible with"), specifically when ambiguous boolean-style attributes were also detected in step 2. Total silence on an attribute nobody asked about is fine; attributes raised only positively site-wide with zero explicit negations anywhere is the risky pattern, because a retrieval system treats an unstated fact as unknown and may fill the gap with a plausible-but-wrong guess.

## Do not report as a defect
- Marketing language that appears on a page that *also* has a full, explicit spec table elsewhere on the same page — vague copy plus concrete facts is normal marketing writing, not a hallucination risk.
- A boolean attribute keyword appearing in an unrelated context (e.g. "vegan" in a blog post about a company's charity partner, not describing the product itself) — this is a known limitation of keyword-based detection; flag it with a limitation note rather than asserting certainty, and prefer under-flagging over a long list of low-precision hits.
- A missing `/specifications` endpoint on a site that isn't product-oriented at all (a blog, a documentation site) — only weight this for pages that look like product/service pages.
- Missing `disambiguatingDescription` on a site with no Organization/Brand entity at all — that absence is already `crawl-render-audit`'s finding; only report this when the entity exists but the specific field is missing.
- Missing negative-grounding statements on a page where no ambiguous boolean attribute was ever raised in the first place — there's nothing to ground against.

## Output
A JSON array of finding objects (`title`, `severity`, `confidence`, `evidence`, `affected_urls`, `suggested_action`, `limitations`) per the shared severity rubric.
