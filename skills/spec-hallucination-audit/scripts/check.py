#!/usr/bin/env python3
"""
spec-hallucination-audit / check.py

Appendix C: the more explicitly and unambiguously a fact is stated in plain
text, the more likely a machine extracts it correctly; the more it's
implied or buried in vague prose, the more likely a retrieval system fills
the gap probabilistically -- i.e. hallucinates a plausible-but-wrong spec.

This is the "beyond the obvious checklist" skill: it doesn't check whether
schema exists (crawl-render-audit does that), it checks whether the page's
own prose forces guessing even when structured data is present.

Modes: --corpus <path> (preferred) or a bare <url> (standalone mode).

Usage:
    python3 check.py https://example.com
    python3 check.py https://example.com --corpus /tmp/corpus.json
"""
import sys
import os
import re
import json
import argparse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "audit-orchestrator", "scripts"))
try:
    from crawl import build_corpus, fetch  # noqa: E402
except Exception:
    build_corpus, fetch = None, None

VAGUE_TERMS = [
    "engineered for", "built to last", "premium quality", "best-in-class",
    "designed for performance", "next-level", "ultimate", "cutting-edge",
    "industry-leading", "unmatched", "revolutionary", "state-of-the-art",
    "weather-resistant", "all-day comfort", "rugged", "high-performance",
]
BOOLEAN_ATTRS = ["waterproof", "water resistant", "machine washable",
                  "vegan", "recyclable", "bpa-free", "gluten-free"]
SPEC_PATHS = ["/specifications", "/specs", "/specifications.html"]
NEGATION_RE = re.compile(
    r"\b(does not|doesn't|is not|isn't|no warranty|not compatible|not included|"
    r"without|excludes?|not available|not supported)\b", re.I)


def load_corpus(args):
    if args.corpus:
        with open(args.corpus) as f:
            return json.load(f)
    if build_corpus is None:
        return {"error": "no --corpus given and crawl.py unavailable for standalone mode"}
    return build_corpus(args.url, max_pages=5)


def strip_tags(html):
    return re.sub(r"<[^>]+>", " ", html)


def extract_declared_props(page):
    declared = set()
    for blob in page.get("jsonld_blobs", []):
        try:
            data = json.loads(blob)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for prop in item.get("additionalProperty", []) or []:
                    if isinstance(prop, dict) and "name" in prop:
                        declared.add(str(prop["name"]).lower())
        except Exception:
            continue
    return declared


