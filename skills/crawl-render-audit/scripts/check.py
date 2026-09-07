#!/usr/bin/env python3
"""
crawl-render-audit / check.py

Off-site discoverability, gate 1+2 of Appendix A ("reach" and "read"): can a
non-rendering AI crawler (GPTBot, ClaudeBot, PerplexityBot, OAI-SearchBot --
none of which execute JavaScript) reach the page at all, and does the raw
HTML actually contain the content, or is it a blank client-side-rendering
shell?

Modes:
  --corpus <path>   Preferred: consume the orchestrator's shared, already-
                     robots.txt-filtered, multi-page corpus (no re-crawling).
  <url> only        Standalone/portable mode: builds its own small corpus by
                     importing crawl.py, so this skill still works if invoked
                     on its own outside the marketplace.

Optional --rendered-corpus <path>: a JSON object {url: rendered_visible_text}
that the INVOKING AGENT can supply if it has its own browser-rendering tool
(e.g. it rendered the page itself and extracted visible text). When given,
this performs a genuine raw-HTML-vs-rendered-DOM text diff per page instead
of the text-length/script-ratio heuristic, and reports the diff with much
higher confidence. This script never launches a browser itself -- it has no
such dependency -- it only consumes rendered text if the agent already has it.

Every finding includes a `confidence` (0-1) and `affected_urls` so the
orchestrator can merge/sort meaningfully across skills, and a `limitations`
list so heuristic checks are never presented with false certainty.

Usage:
    python3 check.py https://example.com
    python3 check.py https://example.com --corpus /tmp/corpus.json
"""
import sys
import os
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "audit-orchestrator", "scripts"))
try:
    from crawl import build_corpus  # noqa: E402
except Exception:
    build_corpus = None

CSR_TEXT_LEN_THRESHOLD = 500
CSR_SCRIPT_RATIO_THRESHOLD = 20
CLOAKING_LEN_DIFF_RATIO = 0.5  # bot vs human body length differs by more than 50%


def load_corpus(args):
    if args.corpus:
        with open(args.corpus) as f:
            return json.load(f)
    if build_corpus is None:
        return {"error": "no --corpus given and crawl.py unavailable for standalone mode"}
    return build_corpus(args.url, max_pages=5)


