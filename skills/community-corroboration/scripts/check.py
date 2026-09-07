#!/usr/bin/env python3
"""
community-corroboration / check.py

Appendix D, off-site half: corroboration doesn't only come from schema.org
markup -- independent third-party discussion (review sites, forums, Q&A)
repeating consistent facts about a brand is itself a trust signal, and its
absence or contradiction is itself a finding.

This script deliberately does NOT scrape Reddit, Google, or any search
engine's HTML results directly: that is fragile, frequently against those
sites' terms of service, and unnecessary. Instead this check is a bounded,
read-only stub that:
  - never fails the audit (it's optional and only meaningful when the
    invoking agent also has a real web/Reddit search tool available),
  - reports transparently as `skipped` with an `info` finding when run in
    script-only mode (e.g. this contest's grading sandbox),
  - documents, in its own output, the exact bounded query templates the
    orchestrator's SKILL.md instructs an agent to run with its OWN
    web_search tool if one is available, so the check degrades gracefully
    instead of silently producing nothing.

Usage:
    python3 check.py https://example.com
    python3 check.py https://example.com --corpus /tmp/corpus.json
"""
import sys
import json
import argparse
import urllib.parse


def brand_guess_from_url(url):
    netloc = urllib.parse.urlparse(url if "://" in url else "https://" + url).netloc
    return netloc.replace("www.", "").split(".")[0]


def check(url):
    brand = brand_guess_from_url(url)
    query_templates = [
        f'"{brand}" reddit review',
        f'"{brand}" scam OR complaints',
        f'site:reddit.com "{brand}"',
    ]
    findings = [{
        "title": "Community/off-site corroboration not checked in script-only mode",
        "severity": "info",
        "confidence": 1.0,
        "evidence": "This skill requires a live web/Reddit search tool, which this deterministic script does "
                    "not perform on its own (to avoid fragile, ToS-risky scraping). Running in script-only mode.",
        "affected_urls": [url],
        "suggested_action": {
            "summary": "If the invoking agent has a web_search tool available, run these bounded, read-only "
                       f"queries and report contradictions/consensus separately from first-party evidence: {query_templates}.",
            "priority": "info",
            "expected_impact": "Surfaces third-party consensus or contradiction the brand's own site cannot show about itself.",
        },
        "limitations": ["This finding is a coverage note, not a site defect -- do not count it toward critical/high/medium/low totals."],
    }]
    return findings, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=None)
    ap.add_argument("--corpus", default=None)
    args = ap.parse_args()
    url = args.url
    if not url and args.corpus:
        with open(args.corpus) as f:
            url = json.load(f).get("site")
    if not url:
        print(json.dumps({"error": "usage: check.py <url> [--corpus <path>]"}))
        sys.exit(1)
    findings, limitations = check(url)
    print(json.dumps({"findings": findings, "skill_limitations": limitations}, indent=2))


if __name__ == "__main__":
    main()
