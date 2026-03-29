#!/usr/bin/env python3
"""Normalize TSN raw trades into a deterministic canonical JSONL format.

Usage:
  python pipelines/normalize_trades.py
  python pipelines/normalize_trades.py --input data/raw/tsn/all.json --output data/normalized/trades.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any


FUTURE_CONSIDERATIONS_TEXT = "future considerations"
PICK_IN_NAME_PATTERN = re.compile(
    r"\b(?:(\d{1,2})(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh)\s*[-\s]*round\s+pick\b",
    re.IGNORECASE,
)
SALARY_RETENTION_PATTERN = re.compile(
    r"\b(?:retain(?:ed)?|salary|%)\b",
    re.IGNORECASE,
)
NON_PLAYER_NAME_PATTERN = re.compile(
    r"\b(?:pick|round|retained|salary|rights)\b|%|\d",
    re.IGNORECASE,
)
VIA_PATTERN = re.compile(r"\s*\(via\s+([A-Za-z]{2,5})\)\s*$", re.IGNORECASE)
TRAILING_POSITION_COMMA_PATTERN = re.compile(
    r"\s*,\s*(C|LW|RW|D|G)\s*$", re.IGNORECASE
)
TRAILING_POSITION_PAREN_PATTERN = re.compile(
    r"\s*\((C|LW|RW|D|G)\)\s*$", re.IGNORECASE
)
PICK_OWNER_BY_BELONG_PATTERN = re.compile(
    r"belong(?:ed)?\s+to\s+(?:the\s+)?([A-Z][A-Za-z .'-]+?)(?:[\.,]|$)",
    re.IGNORECASE,
)
PICK_OWNER_INLINE_PATTERN = re.compile(
    r"([A-Z][A-Za-z .'-]+?)\s+(20\d{2})\s+((?:\d{1,2}(?:st|nd|rd|th))|first|second|third|fourth|fifth|sixth|seventh)\s*[-\s]*round\s+pick",
    re.IGNORECASE,
)
ROUND_NUMERIC_PATTERN = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s*[-\s]*round", re.IGNORECASE)

ROUND_WORD_TO_NUM = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize TSN trade payload to JSONL")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/tsn/all.json"),
        help="Path to TSN raw all.json (default: data/raw/tsn/all.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/normalized/trades.jsonl"),
        help="Output JSONL file path (default: data/normalized/trades.jsonl)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def load_trades(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, list):
        raise ValueError(f"Expected list payload in {path}, got {type(payload).__name__}")
    return payload


def normalize_trade_date(raw_value: Any) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError("tradeDate is missing or invalid")
    try:
        timestamp = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid tradeDate format: {raw_value}") from exc
    return timestamp.date().isoformat()


def normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def parse_round_value(text: str) -> int | None:
    numeric_match = ROUND_NUMERIC_PATTERN.search(text)
    if numeric_match:
        return int(numeric_match.group(1))

    lowered = text.lower()
    for word, value in ROUND_WORD_TO_NUM.items():
        if word in lowered and "round" in lowered:
            return value
    return None


def extract_pick_origin_mentions(informations: str | None) -> list[dict[str, Any]]:
    if not informations:
        return []

    mentions: list[dict[str, Any]] = []
    chunks = [chunk.strip() for chunk in re.split(r"[.;]", informations) if chunk.strip()]

    for chunk in chunks:
        owner_match = PICK_OWNER_BY_BELONG_PATTERN.search(chunk)
        if owner_match:
            mention: dict[str, Any] = {"owner_team": owner_match.group(1).strip()}
            round_value = parse_round_value(chunk)
            if round_value is not None:
                mention["round"] = round_value
            year_match = re.search(r"\b(20\d{2})\b", chunk)
            if year_match:
                mention["year"] = int(year_match.group(1))
            mentions.append(mention)
            continue

        inline_match = PICK_OWNER_INLINE_PATTERN.search(chunk)
        if inline_match:
            owner_team = inline_match.group(1).strip()
            year_value = int(inline_match.group(2))
            round_text = inline_match.group(3)
            mention = {"owner_team": owner_team, "year": year_value}
            round_value = parse_round_value(round_text + " round")
            if round_value is not None:
                mention["round"] = round_value
            mentions.append(mention)

    return mentions


def normalize_name(raw_name: str) -> tuple[str, str | None]:
    name = raw_name.strip()
    via_team: str | None = None

    via_match = VIA_PATTERN.search(name)
    if via_match:
        via_team = via_match.group(1).upper()
        name = VIA_PATTERN.sub("", name).strip()

    name = TRAILING_POSITION_COMMA_PATTERN.sub("", name).strip()
    name = TRAILING_POSITION_PAREN_PATTERN.sub("", name).strip()

    return name, via_team


def normalize_pick(entry: dict[str, Any]) -> dict[str, Any]:
    round_value = entry.get("draftPickRound")
    year_value = entry.get("draftPickYear")
    if not isinstance(round_value, int) or not isinstance(year_value, int):
        raise ValueError(f"Invalid pick payload: {entry}")

    normalized = {
        "type": "pick",
        "round": round_value,
        "year": year_value,
        "is_conditional": bool(entry.get("isConditional", False)),
    }
    return normalized


def normalize_player(entry: dict[str, Any], raw_name: str) -> dict[str, Any]:
    cleaned_name, via_team = normalize_name(raw_name)
    if not cleaned_name:
        raise ValueError(f"Player entry has empty name after cleanup: {entry}")

    raw_player_id = entry.get("playerId")
    nhl_id = raw_player_id if isinstance(raw_player_id, int) and raw_player_id > 0 else None

    normalized = {"type": "player", "nhl_id": nhl_id, "name": cleaned_name}
    if via_team:
        normalized["via_team"] = via_team
    return normalized


def try_parse_pick_from_name(raw_name: str) -> dict[str, Any] | None:
    """Try to parse a pick out of a playerName string like 'Conditional 2023 7th Round Pick'.

    Returns a normalized pick dict if matched, else None.
    """
    if not PICK_IN_NAME_PATTERN.search(raw_name):
        return None

    round_value = parse_round_value(raw_name)
    if round_value is None:
        return None

    year_match = re.search(r"\b(20\d{2})\b", raw_name)
    year_value = int(year_match.group(1)) if year_match else None

    is_conditional = bool(re.search(r"\bconditional\b", raw_name, re.IGNORECASE))

    return {
        "type": "pick",
        "round": round_value,
        "year": year_value,
        "is_conditional": is_conditional,
    }


def normalize_acquisition(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one acquisition entry.

    Returns None for salary-retention notes (caller appends text to informations instead).
    """
    has_pick = entry.get("draftPickRound") is not None
    raw_name = normalize_text(entry.get("playerName"))
    is_future_flag = bool(entry.get("isFutureConsideration", False))

    if is_future_flag:
        return {"type": "future_consideration"}

    if raw_name and raw_name.casefold() == FUTURE_CONSIDERATIONS_TEXT:
        return {"type": "future_consideration"}

    if has_pick:
        return normalize_pick(entry)

    if raw_name:
        # Salary retention note — signal caller to append to informations
        if SALARY_RETENTION_PATTERN.search(raw_name):
            return None

        # Pick encoded as playerName (e.g. "Conditional 2023 7th Round Pick")
        pick_from_name = try_parse_pick_from_name(raw_name)
        if pick_from_name is not None:
            return pick_from_name

        return normalize_player(entry, raw_name)

    return {"type": "future_consideration"}