def load_rendered_corpus(path):
    if not path:
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def check(corpus, rendered_corpus=None):
    findings = []
    limitations = []
    site = corpus.get("site", "")
    pages = corpus.get("pages", [])
    robots = corpus.get("robots", {})
    rendered_corpus = rendered_corpus or {}

    # 1. robots.txt: any major AI crawler explicitly disallowed?
    if robots.get("blocked_ai_bots"):
        findings.append({
            "title": "robots.txt blocks AI assistant crawlers",
            "severity": "critical",
            "confidence": 0.98,
            "evidence": f"robots.txt at {corpus.get('origin','')}/robots.txt disallows: "
                        f"{', '.join(robots['blocked_ai_bots'])} for the site root.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "Remove Disallow rules for AI crawler user-agents (or scope them away "
                           "from product/category pages) -- this is gate 1 of reach/read/extract; "
                           "nothing downstream matters if a bot can't get in.",
                "priority": "critical",
                "expected_impact": "Restores basic eligibility to be crawled and cited at all by the blocked assistant(s).",
            },
            "limitations": [],
        })
    elif not robots.get("fetched"):
        limitations.append("robots.txt was not fetched successfully; crawl-permission findings for AI bots are based on default-allow and may miss an actual block.")

    # 2. Pages unreachable / non-200 to a plain HTTP fetch (split network vs HTTP error).
    unreachable, http_errors = [], []
    for p in pages:
        if p.get("fetch_error"):
            unreachable.append(p["url"])
        elif p.get("status") != 200:
            http_errors.append((p["url"], p.get("status")))

    if unreachable:
        findings.append({
            "title": "Some sampled pages failed at the network/transport level",
            "severity": "medium",
            "confidence": 0.6,
            "evidence": f"{len(unreachable)}/{len(pages)} sampled URLs raised a connection/timeout error "
                        f"before any HTTP response was received: {unreachable[:5]}.",
            "affected_urls": unreachable,
            "suggested_action": {
                "summary": "Investigate DNS, TLS, or timeout issues for these paths; a transport failure "
                           "(as opposed to an HTTP error code) often indicates an infrastructure problem "
                           "rather than a deliberate block, but a crawler sees the same outcome either way: nothing.",
                "priority": "medium",
                "expected_impact": "Fixing transport-level failures restores baseline reachability for those paths.",
            },
            "limitations": ["Could not distinguish a transient sandbox/network issue from a persistent site-side outage from a single pass; re-check from a second network path before treating as confirmed."],
        })
    if http_errors:
        findings.append({
            "title": "Some sampled pages returned a non-200 HTTP status",
            "severity": "high",
            "confidence": 0.85,
            "evidence": f"{len(http_errors)}/{len(pages)} sampled URLs returned non-200: "
                        f"{[(u, s) for u, s in http_errors[:5]]}.",
            "affected_urls": [u for u, _ in http_errors],
            "suggested_action": {
                "summary": "Fix broken/blocked routes (404s, unexpected 403/429s). A recurring 403 across "
                           "many paths is also consistent with bot-detection middleware silently filtering "
                           "non-browser clients -- check WAF/CDN bot-management rules specifically for search/AI UAs.",
                "priority": "high",
                "expected_impact": "Restores content availability to any crawler using a plain HTTP GET.",
            },
            "limitations": [],
        })

    # 3a. Real raw-vs-rendered diff, only for pages the invoking agent supplied
    # rendered text for -- this is the genuine version of the check.
    real_diff_pages, verified_ok_pages = [], []
    for p in pages:
        rendered_text = rendered_corpus.get(p["url"])
        if rendered_text is None or p.get("fetch_error") or p.get("status") != 200:
            continue
        raw_text_len = p.get("text_len", 0)
        rendered_len = len(rendered_text.strip())
        if rendered_len > 0 and raw_text_len < 0.3 * rendered_len and (rendered_len - raw_text_len) > 200:
            real_diff_pages.append((p["url"], raw_text_len, rendered_len))
        else:
            verified_ok_pages.append(p["url"])
    if real_diff_pages:
        findings.append({
            "title": "Confirmed: raw HTML omits content present after JavaScript rendering",
            "severity": "critical",
            "confidence": 0.95,
            "evidence": f"Diffed raw HTML text length against agent-supplied rendered-DOM text for "
                        f"{len(real_diff_pages)} page(s); raw HTML contains under 30% of the rendered text "
                        f"and is missing 200+ characters of content: "
                        f"{[(u, rl, dl) for u, rl, dl in real_diff_pages[:5]]}.",
            "affected_urls": [u for u, _, _ in real_diff_pages],
            "suggested_action": {
                "summary": "This is a confirmed (not heuristic) client-side-rendering gap: server-side render "
                           "or statically pre-render these pages so the missing content exists in the initial "
                           "HTML response, which is all a non-rendering AI crawler ever sees.",
                "priority": "critical",
                "expected_impact": "Makes content already visible to human visitors visible to crawlers too -- confirmed via direct comparison, not inference.",
            },
            "limitations": [],
        })

    # 3b. Client-side-rendering gap heuristic, for pages with no rendered
    # comparison available (the common case without a browser tool).
    csr_blank_pages = []
    already_verified = {u for u, _, _ in real_diff_pages} | set(verified_ok_pages)
    for p in pages:
        if p.get("fetch_error") or p.get("status") != 200 or p["url"] in already_verified:
            continue
        text_len, script_len = p.get("text_len", 0), p.get("script_len", 0)
        ratio = script_len / max(text_len, 1)
        if text_len < CSR_TEXT_LEN_THRESHOLD and (p.get("root_like_divs", 0) > 0 or ratio > CSR_SCRIPT_RATIO_THRESHOLD):
            csr_blank_pages.append(p["url"])
    checkable = [p for p in pages if not p.get("fetch_error") and p.get("status") == 200]
    if csr_blank_pages:
        findings.append({
            "title": "Page content appears client-side rendered; raw HTML is effectively blank",
            "severity": "critical",
            "confidence": 0.75,
            "evidence": f"{len(csr_blank_pages)}/{len(checkable)} sampled pages have <{CSR_TEXT_LEN_THRESHOLD} chars "
                        f"of visible text in the raw HTML alongside a heavy script payload and/or an empty "
                        f"#root/#app/#__next mount div: {csr_blank_pages[:5]}.",
            "affected_urls": csr_blank_pages,
            "suggested_action": {
                "summary": "Move key facts (specs, pricing, category text) to server-side rendering or static "
                           "pre-rendering so they exist in the initial HTML payload, not only after client-side "
                           "JS executes. Non-rendering crawlers never run that JS.",
                "priority": "critical",
                "expected_impact": "Makes the page's real content visible to GPTBot/ClaudeBot/PerplexityBot for the first time.",
            },
            "limitations": ["Detected via a text-length/script-ratio heuristic, not a real headless-browser render comparison. "
                            "If a browser-rendering tool is available to the invoking agent, re-verify by diffing rendered-DOM "
                            "text against this raw HTML before treating as fully confirmed."],
        })

    # 4. UA-based cloaking: does the homepage differ materially between a browser UA and a bot UA?
    cloak = corpus.get("homepage_cloaking_check", {})
    h_len, b_len = cloak.get("human_len", 0), cloak.get("bot_len", 0)
    if cloak.get("human_status") == 200 and cloak.get("bot_status") is not None and h_len > 0:
        diff_ratio = abs(h_len - b_len) / h_len
        if cloak.get("bot_status") != 200 or diff_ratio > CLOAKING_LEN_DIFF_RATIO:
            findings.append({
                "title": "Homepage response differs materially by User-Agent (possible cloaking to AI bots)",
                "severity": "high",
                "confidence": 0.55,
                "evidence": f"Homepage fetched with a standard UA returned status {cloak.get('human_status')} "
                            f"and {h_len} bytes; fetched with a GPTBot UA it returned status {cloak.get('bot_status')} "
                            f"and {b_len} bytes ({diff_ratio:.0%} length difference).",
                "affected_urls": [site],
                "suggested_action": {
                    "summary": "Confirm WAF/CDN/bot-management rules are not serving a stripped-down, blocked, "
                               "or different page specifically to AI/search bot user-agents; unintentional "
                               "differential treatment can silently exclude a brand from citation even when "
                               "the human-facing site is fine.",
                    "priority": "high",
                    "expected_impact": "Ensures crawlers see the same content a human visitor sees.",
                },
                "limitations": ["A length difference can also come from A/B testing, personalization, or geofencing "
                                "unrelated to the User-Agent; confirm with a header/body diff before concluding intentional cloaking."],
            })

    # 5. JSON-LD presence/validity, aggregated across the sample.
    pages_with_ld, pages_with_recognized_type, invalid_ld_pages = [], [], []
    recognized_types = {"Product", "Organization", "Brand", "Article", "FAQPage", "WebPage", "BreadcrumbList"}
    for p in pages:
        blobs = p.get("jsonld_blobs", [])
        if not blobs:
            continue
        pages_with_ld.append(p["url"])
        types_found = set()
        page_invalid = False
        for blob in blobs:
            try:
                data = json.loads(blob)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    t = item.get("@type") if isinstance(item, dict) else None
                    if t:
                        types_found.update(t if isinstance(t, list) else [t])
            except Exception:
                page_invalid = True
        if page_invalid:
            invalid_ld_pages.append(p["url"])
        if types_found & recognized_types:
            pages_with_recognized_type.append(p["url"])

    if not pages_with_ld:
        findings.append({
            "title": "No JSON-LD structured data found on any sampled page",
            "severity": "high",
            "confidence": 0.9,
            "evidence": f"Scanned {len(checkable)} pages ({[p['url'] for p in checkable][:5]}); "
                        f"found 0 with a <script type=\"application/ld+json\"> block.",
            "affected_urls": [p["url"] for p in checkable],
            "suggested_action": {
                "summary": "Add Product/Brand/Offer (or Organization/Article, per page type) JSON-LD to give "
                           "retrieval engines an explicit entity definition instead of inferring one from prose.",
                "priority": "high",
                "expected_impact": "Gives retrieval systems a structured, high-confidence entity to cite directly.",
            },
            "limitations": [],
        })
    elif len(pages_with_recognized_type) < len(pages_with_ld):
        unrecognized = [u for u in pages_with_ld if u not in pages_with_recognized_type]
        findings.append({
            "title": "JSON-LD present but some pages use no recognized schema.org entity type",
            "severity": "medium",
            "confidence": 0.8,
            "evidence": f"{len(unrecognized)}/{len(pages_with_ld)} pages with JSON-LD declare no @type from "
                        f"the core schema.org vocabulary (Product/Organization/Brand/Article/etc.): {unrecognized[:5]}.",
            "affected_urls": unrecognized,
            "suggested_action": {
                "summary": "Use a standard schema.org @type rather than a custom or missing one so vector "
                           "retrieval engines can map the entity to intent queries.",
                "priority": "medium",
                "expected_impact": "Makes existing structured data actually usable by type-aware retrieval.",
            },
            "limitations": [],
        })
    if invalid_ld_pages:
        findings.append({
            "title": "Malformed JSON-LD block(s) found",
            "severity": "medium",
            "confidence": 0.9,
            "evidence": f"{len(invalid_ld_pages)} page(s) contain a JSON-LD <script> block that failed to parse as valid JSON: {invalid_ld_pages[:5]}.",
            "affected_urls": invalid_ld_pages,
            "suggested_action": {
                "summary": "Validate JSON-LD with Google's Rich Results Test or a schema.org validator in CI; "
                           "a syntax error silently drops the entire structured-data block for a crawler.",
                "priority": "medium",
                "expected_impact": "Recovers structured-data visibility that currently silently fails to parse.",
            },
            "limitations": [],
        })

    # 6. Sitemap presence.
    sitemap = corpus.get("sitemap", {})
    if sitemap.get("status") != 200:
        findings.append({
            "title": "No XML sitemap found at declared or conventional location",
            "severity": "medium",
            "confidence": 0.85,
            "evidence": f"Checked {sitemap.get('url', '/sitemap.xml')}; status={sitemap.get('status')}.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "Publish an XML sitemap and reference it in robots.txt so crawlers can discover "
                           "the full URL set without relying on internal link-following alone.",
                "priority": "medium",
                "expected_impact": "Improves discovery completeness, especially for pages with few internal links.",
            },
            "limitations": ["A missing sitemap is a much smaller concern on a genuinely small, fully "
                            "link-connected site; weight this lower if the sampled page count is small."],
        })

    return findings, limitations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=None)
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--rendered-corpus", default=None,
                     help="Optional JSON {url: rendered_visible_text} from the invoking agent's own browser tool.")
    args = ap.parse_args()
    if not args.url and not args.corpus:
        print(json.dumps({"error": "usage: check.py <url> [--corpus <path>] [--rendered-corpus <path>]"}))
        sys.exit(1)
    corpus = load_corpus(args)
    if "error" in corpus:
        print(json.dumps(corpus))
        sys.exit(1)
    rendered_corpus = load_rendered_corpus(args.rendered_corpus)
    findings, limitations = check(corpus, rendered_corpus=rendered_corpus)
    print(json.dumps({"findings": findings, "skill_limitations": limitations}, indent=2))


if __name__ == "__main__":
    main()
