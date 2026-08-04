#!/usr/bin/env python3
"""
E: Estimation des tiers de picks (issue ki3)

Pour chaque élément 'pick' de data/resolved/classified_elements.jsonl, produit une
estimation du rang de sélection à la date du trade, sans jamais utiliser le
résultat réel du repêchage (qui n'existe pas encore à trade_date pour les picks
imminents, et fuirait le futur pour les autres).

Formule (picks de 1re ET 2e ronde, draft imminent = même année que le trade ou
l'année suivante, non conditionnels) :
  - classement NHL (pipelines.sources.nhl_api.get_standings) à trade_date
  - rang de repêchage de l'équipe propriétaire du pick = ligue à l'envers
    (pire équipe = rang 1 = 1er choix), avec repli sur le dernier classement
    disponible si trade_date tombe en entre-saison (droite règle avril-septembre)
  - ronde 2 = decalée de num_teams (ex: équipe classée pire de la ligue -> pick
    #1 en ronde 1, #(num_teams+1) en ronde 2)
  - fenêtre d'estimation ±2 autour du rang; phrasé 'top N' si le rang est parmi
    les 10 premiers (usage courant en repêchage), sinon 'lo-hi'

Picks conditionnels ou hors scope (ronde 3+, 2+ ans dans le futur, ou le passé) :
  pas de formule, juste l'info brute TSN (estimated_pick_range = null, ou
  'conditionnel' si applicable et par ailleurs éligible).

Gère les relocations de franchise dans la fenêtre 2005-présent (Atlanta/Winnipeg,
Phoenix/Arizona/Utah) : le classement NHL renvoie l'abréviation de l'époque
(ex: PHX avant 2014), alors que nos données normalisent au nom de franchise
actuel (ARI) — on essaie les alias connus avant d'abandonner.

Lit  data/resolved/classified_elements.jsonl (éléments type_classified='pick')
Écrit data/raw/picks/{trade_id}-{one|two}-{index}.json (cache, un fichier par élément)
      data/enriched/picks.jsonl (assemblage final, réécrit à chaque run)

Usage:
  python pipelines/enrich_pick.py
  python pipelines/enrich_pick.py --limit 20
  python pipelines/enrich_pick.py --force
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.nhl_api import get_standings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
PICKS_CACHE_DIR = Path("data/raw/picks")
PICKS_PATH = Path("data/enriched/picks.jsonl")

# Relocations de franchise dans la fenêtre couverte (2005-présent). Le classement
# NHL renvoie l'abréviation en usage à trade_date ; nos données normalisent au
# nom de franchise actuel.
FRANCHISE_ALIASES = {
    "ARI": ["ARI", "PHX", "UTA"],
    "PHX": ["PHX", "ARI", "UTA"],
    "UTA": ["UTA", "ARI", "PHX"],
    "ATL": ["ATL", "WPG"],
    "WPG": ["WPG", "ATL"],
}

# Entre-saison approximative (fin saison régulière -> début suivante) : le
# classement à une date dans cette fenêtre est vide, on retombe sur la fin de
# la saison qui vient de se terminer.
OFFSEASON_START = (4, 20)
OFFSEASON_END = (9, 15)

EARLY_PICK_THRESHOLD = 10
RANGE_MARGIN = 2


def load_picks(limit: int | None) -> list[dict]:
    if not CLASSIFIED_PATH.exists():
        raise SystemExit(f"{CLASSIFIED_PATH} introuvable")

    picks = []
    with open(CLASSIFIED_PATH) as f:
        for line in f:
            rec = json.loads(line)
            if rec["element"]["type_classified"] == "pick":
                picks.append(rec)

    if limit:
        picks = picks[:limit]
    return picks


def cache_path(trade_id: int, receives_key: str, element_index: int) -> Path:
    side = "one" if receives_key == "team_one_receives" else "two"
    return PICKS_CACHE_DIR / f"{trade_id}-{side}-{element_index}.json"


def _candidate_dates(trade_date: str) -> list[date]:
    d = date.fromisoformat(trade_date)
    candidates = [d]
    if OFFSEASON_START <= (d.month, d.day) < OFFSEASON_END:
        candidates.append(date(d.year, 4, 19))
    for offset in (15, 30, 60, 90, 120, 150, 180):
        candidates.append(d - timedelta(days=offset))
    return candidates


def get_standings_near(trade_date: str, cache: dict[str, list[dict]]) -> list[dict]:
    """Classement à trade_date, avec repli sur les dates antérieures si vide
    (entre-saison). Mémoïsé — beaucoup d'éléments partagent la même trade_date."""
    if trade_date in cache:
        return cache[trade_date]

    standings: list[dict] = []
    for cand in _candidate_dates(trade_date):
        key = cand.isoformat()
        if key in cache:
            standings = cache[key]
        else:
            standings = get_standings(key)
            cache[key] = standings
        if standings:
            break

    cache[trade_date] = standings
    return standings


