#!/usr/bin/env python3
"""
audit-orchestrator / crawl.py

Builds ONE shared, bounded audit corpus for a target site: robots.txt rules,
a diverse same-origin page sample (raw HTML, fetched once), sitemap entries,
and llms.txt presence. Every sub-skill's check.py consumes this corpus
instead of independently re-fetching the site, so:
  - robots.txt is honored consistently everywhere (pages it disallows never
    enter the corpus in the first place),
  - evidence can say "8/8 product pages" instead of "1/1",
  - the site is only crawled once regardless of how many sub-skills run.

This module is read-only: every request is a GET. It can also be run
standalone for testing / debugging a corpus:
    python3 crawl.py https://example.com > corpus.json

Design budgets (kept conservative so 4-5 sub-skills + this crawl finish
well inside the contest's 5-minute runtime cap):
  - MAX_PAGES same-origin pages fetched (including homepage)
  - PER_REQUEST_TIMEOUT seconds per HTTP call
  - MAX_LINKS_SCANNED links considered when picking the sample
"""
import sys
import re
import json
import time
import datetime
import urllib.request
import urllib.error
import urllib.robotparser
import urllib.parse
from html.parser import HTMLParser
import xml.etree.ElementTree as ET

HUMAN_UA = "Mozilla/5.0 (compatible; brand-ai-readiness-audit/1.0; +https://agentskills.io)"
BOT_UA_FOR_CLOAKING_CHECK = "GPTBot/1.0 (+https://openai.com/gptbot)"
AI_BOTS = ["GPTBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai",
           "PerplexityBot", "Google-Extended", "ChatGPT-User", "CCBot"]

PER_REQUEST_TIMEOUT = 8
MAX_PAGES = 10
MAX_LINKS_SCANNED = 200
GLOBAL_TIME_BUDGET_SEC = 90  # this module's share of the overall 5-minute runtime


class LinkAndTextExtractor(HTMLParser):
    """Single-pass parser: pulls same-origin links, visible-text length,
    inline-script length, and a rough page-type hint, without executing JS."""

    def __init__(self, origin):
        super().__init__()
        self.origin = origin
        self.links = []
        self.text_len = 0
        self.script_len = 0
        self._in_script = False
        self.title = ""
        self._in_title = False
        self.h1 = ""
        self._in_h1 = False
        self.root_like_divs = 0
        self.jsonld_blobs = []
        self._in_jsonld = False
        self._buf = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            href = attrs["href"]
            abs_url = urllib.parse.urljoin(self.origin, href)
            parsed = urllib.parse.urlparse(abs_url)
            if parsed.scheme in ("http", "https") and parsed.netloc == urllib.parse.urlparse(self.origin).netloc:
                clean = abs_url.split("#")[0]
                self.links.append(clean)
        if tag == "script":
            self._in_script = True
            if attrs.get("type") == "application/ld+json":
                self._in_jsonld = True
                self._buf = []
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._in_h1 = True
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "div" and attrs.get("id", "").lower() in ("root", "app", "__next", "__nuxt"):
            self.root_like_divs += 1

    def handle_endtag(self, tag):
        if tag == "script":
            if self._in_jsonld:
                self.jsonld_blobs.append("".join(self._buf))
            self._in_script = False
            self._in_jsonld = False
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if self._in_jsonld:
            self._buf.append(data)
        elif self._in_script:
            self.script_len += len(data)
        else:
            self.text_len += len(data.strip())
            if self._in_title:
                self.title += data.strip()
            if self._in_h1:
                self.h1 += data.strip()


def fetch(url, ua=HUMAN_UA, timeout=PER_REQUEST_TIMEOUT):
    """Returns (status_or_None, body_str, headers_dict, error_kind_or_None).
    error_kind distinguishes a genuine HTTP response (error_kind=None, status set)
    from a transport-level failure (error_kind='network', status=None) --
    these must NOT be treated the same way by downstream checks."""
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode(errors="ignore"), dict(r.headers), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="ignore")
        except Exception:
            body = ""
        return e.code, body, dict(e.headers or {}), None
    except Exception as e:
        return None, "", {}, f"network:{type(e).__name__}"


def guess_page_type(url, title, h1):
    u = url.lower()
    if re.search(r"/(product|item|p)/", u) or re.search(r"product", title.lower()):
        return "product"
    if re.search(r"/(category|collections|shop|catalog)/?", u):
        return "category"
    if re.search(r"/(blog|article|news|post)/", u):
        return "article"
    if re.search(r"/(spec|specification)s?/?$", u):
        return "specifications"
    if u.rstrip("/").count("/") <= 2:
        return "homepage"
    return "other"


def parse_robots(origin):
    code, body, _, err = fetch(origin + "/robots.txt")
    blocked_ai_bots = []
    sitemap_urls = []
    if code == 200 and body:
        for bot in AI_BOTS:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(body.splitlines())
            try:
                if not rp.can_fetch(bot, origin + "/"):
                    blocked_ai_bots.append(bot)
            except Exception:
                pass
        for line in body.splitlines():
            if line.strip().lower().startswith("sitemap:"):
                sitemap_urls.append(line.split(":", 1)[1].strip())
    return {
        "fetched": code == 200,
        "status": code,
        "blocked_ai_bots": blocked_ai_bots,
        "declared_sitemaps": sitemap_urls,
        "raw_present": bool(body),
    }


