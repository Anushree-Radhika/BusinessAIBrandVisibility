#!/usr/bin/env python3
"""
audit-orchestrator / geo_signals.py

Shared, dependency-free text-signal detectors grounded in the empirical
findings of Aggarwal et al., "GEO: Generative Engine Optimization" (KDD '24,
arXiv:2311.09735) -- the paper that first measured, via a controlled
benchmark (GEO-bench, 10K queries) and a live test on Perplexity.ai, which
content-level changes actually move a source's visibility inside a
generative-engine response.

The paper's findings this module operationalizes as deterministic checks:
  - Adding citations, quotations, and statistics to a page's own content
    was measured to raise a source's visibility substantially versus an
    unmodified baseline (their largest gains, ~30-40% relative improvement
    on their Position-Adjusted Word Count metric, came from these three).
  - Keyword stuffing -- the classical SEO lever -- showed no such benefit
    and in one live test underperformed the unmodified baseline.
  - Persuasive/"authoritative" tone alone showed no significant benefit
    either; content substance (statistics, citations, quotes) mattered far
    more than rhetorical style.
  - The effective method varies by content domain/category -- e.g. their
    measurements found citation- and statistics-style additions performed
    best for factual/Law & Government/debate-style content, while
    quotation-style additions performed best for History/People & Society/
    explanatory content.

This module only detects the deterministic, structural correlates of these
patterns in a page's own text (does it contain quoted material, cited
external sources, and quantitative statistics; does it show signs of
keyword-repetition padding) -- it does not call any generative engine or
reproduce the paper's benchmark. Treat every score here as a proxy signal,
not a validated visibility measurement, and say so in any finding that uses it.
"""
import re
from collections import Counter

STOPWORDS = set("""a an the and or but if of to in on for with as by is are was were
be been being this that these those it its from at into over under not no
we you they he she i our your their""".split())

# Category keyword buckets loosely mirroring GEO-bench's query-tag categories
# (Law & Government, Debate, Science, Business, Health, People & Society,
# History, Explanation, Statement/Facts, Opinion) -- used only to pick which
# published finding is most relevant to THIS page's apparent subject matter,
# never to assert the page IS that category with certainty.
DOMAIN_KEYWORDS = {
    "law_government": ["law", "regulation", "policy", "government", "compliance",
                        "legislation", "statute", "court", "government agency", "tax"],
    "science": ["research", "study", "clinical", "experiment", "data shows",
                "scientists", "peer-reviewed", "laboratory"],
    "business": ["revenue", "pricing", "enterprise", "roi", "business", "market",
                 "customers", "b2b", "startup"],
    "health": ["health", "medical", "treatment", "symptom", "wellness", "clinic",
               "doctor", "patient", "therapy"],
    "history": ["history", "historical", "century", "founded in", "since 19",
                "since 18", "heritage", "legacy"],
    "people_society": ["community", "culture", "society", "people", "story",
                        "family", "tradition"],
    "explanation": ["how it works", "guide", "explained", "tutorial", "step by step",
                     "what is", "why does"],
    "debate_opinion": ["should", "debate", "opinion", "argument", "controversial",
                        "pros and cons", "perspective"],
    "statement_facts": ["fact", "according to", "confirmed", "official statement",
                         "verified"],
}

# GEO paper Table 1 / Table 3: which content-addition method the paper measured
# as most effective, ranked, for a given apparent content category, with the
# paper's own reported relative-improvement range on its Position-Adjusted
# Word Count metric. These are the paper's aggregate benchmark findings, not
# a guarantee for any specific page.
GEO_METHOD_BY_DOMAIN = {
    "law_government": [("Statistics Addition", "24-40%"), ("Cite Sources", "20-30%")],
    "debate_opinion": [("Authoritative", "10-25%"), ("Statistics Addition", "20-30%")],
    "science": [("Authoritative", "10-25%"), ("Fluency Optimization", "15-30%")],
    "business": [("Fluency Optimization", "15-30%"), ("Cite Sources", "20-30%")],
    "health": [("Fluency Optimization", "15-30%"), ("Statistics Addition", "20-30%")],
    "history": [("Quotation Addition", "25-40%"), ("Authoritative", "10-25%")],
    "people_society": [("Quotation Addition", "25-40%")],
    "explanation": [("Quotation Addition", "25-40%")],
    "statement_facts": [("Cite Sources", "20-30%")],
}
DEFAULT_METHODS = [("Cite Sources", "20-30%"), ("Statistics Addition", "20-40%"),
                    ("Quotation Addition", "20-40%")]

