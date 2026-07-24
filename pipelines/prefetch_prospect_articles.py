"""Prefetch web articles for prospects found in classified elements.

For each element classified as a prospect (skater_prospect or goalie_prospect)
in data/resolved/classified_elements.jsonl:
  1. Build query: '{name} prospect hockey {trade_year}'
  2. Call search_and_fetch(query, end_date=trade_date) from web_search.py
  3. Results are automatically cached in data/raw/search/ and data/raw/articles/

Idempotent: won't re-fetch if already cached (safe to re-run without extra cost).

Logs at the end: N prospects processed, N with content found, N without result.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Allow running from repo root: python pipelines/prefetch_prospect_articles.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.web_search import hash_search, load_cached_search, search_and_fetch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

PROSPECT_TYPES = {"skater_prospect", "goalie_prospect"}
CLASSIFIED_ELEMENTS_PATH = Path("data/resolved/classified_elements.jsonl")


def _build_query(name: str, trade_date: str) -> str:
    year = trade_date[:4]
    # Strip leading position codes like "D " or "G " that TSN sometimes prepends
    clean_name = name.strip()
    if len(clean_name) >= 2 and clean_name[1] == " " and clean_name[0].isalpha():
        clean_name = clean_name[2:]
    return f"{clean_name} prospect hockey {year}"


def _already_cached(query: str, end_date: str) -> bool:
    """Return True if the search result is already on disk (idempotency guard)."""
    from pipelines.sources.web_search import CACHE_MISS, SEARCH_CACHE_DIR

    cached = load_cached_search(query, end_date, SEARCH_CACHE_DIR)
    return cached is not CACHE_MISS


def load_prospects() -> list[dict]:
    """Load all prospect elements from classified_elements.jsonl."""
    prospects: list[dict] = []
    if not CLASSIFIED_ELEMENTS_PATH.exists():
        log.error("File not found: %s", CLASSIFIED_ELEMENTS_PATH)
        return prospects

    with CLASSIFIED_ELEMENTS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("element", {}).get("type_classified") in PROSPECT_TYPES:
                prospects.append(obj)

    return prospects


def prefetch_all() -> None:
    prospects = load_prospects()
    total = len(prospects)
    log.info("Found %d prospect elements to process", total)

    found = 0
    not_found = 0

    for obj in prospects:
        name: str = obj["element"].get("tsn_name", "")
        trade_date: str = obj.get("trade_date", "")
        trade_id = obj.get("trade_id")

        if not name or not trade_date:
            log.warning("Skipping element with missing name or date: %s", obj)
            not_found += 1
            continue

        query = _build_query(name, trade_date)

        if _already_cached(query, trade_date):
            log.info("[trade %s] Cache hit — skipping: %r", trade_id, query)
            # Count existing cache as "found" only if there are URLs
            from pipelines.sources.web_search import CACHE_MISS, SEARCH_CACHE_DIR

            cached_urls = load_cached_search(query, trade_date, SEARCH_CACHE_DIR)
            if cached_urls is not CACHE_MISS and cached_urls:
                found += 1
            else:
                not_found += 1
            continue

        log.info("[trade %s] Fetching: %r (end_date=%s)", trade_id, query, trade_date)
        content = search_and_fetch(query, end_date=trade_date)

        if content:
            log.info("[trade %s] Content found (%d chars)", trade_id, len(content))
            found += 1
        else:
            log.info("[trade %s] No content found", trade_id)
            not_found += 1

    log.info(
        "Done — %d prospects processed: %d with content, %d without result",
        total,
        found,
        not_found,
    )


if __name__ == "__main__":
    prefetch_all()