def resolve_team_row(standings: list[dict], short: str) -> dict | None:
    by_abbrev = {row["team_abbrev"]: row for row in standings}
    for alias in FRANCHISE_ALIASES.get(short, [short]):
        if alias in by_abbrev:
            return by_abbrev[alias]
    return None


def estimate_pick_range(draft_rank: int, round_num: int, num_teams: int) -> str:
    """rang ± quelques positions, ex: rang 5 en ronde 1 -> 'top 7'."""
    if round_num == 1:
        base = draft_rank
        round_start, round_end = 1, num_teams
    else:
        base = num_teams + draft_rank
        round_start, round_end = num_teams + 1, num_teams * 2

    lo = max(round_start, base - RANGE_MARGIN)
    hi = min(round_end, base + RANGE_MARGIN)

    if base <= EARLY_PICK_THRESHOLD:
        return f"top {hi}"
    return f"{lo}-{hi}"


def enrich(rec: dict, standings_cache: dict[str, list[dict]]) -> dict:
    raw = rec["element"]["raw_tsn_element"]
    round_num = raw["round"]
    draft_year = raw["year"]
    is_conditional = raw["is_conditional"]
    original_owner = rec["giving_team"]["short"]
    trade_date = rec["trade_date"]
    trade_year = int(trade_date[:4])

    delta = draft_year - trade_year if draft_year is not None else None
    formula_scope = round_num in (1, 2) and delta in (0, 1)

    estimated_pick_range: str | None = None
    if formula_scope:
        if is_conditional:
            estimated_pick_range = "conditionnel"
        else:
            standings = get_standings_near(trade_date, standings_cache)
            row = resolve_team_row(standings, original_owner) if standings else None
            if row is None:
                log.warning(
                    "Classement introuvable pour %s à %s (trade_id=%s) — estimated_pick_range=null",
                    original_owner, trade_date, rec["trade_id"],
                )
            else:
                num_teams = len(standings)
                draft_rank = num_teams + 1 - row["league_rank"]
                estimated_pick_range = estimate_pick_range(draft_rank, round_num, num_teams)

    return {
        "trade_id": rec["trade_id"],
        "trade_date": trade_date,
        "receives_key": rec["receives_key"],
        "element_index": rec["element_index"],
        "pick": {
            "type": "pick",
            "round": round_num,
            "draft_year": draft_year,
            "original_owner": original_owner,
            "is_conditional": is_conditional,
            "estimated_pick_range": estimated_pick_range,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    picks = load_picks(args.limit)
    log.info("%d éléments 'pick' à traiter", len(picks))

    PICKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    standings_cache: dict[str, list[dict]] = {}
    results = []

    for i, rec in enumerate(picks, start=1):
        path = cache_path(rec["trade_id"], rec["receives_key"], rec["element_index"])
        if path.exists() and not args.force:
            with open(path) as f:
                results.append(json.load(f))
            continue

        payload = enrich(rec, standings_cache)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        results.append(payload)

        if i % 100 == 0:
            log.info("Progress: %d/%d", i, len(picks))

    PICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    results.sort(key=lambda r: (r["trade_id"], r["receives_key"], r["element_index"]))
    with open(PICKS_PATH, "w") as out:
        for r in results:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    log.info("Terminé : %d picks écrits -> %s", len(results), PICKS_PATH)


if __name__ == "__main__":
    main()
