"""Prefetch web articles for trades without a working TSN article.

For each trade in data/normalized/trades.jsonl that either:
  - has no tsn_url, OR
  - has a tsn_url that returns a non-200 status (dead link)

  1. Build query: '{player1} {player2} trade {team1} {team2} {year}'
  2. Call search_and_fetch(query, end_date=trade_date) from web_search.py
  3. Results are automatically cached in data/raw/search/ and data/raw/articles/

Idempotent: won't re-fetch if already cached (safe to re-run without extra cost).

Logs at the end: N trades processed, N with content found, N without result.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.web_search import (
    CACHE_MISS,
    CHROME_USER_AGENT,
    SEARCH_CACHE_DIR,
    load_cached_search,
    search_and_fetch,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TRADES_PATH = Path("data/normalized/trades.jsonl")
TSN_CHECK_TIMEOUT = 8  # seconds


def is_url_alive(url: str) -> bool:
    """Return True if URL returns HTTP 2xx."""
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": CHROME_USER_AGENT})
        with urlopen(req, timeout=TSN_CHECK_TIMEOUT) as r:
            return r.status < 400
    except (HTTPError, URLError, TimeoutError, ValueError):
        return False


def _player_names(trade: dict) -> list[str]:
    """Extract player/prospect names from both sides of a trade."""
    names = []
    for key in ("team_one_receives", "team_two_receives"):
        for el in trade.get(key, []):
            if el.get("type") in ("player", "prospect"):
                name = el.get("name", "").strip()
                # Strip leading position codes like "D " or "G "
                if len(name) >= 2 and name[1] == " " and name[0].isalpha():
                    name = name[2:]
                if name:
                    names.append(name)
    return names


def _build_query(trade: dict) -> str:
    year = trade.get("trade_date", "")[:4]
    team1 = trade.get("team_one", {}).get("short", "")
    team2 = trade.get("team_two", {}).get("short", "")
    players = _player_names(trade)[:2]  # top 2 players
    parts = players + [team1, team2, "trade", year]
    return " ".join(p for p in parts if p)


def _already_cached(query: str, end_date: str) -> bool:
    cached = load_cached_search(query, end_date, SEARCH_CACHE_DIR)
    return cached is not CACHE_MISS


def load_trades() -> list[dict]:
    trades = []
    if not TRADES_PATH.exists():
        log.error("File not found: %s", TRADES_PATH)
        return trades
    with TRADES_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


def prefetch_all() -> None:
    trades = load_trades()
    log.info("Loaded %d trades total", len(trades))

    to_fetch: list[dict] = []
    skipped_alive = 0

    for trade in trades:
        tsn_url = trade.get("tsn_url")
        if not tsn_url:
            to_fetch.append(trade)
        else:
            if is_url_alive(tsn_url):
                skipped_alive += 1
                log.debug("[trade %s] TSN URL alive, skipping", trade.get("trade_id"))
            else:
                log.info("[trade %s] TSN URL dead (%s), will search", trade.get("trade_id"), tsn_url)
                to_fetch.append(trade)

    log.info(
        "%d trades to search (%d skipped — TSN URL alive)",
        len(to_fetch),
        skipped_alive,
    )

    found = 0
    not_found = 0

    for trade in to_fetch:
        trade_id = trade.get("trade_id")
        trade_date = trade.get("trade_date", "")
        query = _build_query(trade)

        if _already_cached(query, trade_date):
            cached_urls = load_cached_search(query, trade_date, SEARCH_CACHE_DIR)
            if cached_urls is not CACHE_MISS and cached_urls:
                log.info("[trade %s] Cache hit — skipping: %r", trade_id, query)
                found += 1
            else:
                log.info("[trade %s] Cache hit (no results) — skipping: %r", trade_id, query)
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
        "Done — %d trades processed: %d with content, %d without result",
        len(to_fetch),
        found,
        not_found,
    )


if __name__ == "__main__":
    prefetch_all()