def can_fetch_path(origin, path_url, robots_body):
    if not robots_body:
        return True
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_body.splitlines())
    try:
        return rp.can_fetch(HUMAN_UA, path_url)
    except Exception:
        return True


def fetch_sitemap(origin, declared_sitemaps):
    candidates = declared_sitemaps + [origin + "/sitemap.xml"]
    for sm_url in candidates:
        code, body, _, err = fetch(sm_url)
        if code == 200 and body:
            entries = []
            try:
                root = ET.fromstring(body)
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for url_el in root.findall(".//sm:url", ns):
                    loc = url_el.find("sm:loc", ns)
                    lastmod = url_el.find("sm:lastmod", ns)
                    if loc is not None and loc.text:
                        entries.append({"loc": loc.text.strip(),
                                         "lastmod": lastmod.text.strip() if lastmod is not None and lastmod.text else None})
            except ET.ParseError:
                pass
            return {"url": sm_url, "status": 200, "entries": entries}
    return {"url": candidates[0], "status": None, "entries": []}


def select_sample(origin, homepage_links, sitemap_entries, max_pages):
    """Diverse sample: homepage first, then a mix of page types, deduped,
    skipping obvious non-content/crawl-trap paths."""
    SKIP_PATTERNS = re.compile(
        r"(login|logout|signin|signup|account|cart|checkout|admin|/api/|"
        r"\.pdf$|\.jpg$|\.png$|\.zip$|mailto:|tel:|javascript:)", re.I
    )
    seen = set()
    sample = [origin + "/"]
    seen.add((origin + "/").rstrip("/"))

    pool = []
    for loc in [e["loc"] for e in sitemap_entries]:
        pool.append(loc)
    pool.extend(homepage_links[:MAX_LINKS_SCANNED])

    for url in pool:
        if len(sample) >= max_pages:
            break
        key = url.rstrip("/")
        if key in seen:
            continue
        if SKIP_PATTERNS.search(url):
            continue
        seen.add(key)
        sample.append(url)
    return sample[:max_pages]


def build_corpus(url, max_pages=MAX_PAGES):
    start = time.time()
    parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    entry_url = url if "://" in url else "https://" + url

    robots = parse_robots(origin)
    robots_body = None
    if robots["fetched"]:
        _, robots_body, _, _ = fetch(origin + "/robots.txt")

    # Homepage fetch (also used for UA-cloaking comparison and link discovery).
    hp_status, hp_html, hp_headers, hp_err = fetch(entry_url)
    hp_status_bot, hp_html_bot, _, hp_err_bot = fetch(entry_url, ua=BOT_UA_FOR_CLOAKING_CHECK)

    parser = LinkAndTextExtractor(origin)
    if hp_html:
        try:
            parser.feed(hp_html)
        except Exception:
            pass

    sitemap = fetch_sitemap(origin, robots["declared_sitemaps"])
    llms_status, _, _, _ = fetch(origin + "/llms.txt")

    sample_urls = select_sample(origin, parser.links, sitemap["entries"], max_pages)

    pages = []
    blocked_by_robots = []
    for page_url in sample_urls:
        if time.time() - start > GLOBAL_TIME_BUDGET_SEC:
            break
        if robots_body and not can_fetch_path(origin, page_url, robots_body):
            blocked_by_robots.append(page_url)
            continue
        if page_url.rstrip("/") == entry_url.rstrip("/"):
            status, html, headers, err = hp_status, hp_html, hp_headers, hp_err
        else:
            status, html, headers, err = fetch(page_url)
        p = LinkAndTextExtractor(origin)
        if html:
            try:
                p.feed(html)
            except Exception:
                pass
        pages.append({
            "url": page_url,
            "status": status,
            "fetch_error": err,
            "html": html,
            "headers": headers,
            "text_len": p.text_len,
            "script_len": p.script_len,
            "root_like_divs": p.root_like_divs,
            "jsonld_blobs": p.jsonld_blobs,
            "title": p.title,
            "h1": p.h1,
            "ids": p.ids,
            "page_type": guess_page_type(page_url, p.title, p.h1),
        })

    corpus = {
        "site": entry_url,
        "origin": origin,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "robots": robots,
        "homepage_cloaking_check": {
            "human_status": hp_status,
            "bot_status": hp_status_bot,
            "human_len": len(hp_html or ""),
            "bot_len": len(hp_html_bot or ""),
            "human_err": hp_err,
            "bot_err": hp_err_bot,
        },
        "sitemap": sitemap,
        "llms_txt": {"status": llms_status, "found": llms_status == 200},
        "pages": pages,
        "coverage": {
            "pages_requested": max_pages,
            "pages_fetched": len(pages),
            "blocked_by_robots": blocked_by_robots,
            "elapsed_sec": round(time.time() - start, 1),
        },
    }
    return corpus


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: crawl.py <url>"}))
        sys.exit(1)
    print(json.dumps(build_corpus(sys.argv[1]), indent=2))
