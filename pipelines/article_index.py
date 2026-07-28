"""Map trade elements to the article text already sitting in the disk cache.

No network calls: replays the query strings built by the prefetch scripts against
data/raw/search/ and data/raw/articles/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.prefetch_prospect_articles import _build_query as prospect_query
from pipelines.prefetch_trade_articles import _build_query as trade_query
from pipelines.sources.web_search import (
    CACHE_MISS,
    SEARCH_CACHE_DIR,
    is_whitelisted_url,
    load_cached_article,
    load_cached_search,
)

TRADES_PATH = Path("data/normalized/trades.jsonl")
CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
PROSPECT_TYPES = {"skater_prospect", "goalie_prospect"}


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _articles_for_query(query: str, end_date: str) -> list[dict]:
    """Return [{'url': ..., 'text': ...}] for a cached query, text-bearing only."""
    urls = load_cached_search(query, end_date, SEARCH_CACHE_DIR)
    if urls is CACHE_MISS:
        return []

    found = []
    for url in urls:
        if not is_whitelisted_url(url):
            continue
        text = load_cached_article(url)
        if isinstance(text, str) and text.strip():
            found.append({"url": url, "text": text})
    return found


def build_index() -> dict[int, list[dict]]:
    """trade_id -> article candidates found via the trade-level query."""
    return {
        t["trade_id"]: _articles_for_query(trade_query(t), t.get("trade_date", ""))
        for t in load_jsonl(TRADES_PATH)
    }


def articles_for_element(element: dict, trade_index: dict[int, list[dict]]) -> list[dict]:
    """Candidate articles for one classified element, trade-level first."""
    candidates = list(trade_index.get(element["trade_id"], []))

    el = element["element"]
    if el.get("type_classified") in PROSPECT_TYPES:
        name = el.get("tsn_name", "")
        trade_date = element.get("trade_date", "")
        candidates += _articles_for_query(prospect_query(name, trade_date), trade_date)

    seen, unique = set(), []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return unique


if __name__ == "__main__":
    index = build_index()
    elements = load_jsonl(CLASSIFIED_PATH)

    with_text = [e for e in elements if articles_for_element(e, index)]
    by_type: dict[str, int] = {}
    for e in with_text:
        t = e["element"]["type_classified"]
        by_type[t] = by_type.get(t, 0) + 1

    print(f"{len(with_text)}/{len(elements)} elements have at least one cached article")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {t:20s} {n}")
