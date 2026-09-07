#!/usr/bin/env python3
"""
audit-orchestrator / compose.py

The entrypoint's deterministic composer.

1. Builds ONE shared, robots.txt-respecting, multi-page corpus (crawl.py).
2. Runs each mandatory sub-skill's check.py against that corpus as an
   isolated read-only subprocess (own timeout, own error handling -- one
   sub-skill failing doesn't crash the audit; it's surfaced transparently).
3. Runs the optional community-corroboration skill only if --community is
   passed (it never fails the audit; see that skill's own docstring).
4. Merges findings: sorts by severity then confidence, assigns sequential
   ids, computes the severity summary (info findings are reported but never
   counted in the required critical/high/medium/low totals).
5. Emits the final JSON report to stdout, matching the contest's minimum
   schema plus documented extras (confidence, affected_urls, limitations,
   source_skill, coverage/diagnostics).

This script never modifies the target site: every sub-skill only performs
GET requests, and the corpus is built exactly once and reused.

Usage:
    python3 compose.py https://example.com
    python3 compose.py https://example.com --community
    python3 compose.py https://example.com --max-pages 8
"""
import sys
import os
import json
import subprocess
import tempfile
import datetime
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl import build_corpus  # noqa: E402
from geo_signals import classify_domain, recommended_geo_methods, subjective_impression_proxy, strip_tags  # noqa: E402

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

MANDATORY_SUB_SKILLS = [
    "crawl-render-audit",
    "freshness-corroboration",
    "spec-hallucination-audit",
    "engagement-audit",
]
OPTIONAL_SUB_SKILLS = ["community-corroboration"]

PER_SUBSKILL_TIMEOUT = 60


def run_subskill(skill_id, corpus_path, marketplace_root, url, rendered_corpus_path=None):
    script = marketplace_root / "skills" / skill_id / "scripts" / "check.py"
    cmd = [sys.executable, str(script), url, "--corpus", str(corpus_path)]
    if skill_id == "crawl-render-audit" and rendered_corpus_path:
        cmd += ["--rendered-corpus", str(rendered_corpus_path)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PER_SUBSKILL_TIMEOUT
        )
        if result.returncode != 0:
            return [], [], f"{skill_id}: non-zero exit ({result.stderr.strip()[:300]})"
        data = json.loads(result.stdout)
        if isinstance(data, dict) and "error" in data:
            return [], [], f"{skill_id}: {data['error']}"
        return data.get("findings", []), data.get("skill_limitations", []), None
    except subprocess.TimeoutExpired:
        return [], [], f"{skill_id}: timed out after {PER_SUBSKILL_TIMEOUT}s"
    except Exception as e:
        return [], [], f"{skill_id}: {e}"


def merge_findings(all_findings):
    """Merge findings that share the same (source_skill, title): union their
    affected_urls and evidence rather than emitting duplicates. Distinct
    titles from the same skill are kept distinct -- this only collapses
    true duplicates, never genuinely different problems."""
    merged = {}
    for f in all_findings:
        key = (f.get("source_skill"), f.get("title"))
        if key not in merged:
            merged[key] = f
        else:
            existing = merged[key]
            existing_urls = set(existing.get("affected_urls", []))
            new_urls = set(f.get("affected_urls", []))
            existing["affected_urls"] = sorted(existing_urls | new_urls)
    return list(merged.values())


def compose(url, max_pages=10, include_community=False, rendered_corpus_path=None):
    marketplace_root = Path(__file__).resolve().parents[3]
    tmp_corpus = None
    try:
        corpus = build_corpus(url, max_pages=max_pages)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(corpus, tf)
            tmp_corpus = tf.name

        all_findings, errors, all_limitations = [], [], []
        skills_to_run = list(MANDATORY_SUB_SKILLS)
        if include_community:
            skills_to_run += OPTIONAL_SUB_SKILLS

        for skill_id in skills_to_run:
            findings, limitations, err = run_subskill(skill_id, tmp_corpus, marketplace_root, corpus["site"],
                                                        rendered_corpus_path=rendered_corpus_path)
            for f in findings:
                f["source_skill"] = skill_id
            all_findings.extend(findings)
            all_limitations.extend(f"{skill_id}: {l}" for l in limitations)
            if err:
                errors.append(err)

        all_findings = merge_findings(all_findings)
        all_findings.sort(key=lambda f: (
            SEVERITY_ORDER.get(f.get("severity", "low"), 5),
            -f.get("confidence", 0.5),
        ))
        for i, f in enumerate(all_findings, start=1):
            f["id"] = f"F-{i:03d}"

        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        info_count = 0
        for f in all_findings:
            sev = f.get("severity", "low")
            if sev == "info":
                info_count += 1
            elif sev in counts:
                counts[sev] += 1

        # Proactive, published-research-grounded strategy block: domain guess
        # + GEO paper's (Aggarwal et al., KDD'24) top methods for that domain,
        # plus a heuristic proxy for the paper's four Subjective Impression
        # sub-metrics. This is presented independent of whether any defect
        # was found -- it is the "beyond the problems detected" half of the
        # brief, not itself a finding.
        pages = corpus.get("pages", [])
        homepage_html = pages[0].get("html", "") if pages else ""
        other_pages_text = [strip_tags(p.get("html", "")) for p in pages[1:]]
        domain, _ = classify_domain(homepage_html)
        geo_content_strategy = {
            "detected_domain": domain,
            "recommended_geo_methods": [
                {"method": m, "reported_relative_improvement": r} for m, r in recommended_geo_methods(domain)
            ],
            "subjective_impression_proxy": subjective_impression_proxy(homepage_html, other_pages_text) if pages else None,
            "source": "Aggarwal et al., 'GEO: Generative Engine Optimization', KDD'24 (arXiv:2311.09735) -- "
                       "domain-to-method mapping and reported improvement ranges are the published paper's "
                       "own aggregate benchmark results, not a measurement of this specific site.",
        }

        report = {
            "site": corpus["site"],
            "audited_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "summary": {
                "total_findings": len(all_findings) - info_count,
                "critical": counts["critical"],
                "high": counts["high"],
                "medium": counts["medium"],
                "low": counts["low"],
                "info": info_count,
            },
            "findings": all_findings,
            "proactive_recommendations": [
                f for f in all_findings if f.get("severity") in ("low", "info")
            ],
            "geo_content_strategy": geo_content_strategy,
            "coverage": corpus.get("coverage", {}),
        }
        if errors:
            report["diagnostics"] = {"sub_skill_errors": errors}
        if all_limitations:
            report.setdefault("diagnostics", {})["limitations"] = all_limitations
        return report
    finally:
        if tmp_corpus and os.path.exists(tmp_corpus):
            os.unlink(tmp_corpus)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--community", action="store_true",
                     help="Also run the optional community-corroboration skill.")
    ap.add_argument("--rendered-corpus", default=None,
                     help="Optional JSON {url: rendered_visible_text} from the invoking agent's own browser tool, "
                          "forwarded to crawl-render-audit for a real (non-heuristic) raw-vs-rendered diff.")
    args = ap.parse_args()
    print(json.dumps(compose(args.url, max_pages=args.max_pages, include_community=args.community,
                              rendered_corpus_path=args.rendered_corpus), indent=2))


if __name__ == "__main__":
    main()
