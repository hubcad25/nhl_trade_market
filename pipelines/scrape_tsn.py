#!/usr/bin/env python3
"""Scrape TSN NHL Trade Tracker raw trades.

Usage examples:
  python pipelines/scrape_tsn.py --all
  python pipelines/scrape_tsn.py --season 2023

The script fetches TSN trade data with retry logic and writes raw JSON to:
  data/raw/tsn/all.json

When --season is used, it writes:
  data/raw/tsn/{season}.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://next-gen.sports.bellmedia.ca/v2/trades/hockey/nhl"
BASE_PARAMS = {"brand": "tsn", "lang": "en"}


def build_url(params: dict[str, Any] | None = None) -> str:
    merged = dict(BASE_PARAMS)
    if params:
        merged.update(params)
    return f"{BASE_URL}?{urlencode(merged)}"


def fetch_json(
    params: dict[str, Any] | None = None,
    *,
    timeout_seconds: int = 30,
    max_retries: int = 4,
    backoff_seconds: float = 1.0,
) -> Any:
    url = build_url(params)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "nhl-trade-market/1.0",
                },
            )
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except HTTPError as err:
            last_error = err
            retryable = err.code == 429 or 500 <= err.code < 600
            if not retryable or attempt == max_retries:
                raise
        except (URLError, TimeoutError, json.JSONDecodeError) as err:
            last_error = err
            if attempt == max_retries:
                raise

        sleep_seconds = backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, 0.3)
        logging.warning(
            "Request failed (attempt %d/%d) for %s: %s. Retrying in %.2fs",
            attempt,
            max_retries,
            url,
            last_error,
            sleep_seconds,
        )
        time.sleep(sleep_seconds)

    if last_error:
        raise RuntimeError(f"Failed to fetch {url}: {last_error}")
    raise RuntimeError(f"Failed to fetch {url}")


def fetch_all_trades() -> list[dict[str, Any]]:
    """Fetch raw trade payload from TSN endpoint."""

    payload = fetch_json()
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected payload type: {type(payload).__name__}")
    if not payload:
        raise RuntimeError("No trades fetched from TSN endpoint")
    return payload


def extract_season(trade: dict[str, Any]) -> int | None:
    raw_date = trade.get("tradeDate")
    if not isinstance(raw_date, str) or not raw_date:
        return None

    try:
        timestamp = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(timestamp.year)


def group_by_season(trades: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    invalid_count = 0

    for trade in trades:
        season = extract_season(trade)
        if season is None:
            invalid_count += 1
            continue
        buckets[season].append(trade)

    if invalid_count:
        logging.warning("Skipped %d trades with missing/invalid tradeDate", invalid_count)

    return dict(sorted(buckets.items()))


def write_season_file(output_dir: Path, season: int, trades: list[dict[str, Any]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{season}.json"
    with target.open("w", encoding="utf-8") as fp:
        json.dump(trades, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    return target


def write_all_file(output_dir: Path, trades: list[dict[str, Any]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "all.json"
    with target.open("w", encoding="utf-8") as fp:
        json.dump(trades, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape TSN NHL Trade Tracker by season")
    parser.add_argument(
        "--season",
        type=int,
        help="Fetch and write only one season file (example: 2023)",
    )
    parser.add_argument("--all", action="store_true", help="Write complete raw payload to all.json")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/tsn"),
        help="Output directory for season JSON files (default: data/raw/tsn)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    if args.season is not None and args.all:
        raise ValueError("Use either --all or --season, not both")

    if args.season is None and not args.all:
        logging.info("No --season provided; defaulting to --all")
        args.all = True

    logging.info("Fetching TSN trades from %s", BASE_URL)
    trades = fetch_all_trades()
    by_season = group_by_season(trades)

    if not by_season:
        raise RuntimeError("No valid seasonal trades found in payload")

    logging.info("Total unique trades fetched: %d", len(trades))

    if args.season is not None:
        selected = {args.season: by_season.get(args.season, [])}
        if not selected[args.season]:
            logging.warning("No trades found for season %d", args.season)
        for season, season_trades in selected.items():
            target = write_season_file(args.output_dir, season, season_trades)
            logging.info("Season %d: wrote %d trades to %s", season, len(season_trades), target)
    else:
        target = write_all_file(args.output_dir, trades)
        logging.info("Wrote %d trades to %s", len(trades), target)

        for season, season_trades in by_season.items():
            logging.info("Season %d: %d trades", season, len(season_trades))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
