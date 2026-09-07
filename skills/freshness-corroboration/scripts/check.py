#!/usr/bin/env python3
"""
freshness-corroboration / check.py

Appendix D: machines trust a fact more when it's repeated consistently
across independent sources, and can confuse entities that share a name
unless something disambiguates them. Also checks whether a page pushes
freshness signals letting a crawler know a fact has been superseded
(a stale fact repeated for a year beats a corrected one from last week
unless something says otherwise).

Modes: --corpus <path> (preferred, reuses the shared crawl) or a bare <url>
(standalone mode, builds its own small corpus).

Usage:
    python3 check.py https://example.com
    python3 check.py https://example.com --corpus /tmp/corpus.json
"""
import sys
import os
import re
import json
import datetime
import argparse
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "audit-orchestrator", "scripts"))
try:
    from crawl import build_corpus  # noqa: E402
except Exception:
    build_corpus = None
try:
    from geo_signals import content_quality_signals  # noqa: E402
except Exception:
    content_quality_signals = None

STALE_DAYS = 400  # >~13 months since lastmod treated as a freshness risk
CANONICAL_HOSTS = ("wikidata.org", "wikipedia.org", "crunchbase.com", "linkedin.com/company")


def load_corpus(args):
    if args.corpus:
        with open(args.corpus) as f:
            return json.load(f)
    if build_corpus is None:
        return {"error": "no --corpus given and crawl.py unavailable for standalone mode"}
    return build_corpus(args.url, max_pages=5)


def extract_ld(page):
    out = []
    for blob in page.get("jsonld_blobs", []):
        try:
            data = json.loads(blob)
            items = data if isinstance(data, list) else [data]
            out.extend([i for i in items if isinstance(i, dict)])
        except Exception:
            continue
    return out


