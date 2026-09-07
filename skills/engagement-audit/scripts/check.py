#!/usr/bin/env python3
"""
engagement-audit / check.py

The second half of the Round-2 problem: once a visitor arrives (often from
an AI assistant that already told them something specific), can they orient
quickly, or are they dumped on a generic, high-friction page with no memory
of what they came for?

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "audit-orchestrator", "scripts"))
try:
    from crawl import build_corpus  # noqa: E402
except Exception:
    build_corpus = None


def load_corpus(args):
    if args.corpus:
        with open(args.corpus) as f:
            return json.load(f)
    if build_corpus is None:
        return {"error": "no --corpus given and crawl.py unavailable for standalone mode"}
    return build_corpus(args.url, max_pages=5)


def check(corpus):
    findings = []
    limitations = []
    site = corpus.get("site", "")
    pages = [p for p in corpus.get("pages", []) if p.get("status") == 200 and not p.get("fetch_error")]
    if not pages:
        limitations.append("No successfully-fetched pages in the corpus; engagement checks skipped.")
        return findings, limitations

    # 1. Deep-linkable, SEMANTIC section anchors -- not just any id, but one
    # whose name itself describes the section (so an assistant's generated
    # deep link is self-explanatory), versus generic framework-default ids.
    GENERIC_ID_RE = re.compile(r"^(section[-_]?\d*|div\d*|el\d*|root|app|main|container|wrapper|content\d*)$", re.I)
    SEMANTIC_ID_RE = re.compile(r"spec|price|pricing|review|faq|detail|shipping|warranty|return|ingredient|feature", re.I)
    pages_without_anchors, pages_generic_only = [], []
    for p in pages:
        ids = p.get("ids", [])
        semantic_ids = [i for i in ids if SEMANTIC_ID_RE.search(i)]
        generic_ids = [i for i in ids if GENERIC_ID_RE.match(i)]
        if not semantic_ids:
            pages_without_anchors.append(p["url"])
            if generic_ids:
                pages_generic_only.append(p["url"])
    if len(pages_without_anchors) == len(pages):
        findings.append({
            "title": "No semantically-named deep-linkable section anchors found on any sampled page",
            "severity": "medium",
            "confidence": 0.7,
            "evidence": f"{len(pages_without_anchors)}/{len(pages)} sampled pages have no id attribute whose "
                        f"name itself describes a specs/pricing/reviews/FAQ/shipping/warranty section"
                        f"{f'; {len(pages_generic_only)} use only generic framework-default ids '
                           f'(section-N, div, root, app)' if pages_generic_only else ''}.",
            "affected_urls": pages_without_anchors[:10],
            "suggested_action": {
                "summary": "Add stable, descriptively-named id anchors (e.g. #pricing, #shipping-returns, "
                           "#warranty) to key sections -- not just any id, but one whose name itself tells an "
                           "assistant what's there -- so a generated deep link is self-explanatory and a "
                           "visitor lands exactly where their question was already answered.",
                "priority": "medium",
                "expected_impact": "Reduces the re-orientation cost for a visitor who arrives with a specific question already answered.",
            },
            "limitations": ["Detected via id-attribute naming conventions against a fixed keyword list; a "
                            "page could offer equivalent anchors under differently-named ids this check would miss."],
        })

    # 2. Referrer/query-param-aware landing logic.
    has_referrer_logic_anywhere = any(
        re.search(r"document\.referrer|utm_source|utm_medium|ref=", p.get("html", "") or "", re.I) for p in pages
    )
    if not has_referrer_logic_anywhere:
        findings.append({
            "title": "No referrer- or query-param-aware landing logic detected",
            "severity": "low",
            "confidence": 0.5,
            "evidence": f"No reference to document.referrer or utm_*/ref= query handling found in the sampled "
                        f"pages' HTML/inline scripts.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "Parse incoming referrer headers or deep-link query parameters and surface a short "
                           "context summary of the product/spec/pricing the visitor likely just asked an "
                           "assistant about, reducing the need to re-search the site.",
                "priority": "low",
                "expected_impact": "Cuts the gap between what the AI told the visitor and what the page shows them first.",
            },
            "limitations": ["This logic may exist in an externally-loaded JS bundle not visible in inline HTML; "
                            "this check only inspects the raw page source, not linked scripts."],
        })

    # 3. High-friction interstitials, aggregated.
    interstitial_counts = {}
    for p in pages:
        hits = re.findall(
            r'class=["\'][^"\']*(modal|overlay|popup|interstitial|newsletter-signup|cookie-banner)[^"\']*["\']',
            p.get("html", "") or "", re.I)
        if len(hits) >= 2:
            interstitial_counts[p["url"]] = len(hits)
    if interstitial_counts:
        findings.append({
            "title": "Multiple interstitial/overlay patterns detected",
            "severity": "medium",
            "confidence": 0.55,
            "evidence": f"{len(interstitial_counts)}/{len(pages)} sampled pages match 2+ modal/overlay/popup/"
                        f"newsletter/cookie-banner class patterns: {interstitial_counts}.",
            "affected_urls": list(interstitial_counts.keys()),
            "suggested_action": {
                "summary": "Reduce or defer non-essential interstitials (newsletter modals, promo overlays) for "
                           "AI-referred sessions specifically, since these visitors already have intent and high "
                           "cognitive friction on arrival drives immediate bounces.",
                "priority": "medium",
                "expected_impact": "Removes friction at the exact moment a high-intent visitor lands.",
            },
            "limitations": ["Detected via CSS class-name pattern matching, which can both over- and "
                            "under-count actual interstitials depending on the site's naming conventions."],
        })

    # 4. Page weight as a friction proxy.
    heavy_pages = [(p["url"], len((p.get("html") or "").encode("utf-8")) / 1024) for p in pages]
    heavy_pages = [(u, kb) for u, kb in heavy_pages if kb > 700]
    if heavy_pages:
        findings.append({
            "title": "Very large HTML payload on one or more sampled pages",
            "severity": "low",
            "confidence": 0.6,
            "evidence": f"{len(heavy_pages)}/{len(pages)} sampled pages exceed 700 KB of raw HTML: "
                        f"{[(u, round(kb)) for u, kb in heavy_pages[:5]]}.",
            "affected_urls": [u for u, _ in heavy_pages],
            "suggested_action": {
                "summary": "Audit for redundant markup, inline scripts, or oversized promotional banners "
                           "bloating the DOM; heavier pages slow first paint and add friction for visitors "
                           "who arrive with a specific intent.",
                "priority": "low",
                "expected_impact": "Faster first paint for intent-driven, impatient AI-referred visitors.",
            },
            "limitations": [],
        })

    # 5. llms.txt presence.
    if not corpus.get("llms_txt", {}).get("found"):
        findings.append({
            "title": "No llms.txt guidance file",
            "severity": "low",
            "confidence": 0.9,
            "evidence": f"GET {corpus.get('origin','')}/llms.txt did not return 200.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "Publish an llms.txt at the site root summarizing key pages (specs, pricing, docs) "
                           "in plain Markdown links; several assistants consult it to decide where to route a "
                           "visitor and what to cite.",
                "priority": "low",
                "expected_impact": "Gives assistants a curated, low-noise map of the site's most important pages.",
            },
            "limitations": ["llms.txt is an emerging, not universally adopted, convention -- its absence is a "
                            "proactive opportunity, not evidence of a defect; do not weight this above concrete extraction failures."],
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
