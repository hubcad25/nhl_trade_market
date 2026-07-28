"""Distill cached article text into a qualitative block per trade element.

One plain API call per element — no agent loop. The article text is already on
disk (data/raw/articles/), so there is nothing to explore.

Two guardrails against label leakage:
  1. The schema and prompt restrict the output to the player himself; the model
     is told never to mention the return or evaluate the trade.
  2. A deterministic validator flags any output that names the other side of the
     trade, the acquiring team, or draft-pick vocabulary.

Usage:
    python pipelines/extract_qualitative.py --limit 15        # pilot
    python pipelines/extract_qualitative.py                   # full run
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
from dotenv import load_dotenv

from pipelines.article_index import (
    CLASSIFIED_PATH,
    TRADES_PATH,
    articles_for_element,
    build_index,
    load_jsonl,
)

# .env wins over the ambient environment: Claude Code exports its own
# ANTHROPIC_API_KEY, which is not a usable Messages API credential.
load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_ARTICLE_CHARS = 12000
OUTPUT_PATH = Path("data/enriched/qualitative.jsonl")

# Element types worth distilling. Picks and future considerations carry no
# player-level qualitative signal — pick value comes from standings instead.
EXTRACTABLE_TYPES = ("nhl_skater", "skater_prospect", "nhl_goalie", "goalie_prospect")

PICK_VOCABULARY = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|1st|2nd|3rd|4th|5th|6th|7th)[- ]round\b"
    r"|\bdraft pick\b|\bround pick\b|\bconditional pick\b",
    re.IGNORECASE,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "player_discussed": {
            "type": "boolean",
            "description": "True only if the article says something substantive about this specific player.",
        },
        "qualitative_summary": {
            "type": ["string", "null"],
            "description": "2-5 sentences describing the player himself: role, playing style, reputation, health, contract situation. Null if player_discussed is false.",
        },
        "qualitative_signals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short phrases lifted from or grounded in the article, e.g. 'elite penalty killer', 'coming off shoulder surgery', 'locker room leader'. Empty if nothing substantive.",
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["player_discussed", "qualitative_summary", "qualitative_signals", "confidence"],
    "additionalProperties": False,
}

SYSTEM = """You extract player scouting information from hockey journalism for a dataset.

Rules, in order of importance:

1. Extract only. Never invent, infer beyond the text, or fill gaps from your own
   knowledge of the player. If the article does not discuss this player in any
   substantive way, set player_discussed to false and return a null summary.

2. Describe ONLY the player named in the request: his role, deployment, playing
   style, reputation, physical profile, health, contract situation, and how he is
   regarded. This is what a team would have known about him.

3. Never mention what the player was traded for, who came back the other way,
   which team acquired him, or any draft pick. Never evaluate the trade or say who
   won it. Those facts are the prediction target of the dataset — including them
   would corrupt it. Write as if describing the player before any trade happened.

4. Neutral, factual register. No hype, no speculation about the future."""

USER_TEMPLATE = """Player: {name}
Type: {type_classified}
Position: {position}
Trade date: {trade_date}

Article text:
---
{article}
---

Extract the qualitative block for {name}."""


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if len(name) >= 2 and name[1] == " " and name[0].isalpha():
        name = name[2:]
    return name


def _return_side_names(trade: dict, receives_key: str) -> list[str]:
    """Player names on the OTHER side of the trade — what this player was traded for."""
    other = "team_two_receives" if receives_key == "team_one_receives" else "team_one_receives"
    names = []
    for el in trade.get(other, []):
        if el.get("type") in ("player", "prospect"):
            clean = _clean_name(el.get("name", ""))
            if clean:
                names.append(clean)
    return names


def validate(result: dict, element: dict, trade: dict) -> list[str]:
    """Return a list of leakage warnings; empty means clean."""
    text = " ".join(
        [result.get("qualitative_summary") or ""] + list(result.get("qualitative_signals") or [])
    )
    if not text.strip():
        return []

    warnings = []
    lowered = text.lower()

    for name in _return_side_names(trade, element["receives_key"]):
        # Match the surname — first names are too common to be reliable signals.
        surname = name.split()[-1]
        if len(surname) > 3 and re.search(rf"\b{re.escape(surname)}\b", lowered, re.IGNORECASE):
            warnings.append(f"names return-side player: {name}")

    receiving = element.get("receiving_team") or {}
    for field in ("name", "short"):
        value = (receiving.get(field) or "").strip()
        if len(value) > 3 and re.search(rf"\b{re.escape(value)}\b", lowered, re.IGNORECASE):
            warnings.append(f"names acquiring team: {value}")
            break

    if PICK_VOCABULARY.search(text):
        warnings.append("mentions draft picks")

    return warnings


def extract_one(client: anthropic.Anthropic, element: dict, article: dict) -> dict:
    el = element["element"]
    name = _clean_name(el.get("tsn_name", ""))

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    name=name,
                    type_classified=el.get("type_classified", ""),
                    position=el.get("position") or "unknown",
                    trade_date=element.get("trade_date", ""),
                    article=article["text"][:MAX_ARTICLE_CHARS],
                ),
            }
        ],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")

    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="stop after N elements")
    args = parser.parse_args()

    trades = {t["trade_id"]: t for t in load_jsonl(TRADES_PATH)}
    index = build_index()
    elements = load_jsonl(CLASSIFIED_PATH)

    todo = []
    for element in elements:
        if element["element"].get("type_classified") not in EXTRACTABLE_TYPES:
            continue
        articles = articles_for_element(element, index)
        if articles:
            todo.append((element, articles[0]))

    if args.limit:
        # Round-robin across types so a small pilot isn't all skaters.
        by_type: dict[str, list] = {}
        for pair in todo:
            by_type.setdefault(pair[0]["element"]["type_classified"], []).append(pair)
        sampled, i = [], 0
        while len(sampled) < args.limit and any(by_type.values()):
            for bucket in by_type.values():
                if i < len(bucket) and len(sampled) < args.limit:
                    sampled.append(bucket[i])
            i += 1
        todo = sampled

    log.info("Extracting %d elements", len(todo))
    client = anthropic.Anthropic()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with OUTPUT_PATH.open("w", encoding="utf-8") as out:
        for element, article in todo:
            el = element["element"]
            name = _clean_name(el.get("tsn_name", ""))
            try:
                result = extract_one(client, element, article)
            except Exception as err:
                log.warning("[%s] extraction failed: %s", name, err)
                continue

            trade = trades.get(element["trade_id"], {})
            record = {
                "trade_id": element["trade_id"],
                "receives_key": element["receives_key"],
                "element_index": element["element_index"],
                "trade_date": element.get("trade_date"),
                "player_name": name,
                "type_classified": el.get("type_classified"),
                **result,
                "qualitative_source_url": article["url"],
                # Every cached search passed end_date=trade_date to Tavily, so the
                # article is bounded to on-or-before the trade. Recorded as a fact
                # about the fetch, not a claim about publication date.
                "qualitative_source_timing": "bounded_by_trade_date",
                "qualitative_extraction_method": "llm_distilled",
                "qualitative_extraction_model": MODEL,
                "leakage_warnings": validate(result, element, trade),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

            flag = " ⚠ " + "; ".join(record["leakage_warnings"]) if record["leakage_warnings"] else ""
            log.info(
                "[%s] discussed=%s conf=%s%s",
                name,
                result["player_discussed"],
                result["confidence"],
                flag,
            )

    log.info("Wrote %d records to %s", written, OUTPUT_PATH)


if __name__ == "__main__":
    main()