STAT_RE = re.compile(
    r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(%|percent|kg|km|lbs|million|billion|thousand|x\b)"
    r"|\b\d{4}\b(?=.{0,15}(study|report|survey|research))"
    , re.I)
QUOTE_RE = re.compile(r'["\u201c][^"\u201d]{15,}["\u201d]')
ATTRIBUTION_RE = re.compile(r"\baccording to\b|\bsaid\b|\breports? (that|from)\b|\bsurvey (by|from|conducted)\b", re.I)
CITATION_LINK_RE = re.compile(r'<a\s+[^>]*href=["\'](https?://[^"\']+)["\']', re.I)


def strip_tags(html):
    return re.sub(r"<[^>]+>", " ", html or "")


def content_quality_signals(html, origin_netloc=None):
    """Returns counts of the three GEO-paper-linked content patterns
    (statistics, quotations/attribution, external citations) in a page,
    plus a coarse word-repetition score used as a keyword-stuffing proxy."""
    text = strip_tags(html)
    stats = len(STAT_RE.findall(text))
    quotes = len(QUOTE_RE.findall(text)) + len(ATTRIBUTION_RE.findall(text))
    links = CITATION_LINK_RE.findall(html or "")
    external_citations = 0
    if origin_netloc:
        for url in links:
            if origin_netloc not in url:
                external_citations += 1
    else:
        external_citations = len(links)

    words = [w.lower() for w in re.findall(r"[a-zA-Z']{3,}", text) if w.lower() not in STOPWORDS]
    total_words = max(len(words), 1)
    top_repeats = Counter(words).most_common(5)
    max_repeat_ratio = (top_repeats[0][1] / total_words) if top_repeats else 0.0

    return {
        "stat_mentions": stats,
        "quote_or_attribution_mentions": quotes,
        "external_citation_links": external_citations,
        "word_count": total_words,
        "max_single_word_repeat_ratio": round(max_repeat_ratio, 4),
        "top_repeated_words": top_repeats,
    }


def classify_domain(html):
    """Best-effort, keyword-bucket domain guess -- used only to select which
    published GEO finding is most relevant, never asserted as ground truth."""
    text = strip_tags(html).lower()
    scores = {cat: sum(text.count(kw) for kw in kws) for cat, kws in DOMAIN_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return None, scores
    return best, scores


def recommended_geo_methods(domain):
    return GEO_METHOD_BY_DOMAIN.get(domain, DEFAULT_METHODS)


def subjective_impression_proxy(html, sample_other_pages_text=None):
    """A heuristic, page-level proxy for the GEO paper's four qualitative
    "Subjective Impression" sub-metrics (relevance, influence, uniqueness,
    diversity) -- computed here from static text signals only, NOT the
    paper's own LLM-judge (G-Eval) methodology. Each score is 0-100 and
    must always be presented as a rough proxy, not a validated measurement.
    """
    text = strip_tags(html)
    words = re.findall(r"[a-zA-Z']{3,}", text.lower())
    total = max(len(words), 1)
    unique_ratio = len(set(words)) / total  # crude "uniqueness" proxy
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentence_lengths = [len(s.split()) for s in sentences if s.strip()]
    length_variety = (max(sentence_lengths) - min(sentence_lengths)) if sentence_lengths else 0
    diversity = min(100, round(length_variety * 2))  # crude "diversity" proxy

    heading_like = len(re.findall(r"\b(overview|summary|key|important|note)\b", text, re.I))
    influence = min(100, heading_like * 15)  # crude "influence"/salience proxy

    overlap_ratio = 0.0
    if sample_other_pages_text:
        other_words = set()
        for t in sample_other_pages_text:
            other_words.update(re.findall(r"[a-zA-Z']{4,}", t.lower()))
        this_words = set(w for w in words if len(w) >= 4)
        if this_words:
            overlap_ratio = len(this_words & other_words) / len(this_words)
    relevance = round(100 * (1 - overlap_ratio)) if sample_other_pages_text else None

    return {
        "uniqueness_proxy": round(unique_ratio * 100, 1),
        "diversity_proxy": diversity,
        "influence_proxy": influence,
        "relevance_proxy": relevance,
        "methodology_note": "Static text-signal heuristic proxy for the GEO paper's "
                             "Subjective Impression sub-metrics; not the paper's own "
                             "LLM-judge (G-Eval) methodology, and not a measurement "
                             "against any live generative engine.",
    }
