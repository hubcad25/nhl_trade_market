"""Tavily web search and article text fetching with on-disk caching."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

CHROME_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
MIN_PARAGRAPH_LENGTH = 80
MIN_SUBSTANTIAL_LENGTH = 200
MAX_SEARCH_RESULTS = 10
CACHE_DIR = Path("data/raw/articles")
SEARCH_CACHE_DIR = Path("data/raw/search")
CACHE_MISS = object()

SOURCES_WHITELIST = {
    "nhl.com",
    "thehockeynews.com",
    "dobberprospects.com",
    "insidetherink.com",
    "zonecoverage.com",
    "bostonhockeyinsider.com",
    "montrealhockeynow.com",
    "cbc.ca",
    "globalnews.ca",
    "thehockeywriters.com",
    "prohockeyrumors.com",
}

WHITESPACE_PATTERN = re.compile(r"\s+")
NHL_ARTICLE_ID_PATTERN = re.compile(r"-(\d{7,8})(?:$|[/?#])")


def _get_tavily_client() -> TavilyClient:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY not set in environment / .env")
    return TavilyClient(api_key=api_key)


class ParagraphExtractor(HTMLParser):
    """Extract visible paragraph text while skipping non-content sections."""

    EXCLUDED_TAGS = {
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "svg",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._excluded_depth = 0
        self._paragraph_depth = 0
        self._paragraph_chunks: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in self.EXCLUDED_TAGS:
            self._excluded_depth += 1
            return
        if normalized == "p" and self._excluded_depth == 0:
            self._paragraph_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self.EXCLUDED_TAGS and self._excluded_depth > 0:
            self._excluded_depth -= 1
            return
        if normalized == "p" and self._paragraph_depth > 0:
            self._paragraph_depth -= 1
            if self._paragraph_depth == 0:
                text = clean_text(" ".join(self._paragraph_chunks))
                self._paragraph_chunks = []
                if text:
                    self.paragraphs.append(text)

    def handle_data(self, data: str) -> None:
        if self._excluded_depth > 0 or self._paragraph_depth == 0:
            return
        if data:
            self._paragraph_chunks.append(data)


def clean_text(text: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cache_path_for_url(url: str, cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / f"{hash_url(url)}.json"


def load_cached_article(url: str, cache_dir: Path = CACHE_DIR) -> str | None | object:
    path = cache_path_for_url(url, cache_dir)
    if not path.exists():
        return CACHE_MISS

    try:
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return CACHE_MISS

    if not isinstance(payload, dict):
        return CACHE_MISS

    text = payload.get("text")
    if text is None:
        return None
    if isinstance(text, str):
        return text
    return CACHE_MISS


def save_article_cache(url: str, text: str | None, cache_dir: Path = CACHE_DIR) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
    }
    path = cache_path_for_url(url, cache_dir)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def hash_search(query: str, end_date: str | None) -> str:
    key = json.dumps({"query": query, "end_date": end_date}, sort_keys=True)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_cached_search(query: str, end_date: str | None, cache_dir: Path = SEARCH_CACHE_DIR) -> list[str] | object:
    path = cache_dir / f"{hash_search(query, end_date)}.json"
    if not path.exists():
        return CACHE_MISS
    try:
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return CACHE_MISS
    if not isinstance(payload, dict):
        return CACHE_MISS
    urls = payload.get("urls")
    if isinstance(urls, list):
        return urls
    return CACHE_MISS


def save_search_cache(query: str, end_date: str | None, urls: list[str], cache_dir: Path = SEARCH_CACHE_DIR) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "query": query,
        "end_date": end_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "urls": urls,
    }
    path = cache_dir / f"{hash_search(query, end_date)}.json"
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def normalize_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def extract_nhl_article_id(url: str) -> int | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "nhl.com" not in host:
        return None

    match = NHL_ARTICLE_ID_PATTERN.search(parsed.path)
    if not match:
        return None
    return int(match.group(1))


def is_whitelisted_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    for domain in SOURCES_WHITELIST:
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False


def search(query: str, end_date: str | None = None) -> list[str]:
    """Search via Tavily, restricted to whitelisted domains, return up to MAX_SEARCH_RESULTS unique URLs.

    Results are cached on disk keyed by (query, end_date) to avoid burning credits on restarts.

    Args:
        query: Search query string.
        end_date: Optional upper bound in YYYY-MM-DD format (e.g. trade date).
                  Tavily will only return articles published on or before this date.
    """
    cached = load_cached_search(query, end_date)
    if cached is not CACHE_MISS:
        assert isinstance(cached, list)
        logging.debug("Search cache hit for %r", query)
        return cached

    client = _get_tavily_client()
    kwargs: dict = dict(
        query=query,
        max_results=MAX_SEARCH_RESULTS,
        search_depth="basic",
        include_domains=list(SOURCES_WHITELIST),
    )
    if end_date:
        kwargs["end_date"] = end_date

    try:
        response = client.search(**kwargs)
    except Exception as err:
        logging.warning("Tavily search failed for %r: %s", query, err)
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for result in response.get("results", []):
        url = normalize_url(result.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        nhl_id = extract_nhl_article_id(url)
        if nhl_id is not None:
            logging.debug("Found NHL article id %s from URL %s", nhl_id, url)

    save_search_cache(query, end_date, urls)
    return urls


def fetch_html(url: str, timeout_seconds: int = 30) -> str:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": CHROME_USER_AGENT,
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        content = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return content.decode(charset, errors="replace")


def extract_article_text(html: str) -> str | None:
    parser = ParagraphExtractor()
    parser.feed(html)
    parser.close()

    paragraphs = [p for p in parser.paragraphs if len(p) >= MIN_PARAGRAPH_LENGTH]
    if not paragraphs:
        return None

    text = "\n\n".join(paragraphs)
    return text if text else None


def fetch_article(url: str) -> str | None:
    """Fetch article text, using cache first."""

    cached = load_cached_article(url)
    if cached is not CACHE_MISS:
        assert cached is None or isinstance(cached, str)
        return cached

    try:
        html = fetch_html(url)
    except (HTTPError, URLError, TimeoutError, ValueError) as err:
        logging.debug("Failed to fetch article %s: %s", url, err)
        save_article_cache(url, None)
        return None

    text = extract_article_text(html)
    save_article_cache(url, text)
    return text


def search_and_fetch(query: str, max_attempts: int = 5, end_date: str | None = None) -> str | None:
    """Search via Tavily, whitelist results, then fetch first substantial article.

    Args:
        query: Search query string.
        max_attempts: Max number of whitelisted URLs to try fetching.
        end_date: Optional upper bound in YYYY-MM-DD format (e.g. trade date).
    """
    urls = search(query, end_date=end_date)
    whitelisted = [url for url in urls if is_whitelisted_url(url)]

    for attempt, url in enumerate(whitelisted, start=1):
        if attempt > max_attempts:
            break
        text = fetch_article(url)
        if text and len(text) > MIN_SUBSTANTIAL_LENGTH:
            return text
    return None