def check(corpus):
    findings = []
    limitations = []
    site = corpus.get("site", "")
    origin = corpus.get("origin", site)
    pages = [p for p in corpus.get("pages", []) if p.get("status") == 200 and not p.get("fetch_error")]
    # This skill is most meaningful on product/spec-bearing pages; still runs on all
    # sampled pages but weights evidence toward product-like ones.
    if not pages:
        limitations.append("No successfully-fetched pages in the corpus; spec-hallucination checks skipped.")
        return findings, limitations

    vague_no_table_pages = []
    ambiguous_attr_pages = {}
    for p in pages:
        html = p.get("html", "")
        if not html:
            continue
        text = strip_tags(html).lower()
        vague_hits = [t for t in VAGUE_TERMS if t in text]
        has_table = bool(re.search(r"<table[\s>]", html, re.I))
        has_dl = bool(re.search(r"<dl[\s>]", html, re.I))
        if vague_hits and not (has_table or has_dl):
            vague_no_table_pages.append(p["url"])

        declared_props = extract_declared_props(p)
        found_ambiguous = []
        for attr in BOOLEAN_ATTRS:
            if attr in text and attr not in declared_props:
                window = re.search(r".{0,40}" + re.escape(attr) + r".{0,40}", text)
                near_boolean = window and re.search(r"\b(yes|no|true|false)\b", window.group())
                if not near_boolean:
                    found_ambiguous.append(attr)
        if found_ambiguous:
            ambiguous_attr_pages[p["url"]] = found_ambiguous

    if vague_no_table_pages:
        findings.append({
            "title": "Vague marketing prose with no structured specification table",
            "severity": "high",
            "confidence": 0.7,
            "evidence": f"{len(vague_no_table_pages)}/{len(pages)} sampled pages contain unquantified marketing "
                        f"phrases (e.g. 'engineered for', 'premium quality') with no <table> or <dl> spec block: "
                        f"{vague_no_table_pages[:5]}.",
            "affected_urls": vague_no_table_pages,
            "suggested_action": {
                "summary": "Publish a dedicated specifications section with an explicit key-value table for "
                           "every attribute a shopper or assistant might ask about; unstructured prose forces "
                           "the retrieval layer to guess.",
                "priority": "high",
                "expected_impact": "Replaces guesswork with a quotable, checkable fact for common attribute questions.",
            },
            "limitations": ["Vague-language detection is keyword-based; a page can use these phrases descriptively "
                            "without being ambiguous about its actual specs elsewhere. Spot-check a sample before treating as certain."],
        })

    if ambiguous_attr_pages:
        all_attrs = sorted({a for attrs in ambiguous_attr_pages.values() for a in attrs})
        findings.append({
            "title": "Boolean-style attributes mentioned without an explicit true/false fact",
            "severity": "high",
            "confidence": 0.65,
            "evidence": f"Attributes mentioned in free text without a clear boolean statement or matching "
                        f"JSON-LD additionalProperty on {len(ambiguous_attr_pages)} page(s): "
                        f"{dict(list(ambiguous_attr_pages.items())[:5])}.",
            "affected_urls": list(ambiguous_attr_pages.keys()),
            "suggested_action": {
                "summary": "State these explicitly as JSON-LD additionalProperty {name, value:true/false} pairs "
                           "(e.g. {\"name\":\"waterproof\",\"value\":false}) and in a visible spec table, so an "
                           "assistant quotes the real value instead of inferring one from adjacent marketing copy.",
                "priority": "high",
                "expected_impact": f"Removes {len(all_attrs)} specific hallucination-prone gap(s): {all_attrs}.",
            },
            "limitations": ["A 40-character text window around each mention is used to check for a nearby "
                            "yes/no/true/false; a genuinely explicit statement phrased unusually could still be "
                            "flagged as ambiguous. Verify a sample before publishing this finding externally."],
        })

    # Dedicated /specifications endpoint presence (single check at origin level, not per-page).
    found_spec_page = False
    checked_paths = []
    if fetch:
        for path in SPEC_PATHS:
            code, _, _, _ = fetch(origin + path)
            checked_paths.append(origin + path)
            if code == 200:
                found_spec_page = True
                break
    if not found_spec_page:
        findings.append({
            "title": "No dedicated machine-queryable specifications endpoint",
            "severity": "medium",
            "confidence": 0.75,
            "evidence": f"Checked {', '.join(checked_paths) if checked_paths else SPEC_PATHS}; none returned 200.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "Publish a dedicated /specifications page per product line with clear key-value "
                           "tables, and optionally a JSON/REST feed, so RAG agents can query deterministic "
                           "facts instead of extracting them from prose.",
                "priority": "medium",
                "expected_impact": "Gives retrieval systems one authoritative, easy-to-parse source per product line.",
            },
            "limitations": ["Only three conventional path names were checked; a site may expose specs under a "
                            "different, equally valid path this check would miss."],
        })

    # Entity disambiguation: an Organization/Brand entity exists in JSON-LD but
    # omits schema.org's own disambiguatingDescription property -- the field
    # that exists specifically to tell a consumer "this is the X that does Y,
    # not the unrelated thing sharing the name."
    org_pages_missing_disambiguation = []
    org_entity_found = False
    for p in pages:
        for blob in p.get("jsonld_blobs", []):
            try:
                data = json.loads(blob)
                items = data if isinstance(data, list) else [data]
            except Exception:
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type")
                types = t if isinstance(t, list) else [t]
                if any(str(tt).lower() in ("organization", "brand", "corporation", "localbusiness") for tt in types if tt):
                    org_entity_found = True
                    if "disambiguatingDescription" not in item:
                        org_pages_missing_disambiguation.append(p["url"])
    if org_pages_missing_disambiguation:
        findings.append({
            "title": "Organization/Brand entity markup omits disambiguatingDescription",
            "severity": "medium",
            "confidence": 0.75,
            "evidence": f"{len(org_pages_missing_disambiguation)} page(s) declare an Organization/Brand/"
                        f"LocalBusiness JSON-LD entity with no disambiguatingDescription property: "
                        f"{org_pages_missing_disambiguation[:5]}.",
            "affected_urls": org_pages_missing_disambiguation,
            "suggested_action": {
                "summary": "Add schema.org's disambiguatingDescription property to the Organization/Brand "
                           "entity -- a short phrase distinguishing this entity from any other thing sharing "
                           "its name (e.g. industry, location, or founding context). This is the property "
                           "schema.org defines specifically for the mistaken-identity problem, and it is "
                           "cheap to add wherever an Organization entity already exists.",
                "priority": "medium",
                "expected_impact": "Directly reduces the risk of an assistant conflating this brand with an unrelated entity of the same name.",
            },
            "limitations": [],
        })
    elif not org_entity_found:
        limitations.append("No Organization/Brand/LocalBusiness JSON-LD entity found in the sample at all; "
                            "entity-disambiguation check could not run (the absence of any recognized entity "
                            "type is already flagged separately by crawl-render-audit).")

    # Negative grounding: does the sampled content ever explicitly state what
    # is NOT true/included, or only ever state things in the positive? Total
    # silence on an attribute is fine (nothing to hallucinate against); the
    # risky pattern is attributes raised only positively, site-wide, with zero
    # explicit negations anywhere to anchor the boundary of what's claimed.
    any_negation = any(NEGATION_RE.search(strip_tags(p.get("html", ""))) for p in pages)
    total_ambiguous_attr_mentions = sum(len(v) for v in ambiguous_attr_pages.values())
    if not any_negation and total_ambiguous_attr_mentions > 0:
        findings.append({
            "title": "No explicit negative-grounding statements found anywhere in the sample",
            "severity": "low",
            "confidence": 0.5,
            "evidence": f"Zero pages in the {len(pages)}-page sample contain an explicit negation pattern "
                        f"(e.g. 'does not', 'not included', 'not compatible') despite {total_ambiguous_attr_mentions} "
                        f"ambiguous boolean-style attribute mention(s) detected elsewhere in this audit.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "For commonly-asked attributes, explicitly state both what IS and is NOT true "
                           "(e.g. 'not machine washable; hand wash only' rather than silence). A retrieval "
                           "system treats unstated attributes as unknown and may fill the gap with a "
                           "statistically plausible guess; an explicit negation removes the gap entirely.",
                "priority": "low",
                "expected_impact": "Closes the specific hallucination pathway where an unstated fact gets filled in incorrectly rather than left unknown.",
            },
            "limitations": ["Detected via a fixed negation-phrase list; a site could ground facts negatively "
                            "using different phrasing this check does not recognize."],
        })

    return findings, limitations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=None)
    ap.add_argument("--corpus", default=None)
    args = ap.parse_args()
    if not args.url and not args.corpus:
        print(json.dumps({"error": "usage: check.py <url> [--corpus <path>]"}))
        sys.exit(1)
    corpus = load_corpus(args)
    if "error" in corpus:
        print(json.dumps(corpus))
        sys.exit(1)
    findings, limitations = check(corpus)
    print(json.dumps({"findings": findings, "skill_limitations": limitations}, indent=2))


if __name__ == "__main__":
    main()
