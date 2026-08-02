#!/usr/bin/env python3
"""Normalize nhltradetracker.com raw trades into the same schema as trades.jsonl.

Lit  data/raw/nhltradetracker/all.json (produit par scrape_nhltradetracker.py)
Écrit data/normalized/trades_pre_tsn.jsonl

Le site distingue déjà joueur vs reste (pick / future consideration / cash / rights)
au niveau du HTML — un lien `javascript:show(...)` pour un joueur avec page dédiée,
du texte brut sinon (cf. scrape_nhltradetracker.py::parse_side). Mais ce texte brut
cache aussi de vrais joueurs : les transactions de type « rights to X » (joueur RFA
non signé, sans page dédiée sur le site) sont rendues en texte brut alors que ce sont
des éléments joueur pour research_player.py. Ce module les récupère par motif
(`^rights to ...`) avant de traiter le reste comme pick / future consideration.

trade_id : décalé de 1 000 000 pour ne jamais collisionner avec les trade_id TSN
(actuellement < 6 000) — les deux fichiers sont fusionnés en aval par
merge_trade_sources.py, pas ici.

Usage:
  python pipelines/normalize_nhltradetracker.py
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

TRADE_ID_OFFSET = 1_000_000

TEAM_ABBREV = {
    "Anaheim Ducks": "ANA",
    "Arizona Coyotes": "ARI",
    "Atlanta Thrashers": "ATL",
    "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR",
    "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL",
    "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM",
    "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK",
    "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL",
    "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD",
    "New York Islanders": "NYI",
    "New York Rangers": "NYR",
    "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR",
    "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}
# id synthétique stable — arbitraire, jamais consommé ailleurs que comme clé d'affichage
TEAM_ID = {name: i + 1 for i, name in enumerate(sorted(TEAM_ABBREV))}

RIGHTS_PATTERN = re.compile(
    r"^rights to (?:the )?(?:forward |defenseman |defenceman |goalie )?(.+)$",
    re.IGNORECASE,
)
FUTURE_CONSIDERATION_PATTERN = re.compile(
    r"^(future consideration|cash|no return|expansion draft consideration)",
    re.IGNORECASE,
)
ROUND_PATTERN = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s*[-\s]*round", re.IGNORECASE)
ROUND_WORD_TO_NUM = {
    "first": 1, "second": 2, "third": 3, "fourth": 4,
    "fifth": 5, "sixth": 6, "seventh": 7,
}
YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
CONDITIONAL_PATTERN = re.compile(r"\bconditional|conditionnal\b", re.IGNORECASE)
PICK_PATTERN = re.compile(r"\b(round|pick)\b", re.IGNORECASE)


def parse_date(date_text: str) -> str:
    return datetime.strptime(date_text, "%B %d, %Y").date().isoformat()


def normalize_team(name: str) -> dict[str, Any]:
    if name not in TEAM_ABBREV:
        raise ValueError(f"Équipe inconnue, ajouter à TEAM_ABBREV : {name!r}")
    return {"id": TEAM_ID[name], "short": TEAM_ABBREV[name], "name": name}


def parse_round(text: str) -> int | None:
    m = ROUND_PATTERN.search(text)
    if m:
        return int(m.group(1))
    lowered = text.lower()
    for word, value in ROUND_WORD_TO_NUM.items():
        if word in lowered:
            return value
    return None


def normalize_element(el: dict[str, Any]) -> dict[str, Any]:
    if el["type"] == "player":
        return {
            "type": "player",
            "nhl_id": None,
            "name": el["name"].strip(),
            "site_player_id": el.get("site_player_id"),
        }

    text = el["text"].strip()

    rights_match = RIGHTS_PATTERN.match(text)
    if rights_match:
        return {
            "type": "player",
            "nhl_id": None,
            "name": rights_match.group(1).strip(),
            "site_player_id": None,
            "rights_only": True,
        }

    if FUTURE_CONSIDERATION_PATTERN.match(text):
        return {"type": "future_consideration", "raw_text": text}

    if PICK_PATTERN.search(text):
        year_match = YEAR_PATTERN.search(text)
        return {
            "type": "pick",
            "round": parse_round(text),
            "year": int(year_match.group(0)) if year_match else None,
            "is_conditional": bool(CONDITIONAL_PATTERN.search(text)),
            "raw_text": text,
        }

    # Cas résiduels non reconnus (options d'équipe, notes diverses) — jamais
    # traités comme joueur : mieux vaut perdre l'élément que polluer la
    # recherche par joueur avec un nom qui n'en est pas un.
    logging.warning("Élément non reconnu, classé future_consideration par défaut : %r", text)
    return {"type": "future_consideration", "raw_text": text}


def normalize_trade(raw: dict[str, Any]) -> dict[str, Any]:
    site_trade_id = int(raw["site_trade_id"])
    return {
        "trade_id": TRADE_ID_OFFSET + site_trade_id,
        "source": "nhltradetracker",
        "source_trade_id": site_trade_id,
        "trade_date": parse_date(raw["date_text"]),
        "team_one": normalize_team(raw["team_one"]),
        "team_two": normalize_team(raw["team_two"]),
        "team_one_receives": [normalize_element(e) for e in raw["team_one_receives"]],
        "team_two_receives": [normalize_element(e) for e in raw["team_two_receives"]],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/raw/nhltradetracker/all.json"))
    parser.add_argument("--output", type=Path, default=Path("data/normalized/trades_pre_tsn.jsonl"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level),
                         format="%(asctime)s | %(levelname)s | %(message)s")

    with args.input.open() as fp:
        raw_trades = json.load(fp)

    normalized = [normalize_trade(t) for t in raw_trades]
    normalized.sort(key=lambda t: (t["trade_date"], t["trade_id"]))

    ids = [t["trade_id"] for t in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("trade_id dupliqués après normalisation — collision site_trade_id")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fp:
        for trade in normalized:
            fp.write(json.dumps(trade, ensure_ascii=False))
            fp.write("\n")

    n_players = sum(
        1 for t in normalized for side in ("team_one_receives", "team_two_receives")
        for el in t[side] if el["type"] == "player"
    )
    n_rights_only = sum(
        1 for t in normalized for side in ("team_one_receives", "team_two_receives")
        for el in t[side] if el.get("rights_only")
    )
    logging.info("Normalisé %d trades (%d éléments joueur, dont %d 'rights to') vers %s",
                 len(normalized), n_players, n_rights_only, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