def check(corpus):
    findings = []
    limitations = []
    site = corpus.get("site", "")
    pages = [p for p in corpus.get("pages", []) if p.get("status") == 200 and not p.get("fetch_error")]
    if not pages:
        limitations.append("No successfully-fetched pages in the corpus; freshness/corroboration checks skipped.")
        return findings, limitations

    # 1. HTTP-level freshness headers, aggregated.
    no_freshness_headers = [p["url"] for p in pages
                             if "Last-Modified" not in p.get("headers", {}) and "ETag" not in p.get("headers", {})]
    if len(no_freshness_headers) == len(pages):
        findings.append({
            "title": "No HTTP freshness headers (Last-Modified / ETag) on any sampled page",
            "severity": "medium",
            "confidence": 0.85,
            "evidence": f"{len(no_freshness_headers)}/{len(pages)} sampled responses contain neither "
                        f"Last-Modified nor ETag.",
            "affected_urls": no_freshness_headers[:10],
            "suggested_action": {
                "summary": "Emit Last-Modified and/or ETag headers so crawlers and caches can detect changes "
                           "without a full re-fetch, and re-index promptly when content changes.",
                "priority": "medium",
                "expected_impact": "Lets crawlers prioritize re-fetching genuinely changed pages.",
            },
            "limitations": [],
        })

    # 2. Page-level freshness (dateModified in JSON-LD) and entity corroboration (sameAs).
    pages_missing_date_modified = []
    all_sameas = []
    for p in pages:
        ld_items = extract_ld(p)
        has_dm = any("dateModified" in item for item in ld_items)
        if not has_dm:
            pages_missing_date_modified.append(p["url"])
        for item in ld_items:
            sa = item.get("sameAs")
            if sa:
                all_sameas.extend(sa if isinstance(sa, list) else [sa])

    has_any_jsonld = any(p.get("jsonld_blobs") for p in pages)
    if has_any_jsonld and len(pages_missing_date_modified) == len(pages):
        findings.append({
            "title": "No dateModified found in structured data on any sampled page",
            "severity": "high",
            "confidence": 0.75,
            "evidence": f"{len(pages_missing_date_modified)}/{len(pages)} sampled pages have JSON-LD but none "
                        f"declare a dateModified property.",
            "affected_urls": pages_missing_date_modified[:10],
            "suggested_action": {
                "summary": "Add dateModified (and datePublished) to Product/Article JSON-LD, and push updates "
                           "via the IndexNow protocol when it changes, so consensus-weighting models can prefer "
                           "recent facts over an older, more-frequently-repeated version.",
                "priority": "high",
                "expected_impact": "Directly counters the 'most-repeated wins over most-recent' failure mode.",
            },
            "limitations": [],
        })
    elif not has_any_jsonld:
        limitations.append("No JSON-LD found on sampled pages at all; dateModified/sameAs checks could not run "
                            "(the crawl-render-audit skill already flags the absence of JSON-LD itself).")

    if all_sameas:
        has_canonical = any(any(h in s for h in CANONICAL_HOSTS) for s in all_sameas)
        if not has_canonical:
            findings.append({
                "title": "sameAs links exist but omit high-authority corroboration sources",
                "severity": "low",
                "confidence": 0.7,
                "evidence": f"sameAs links found ({list(dict.fromkeys(all_sameas))[:5]}) but none point to "
                            f"Wikidata, Wikipedia, or Crunchbase.",
                "affected_urls": [site],
                "suggested_action": {
                    "summary": "Add a Wikidata and/or Wikipedia sameAs entry specifically -- these are "
                               "disproportionately weighted as ground truth by consensus-based retrieval systems.",
                    "priority": "low",
                    "expected_impact": "Strengthens third-party corroboration, which drives ~85% of AI brand citations per industry GEO research, more than first-party pages alone.",
                },
                "limitations": [],
            })
    elif has_any_jsonld:
        findings.append({
            "title": "No sameAs entity links to external corroborating sources",
            "severity": "medium",
            "confidence": 0.75,
            "evidence": f"No sameAs property found in any JSON-LD across {len(pages)} sampled pages.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "Add a sameAs array pointing to Wikidata/Wikipedia/Crunchbase/official social "
                           "profiles so a system that sees the brand mentioned elsewhere can disambiguate it "
                           "from unrelated entities sharing the same name, and cross-source agreement raises "
                           "trust in the facts stated here.",
                "priority": "medium",
                "expected_impact": "Reduces entity-confusion risk and raises trust weighting for this brand's own facts.",
            },
            "limitations": [],
        })

    # 3. Sitemap lastmod staleness.
    sitemap = corpus.get("sitemap", {})
    entries = sitemap.get("entries", [])
    lastmods = [e["lastmod"] for e in entries if e.get("lastmod")]
    if entries and not lastmods:
        findings.append({
            "title": "Sitemap present but has no <lastmod> dates",
            "severity": "medium",
            "confidence": 0.9,
            "evidence": f"{sitemap.get('url')} contains {len(entries)} URL entries with no <lastmod> tags.",
            "affected_urls": [site],
            "suggested_action": {
                "summary": "Add accurate <lastmod> timestamps to every sitemap entry so crawlers can "
                           "prioritize re-fetching changed pages instead of relying on stale caches.",
                "priority": "medium",
                "expected_impact": "Lets crawlers distinguish genuinely updated pages from unchanged ones.",
            },
            "limitations": [],
        })
    elif lastmods:
        today = datetime.datetime.now(datetime.timezone.utc)
        stale = 0
        for lm in lastmods:
            try:
                d = datetime.datetime.fromisoformat(lm.replace("Z", "+00:00"))
                if (today - d).days > STALE_DAYS:
                    stale += 1
            except Exception:
                continue
        if stale / len(lastmods) > 0.5:
            findings.append({
                "title": "Majority of sitemap URLs have stale lastmod dates",
                "severity": "medium",
                "confidence": 0.8,
                "evidence": f"{stale}/{len(lastmods)} sitemap entries have <lastmod> older than {STALE_DAYS} days.",
                "affected_urls": [site],
                "suggested_action": {
                    "summary": "Regenerate lastmod on real content changes and submit updated URLs via "
                               "IndexNow so freshness signals reflect the current state of the site, not a "
                               "one-time sitemap generation.",
                    "priority": "medium",
                    "expected_impact": "Restores crawler trust that lastmod actually reflects real changes.",
                },
                "limitations": [],
            })

    # 4. GEO-paper-grounded content-quality signals (Aggarwal et al., KDD'24):
    # citations/quotations/statistics measured to raise generative-engine
    # visibility substantially; keyword-repetition padding measured to not.
    if content_quality_signals:
        origin = corpus.get("origin", "")
        netloc = urllib.parse.urlparse(origin).netloc if origin else None
        low_substance_pages = []
        stuffed_pages = []
        agg_stats = agg_quotes = agg_citations = 0
        for p in pages:
            sig = content_quality_signals(p.get("html", ""), origin_netloc=netloc)
            agg_stats += sig["stat_mentions"]
            agg_quotes += sig["quote_or_attribution_mentions"]
            agg_citations += sig["external_citation_links"]
            if sig["word_count"] > 150 and sig["stat_mentions"] == 0 and sig["quote_or_attribution_mentions"] == 0 and sig["external_citation_links"] == 0:
                low_substance_pages.append(p["url"])
            if sig["max_single_word_repeat_ratio"] > 0.06 and sig["stat_mentions"] == 0 and sig["quote_or_attribution_mentions"] == 0:
                stuffed_pages.append((p["url"], sig["top_repeated_words"][0][0], sig["max_single_word_repeat_ratio"]))

        if low_substance_pages:
            findings.append({
                "title": "Substantial pages contain no statistics, quotations, or external citations",
                "severity": "high",
                "confidence": 0.7,
                "evidence": f"{len(low_substance_pages)}/{len(pages)} sampled pages have 150+ words of body "
                            f"text but zero detected statistic mentions, zero quoted/attributed statements, "
                            f"and zero links to external sources: {low_substance_pages[:5]}.",
                "affected_urls": low_substance_pages,
                "suggested_action": {
                    "summary": "Add concrete statistics, direct quotations from credible sources, and "
                               "citation links to independent sources within the page's own prose. Published "
                               "GEO research (Aggarwal et al., KDD'24) measured these three additions as the "
                               "strongest levers for a source's visibility inside generative-engine responses, "
                               "with reported relative gains in the 20-40% range on their benchmark -- "
                               "substantially larger than stylistic changes alone.",
                    "priority": "high",
                    "expected_impact": "Directly targets the content property the published research found "
                                       "most predictive of generative-engine citation and visibility.",
                },
                "limitations": ["Detected via regex pattern-matching for numeric/percentage statistics, "
                                "quotation marks, attribution phrases, and outbound links -- a page could "
                                "state facts in a form this pattern-matching misses, or could already meet "
                                "this bar in content not present in the sampled HTML (e.g. behind pagination)."],
            })

        if stuffed_pages:
            findings.append({
                "title": "Keyword-repetition pattern detected without corroborating statistics/citations",
                "severity": "low",
                "confidence": 0.55,
                "evidence": f"{len(stuffed_pages)} sampled page(s) show a single word repeated at high "
                            f"density with no accompanying statistics or quotations: "
                            f"{[(u, w, round(r, 3)) for u, w, r in stuffed_pages[:5]]}.",
                "affected_urls": [u for u, _, _ in stuffed_pages],
                "suggested_action": {
                    "summary": "Replace repeated-keyword padding with substantive additions instead: "
                               "published GEO research (Aggarwal et al., KDD'24) specifically measured "
                               "keyword-stuffing-style changes as providing little to no generative-engine "
                               "visibility benefit -- in one live test on a deployed generative engine it "
                               "underperformed making no change at all -- while statistics, citations, and "
                               "quotations measurably outperformed it on the same benchmark.",
                    "priority": "low",
                    "expected_impact": "Redirects effort away from a technique the published research found "
                                       "ineffective, toward the ones it found effective.",
                },
                "limitations": ["High single-word repetition can also be a legitimate brand/product name "
                                "appearing frequently and is not itself a defect -- treat as a prompt to add "
                                "substance alongside the repeated term, not to remove the term."],
            })

        # Proactive, domain-aware GEO method recommendation -- beyond any defect found.
        try:
            from geo_signals import classify_domain, recommended_geo_methods
            homepage_html = pages[0].get("html", "") if pages else ""
            domain, _ = classify_domain(homepage_html)
            methods = recommended_geo_methods(domain)
            method_str = "; ".join(f"{m} (~{r} reported relative improvement)" for m, r in methods)
            domain_label = domain.replace("_", " ") if domain else None
            title_suffix = f" ({domain_label})" if domain_label else ""
            findings.append({
                "title": f"Proactive GEO content strategy for detected content domain{title_suffix}",
                "severity": "info",
                "confidence": 0.4,
                "evidence": f"Homepage content keyword-matched most closely to the "
                            f"'{domain.replace('_', ' ') if domain else 'unclassified'}' category used in "
                            f"published GEO benchmark tagging; that category's top-performing content "
                            f"strategies in the published research were: {method_str}.",
                "affected_urls": [corpus.get("site", "")],
                "suggested_action": {
                    "summary": f"Prioritize {methods[0][0]} as the next content investment for this site's "
                               f"apparent subject matter, per the domain-specific breakdown in published GEO "
                               f"research (Aggarwal et al., KDD'24, Table 3) -- effectiveness of these "
                               f"strategies was measured to vary by content domain, so a generic strategy is "
                               f"less reliable than one matched to this domain.",
                    "priority": "low",
                    "expected_impact": "A domain-matched content strategy, per the published research, "
                                       "rather than a one-size-fits-all recommendation.",
                },
                "limitations": ["Domain classification here is a simple keyword-bucket heuristic on the "
                                "homepage only, not a validated topic classifier; treat this as a starting "
                                "hypothesis to confirm manually, not a certain categorization."],
            })
        except Exception:
            pass

        limitations.append(f"Aggregate content-quality signal across sample: {agg_stats} statistic mention(s), "
                            f"{agg_quotes} quotation/attribution mention(s), {agg_citations} external citation "
                            f"link(s) detected -- provided as diagnostic context alongside the findings above.")

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