def normalize_team(team: dict[str, Any]) -> dict[str, Any]:
    team_id = team.get("competitorId")
    short_name = normalize_text(team.get("shortName"))
    name = normalize_text(team.get("name"))

    if not isinstance(team_id, int):
        raise ValueError(f"Invalid competitorId: {team_id}")
    if not short_name or not name:
        raise ValueError(f"Missing team values: {team}")

    return {"id": team_id, "short": short_name, "name": name}


def normalize_trade(trade: dict[str, Any]) -> dict[str, Any]:
    trade_id = trade.get("tradeId")
    tsn_id = normalize_text(trade.get("id"))
    trade_date = normalize_trade_date(trade.get("tradeDate"))

    if not isinstance(trade_id, int):
        raise ValueError(f"Invalid tradeId: {trade_id}")
    if not tsn_id:
        raise ValueError(f"Missing TSN id for tradeId={trade_id}")

    team_one = normalize_team(trade.get("competitorOne", {}))
    team_two = normalize_team(trade.get("competitorTwo", {}))

    acquisitions = trade.get("tradeAcquisitions", {})
    team_one_receives_raw = acquisitions.get("competitorOne", [])
    team_two_receives_raw = acquisitions.get("competitorTwo", [])
    if not isinstance(team_one_receives_raw, list) or not isinstance(team_two_receives_raw, list):
        raise ValueError(f"Invalid tradeAcquisitions payload in tradeId={trade_id}")

    tsn_meta = trade.get("brandsExtraInfo", {}).get("TSN", {})
    if not isinstance(tsn_meta, dict):
        tsn_meta = {}

    informations = normalize_text(tsn_meta.get("informations"))
    extra_notes: list[str] = []

    def process_side(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for item in raw_items:
            normalized = normalize_acquisition(item)
            if normalized is None:
                # Salary retention note — collect raw name for informations
                note = normalize_text(item.get("playerName"))
                if note:
                    extra_notes.append(note)
            else:
                result.append(normalized)
        return result

    team_one_receives = process_side(team_one_receives_raw)
    team_two_receives = process_side(team_two_receives_raw)

    if extra_notes:
        joined = ". ".join(extra_notes)
        informations = f"{informations}. {joined}" if informations else joined

    normalized_trade = {
        "trade_id": trade_id,
        "tsn_id": tsn_id,
        "trade_date": trade_date,
        "is_canadian_trade": bool(trade.get("isCanadianTrade", False)),
        "is_major_trade": bool(tsn_meta.get("isMajorTrade", False)),
        "tsn_url": normalize_text(tsn_meta.get("url")),
        "informations": informations,
        "team_one": team_one,
        "team_two": team_two,
        "team_one_receives": team_one_receives,
        "team_two_receives": team_two_receives,
    }

    pick_origin_mentions = extract_pick_origin_mentions(informations)
    if pick_origin_mentions:
        normalized_trade["pick_origin_mentions"] = pick_origin_mentions

    return normalized_trade


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False))
            fp.write("\n")


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    logging.info("Loading raw TSN trades from %s", args.input)
    trades = load_trades(args.input)

    normalized: list[dict[str, Any]] = []
    for trade in trades:
        normalized.append(normalize_trade(trade))

    normalized.sort(key=lambda item: (item["trade_date"], item["trade_id"]))
    write_jsonl(args.output, normalized)

    logging.info("Normalized %d trades to %s", len(normalized), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
