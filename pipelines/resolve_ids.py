#!/usr/bin/env python3
"""Resolve missing NHL IDs via CapWages player pages.

Usage:
  python3 pipelines/resolve_ids.py
  python3 pipelines/resolve_ids.py --input data/normalized/trades.jsonl --output data/resolved/player_id_map.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CAPWAGES_PLAYER_URL = "https://www.capwages.com/players/{slug}"
NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)
LEADING_POSITION_PATTERN = re.compile(r"^(?:D|F|G)\s+", re.IGNORECASE)
WHITESPACE_PATTERN = re.compile(r"\s+")
NON_PLAYER_PATTERN = re.compile(
    r"\b(?:pick|round|future\s+consideration(?:s)?|conditional|retained|salary|rights)\b",
    re.IGNORECASE,
)
TRAILING_POSITION_COMMA_PATTERN = re.compile(r"\s*,\s*(?:C|LW|RW|D|G|F)\s*$", re.IGNORECASE)
TRAILING_POSITION_PAREN_PATTERN = re.compile(r"\s*\((?:C|LW|RW|D|G|F)\)\s*$", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve missing player NHL IDs from CapWages")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/normalized/trades.jsonl"),
        help="Path to normalized trades JSONL (default: data/normalized/trades.jsonl)",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("data/manual/name_overrides.json"),
        help="Path to manual name overrides (default: data/manual/name_overrides.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/resolved/player_id_map.json"),
        help="Output mapping path (default: data/resolved/player_id_map.json)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=25,
        help="Write partial output every N processed names (default: 25)",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def load_trades_jsonl(path: Path) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fp:
        for line_number, line in enumerate(fp, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} in {path}") from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Expected object per line in {path}, got {type(row).__name__} on line {line_number}"
                )
            trades.append(row)
    return trades


def extract_missing_player_names(trades: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()

    for trade in trades:
        for side in ("team_one_receives", "team_two_receives"):
            acquisitions = trade.get(side, [])
            if not isinstance(acquisitions, list):
                continue
            for item in acquisitions:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "player":
                    continue

                nhl_id = item.get("nhl_id")
                if isinstance(nhl_id, int) and nhl_id > 0:
                    continue

                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    cleaned_name = name.strip()
                    if not is_probable_player_name(cleaned_name):
                        continue
                    names.add(cleaned_name)

    return names


def normalize_tsn_name(tsn_name: str) -> str:
    cleaned = tsn_name.strip()
    cleaned = LEADING_POSITION_PATTERN.sub("", cleaned)
    cleaned = TRAILING_POSITION_COMMA_PATTERN.sub("", cleaned)
    cleaned = TRAILING_POSITION_PAREN_PATTERN.sub("", cleaned)

    if "," in cleaned:
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if len(parts) == 2:
            cleaned = f"{parts[1]} {parts[0]}"

    cleaned = WHITESPACE_PATTERN.sub(" ", cleaned)
    return cleaned.strip()


def slugify_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_only.lower()
    lowered = lowered.replace("'", "-").replace(".", "-")
    lowered = re.sub(r"[^a-z0-9-]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered)
    return lowered.strip("-")


def is_probable_player_name(name: str) -> bool:
    normalized = normalize_tsn_name(name)
    if not normalized:
        return False

    if any(char.isdigit() for char in normalized) or "%" in normalized:
        return False

    if NON_PLAYER_PATTERN.search(normalized):
        return False

    parts = [part for part in normalized.split(" ") if part]
    if len(parts) < 2:
        return False

    return True


def pick_override_entry(raw: Any, tsn_name: str) -> tuple[str | None, bool | None]:
    if raw is None:
        return None, None

    if isinstance(raw, str):
        return raw.strip() or None, None

    if not isinstance(raw, dict):
        raise ValueError(f"Override for {tsn_name!r} must be string or object")

    slug = raw.get("capwages_slug")
    is_nhl = raw.get("is_nhl")

    if slug is not None and not isinstance(slug, str):
        raise ValueError(f"Override capwages_slug for {tsn_name!r} must be string")
    if is_nhl is not None and not isinstance(is_nhl, bool):
        raise ValueError(f"Override is_nhl for {tsn_name!r} must be boolean")

    return (slug.strip() if isinstance(slug, str) and slug.strip() else None), is_nhl


def fetch_capwages_next_data(slug: str) -> tuple[dict[str, Any] | None, int | None]:
    url = CAPWAGES_PLAYER_URL.format(slug=slug)
    max_retries = 4
    html = ""

    for attempt in range(1, max_retries + 1):
        request = Request(
            url,
            headers={
                "Accept": "text/html",
                "User-Agent": "nhl-trade-market/1.0",
            },
        )

        try:
            with urlopen(request, timeout=20) as response:
                html = response.read().decode("utf-8")
                break
        except HTTPError as err:
            if err.code == 404:
                return None, 404

            retryable = err.code == 429 or 500 <= err.code < 600
            if not retryable or attempt == max_retries:
                raise RuntimeError(f"HTTP {err.code} while fetching {url}") from err
        except (TimeoutError, URLError) as err:
            if attempt == max_retries:
                raise RuntimeError(f"Network error while fetching {url}: {err}") from err

        sleep_seconds = (2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
        logging.warning(
            "Fetch failed for %s (attempt %d/%d). Retrying in %.2fs",
            slug,
            attempt,
            max_retries,
            sleep_seconds,
        )
        time.sleep(sleep_seconds)

    match = NEXT_DATA_PATTERN.search(html)
    if not match:
        raise RuntimeError(f"Could not find __NEXT_DATA__ script on {url}")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid __NEXT_DATA__ JSON on {url}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected __NEXT_DATA__ payload type on {url}")

    return data, None


def extract_player_from_next_data(data: dict[str, Any], slug_hint: str) -> tuple[int | None, str]:
    props = data.get("props", {})
    if not isinstance(props, dict):
        return None, slug_hint

    page_props = props.get("pageProps", {})
    if not isinstance(page_props, dict):
        return None, slug_hint

    player = page_props.get("player", {})
    if not isinstance(player, dict):
        return None, slug_hint

    nhl_id = player.get("nhlId")
    capwages_slug = player.get("slug")

    resolved_nhl_id = nhl_id if isinstance(nhl_id, int) and nhl_id > 0 else None
    resolved_slug = capwages_slug if isinstance(capwages_slug, str) and capwages_slug else slug_hint

    return resolved_nhl_id, resolved_slug


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    logging.info("Loading trades from %s", args.input)
    trades = load_trades_jsonl(args.input)

    logging.info("Loading name overrides from %s", args.overrides)
    overrides_raw = load_json(args.overrides)
    if not isinstance(overrides_raw, dict):
        raise ValueError(f"Expected object payload in {args.overrides}")

    missing_names = sorted(extract_missing_player_names(trades))
    logging.info("Found %d players with missing nhl_id", len(missing_names))

    resolved: dict[str, dict[str, Any]] = {}
    if args.output.exists():
        existing = load_json(args.output)
        if not isinstance(existing, dict):
            raise ValueError(f"Expected object payload in {args.output}")
        resolved = {
            key: value for key, value in existing.items() if isinstance(key, str) and isinstance(value, dict)
        }
        logging.info("Resuming from existing output with %d rows", len(resolved))

    total_missing = len(missing_names)
    for index, tsn_name in enumerate(missing_names, start=1):
        if index == 1 or index % 25 == 0 or index == total_missing:
            logging.info("Resolving %d/%d: %s", index, total_missing, tsn_name)

        if tsn_name in resolved:
            continue

        override_slug, override_is_nhl = pick_override_entry(overrides_raw.get(tsn_name), tsn_name)
        normalized_name = normalize_tsn_name(tsn_name)
        candidate_slug = override_slug or slugify_name(normalized_name)

        if not candidate_slug:
            raise ValueError(f"Could not build slug for {tsn_name!r}")

        if override_is_nhl is False:
            resolved[tsn_name] = {
                "nhl_id": None,
                "capwages_slug": candidate_slug,
                "is_nhl": False,
            }
            continue

        next_data, status_code = fetch_capwages_next_data(candidate_slug)

        if status_code == 404 and override_slug is None:
            resolved[tsn_name] = {
                "nhl_id": None,
                "capwages_slug": candidate_slug,
                "is_nhl": False,
            }
            logging.debug("No CapWages page for %s (%s)", tsn_name, candidate_slug)
            continue

        if status_code == 404:
            raise RuntimeError(
                f"Override slug {candidate_slug!r} for {tsn_name!r} returned 404. "
                "Fix data/manual/name_overrides.json"
            )

        assert next_data is not None
        nhl_id, resolved_slug = extract_player_from_next_data(next_data, candidate_slug)

        resolved[tsn_name] = {
            "nhl_id": nhl_id,
            "capwages_slug": resolved_slug,
            "is_nhl": True,
        }

        if args.checkpoint_every > 0 and index % args.checkpoint_every == 0:
            write_json(args.output, resolved)
            logging.info("Checkpoint saved to %s (%d rows)", args.output, len(resolved))

    write_json(args.output, resolved)
    resolved_count = sum(1 for row in resolved.values() if row.get("nhl_id") is not None)
    non_nhl_count = sum(1 for row in resolved.values() if row.get("is_nhl") is False)
    logging.info(
        "Wrote %d rows to %s (resolved_nhl=%d, is_nhl_false=%d)",
        len(resolved),
        args.output,
        resolved_count,
        non_nhl_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
