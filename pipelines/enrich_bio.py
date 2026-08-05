#!/usr/bin/env python3
"""
E7b — Âge au trade, gabarit (taille) et repêchage

Pour chaque élément joueur du scope wm9 (les 727 avec profil E6 complet) : âge à
trade_date depuis la date de naissance NHL API, taille (pouces), et détails de
repêchage (année/ronde/rang au total, si drafté). Un seul appel API par nhl_id
unique — aucun de ces champs ne dépend du trade, et beaucoup de joueurs
apparaissent dans plusieurs trades du dataset.

Poids délibérément omis : il varie dans le temps (contrairement à la taille adulte)
et le landing NHL n'en donne qu'un instantané au moment de l'appel, pas à trade_date
— l'inclure serait une fuite mineure du présent vers le passé pour les trades anciens.

Lit  data/enriched/profiles_deidentified.jsonl (scope : quels éléments ont besoin d'un bio)
     data/resolved/classified_elements.jsonl (nhl_id par élément)
Écrit data/raw/player_bio/{nhl_id}.json (cache, un fichier par joueur)
      data/enriched/bio.jsonl (assemblage final, réécrit à chaque run)

Usage:
  python pipelines/enrich_bio.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.nhl_api import get_player_bio  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROFILES_PATH = Path("data/enriched/profiles_deidentified.jsonl")
CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
BIO_CACHE_DIR = Path("data/raw/player_bio")
BIO_PATH = Path("data/enriched/bio.jsonl")


def key_of(rec: dict) -> tuple:
    return (rec["trade_id"], rec["receives_key"], rec["element_index"])


def load_classified_index() -> dict[tuple, dict]:
    index = {}
    with open(CLASSIFIED_PATH) as f:
        for line in f:
            rec = json.loads(line)
            index[key_of(rec)] = rec["element"]
    return index


def age_at(birth_date: str, trade_date: str) -> int:
    b = date.fromisoformat(birth_date)
    t = date.fromisoformat(trade_date)
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def cached_bio(nhl_id: int) -> dict:
    path = BIO_CACHE_DIR / f"{nhl_id}.json"
    if path.exists():
        with open(path) as f:
            bio = json.load(f)
        if "draft_year" in bio:
            return bio
    bio = get_player_bio(nhl_id)
    bio["nhl_id"] = nhl_id
    BIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(bio, f)
    tmp.replace(path)
    return bio


def main() -> None:
    classified = load_classified_index()

    scope = []
    with open(PROFILES_PATH) as f:
        for line in f:
            scope.append(json.loads(line))

    log.info("%d éléments dans le scope", len(scope))

    results = []
    skipped = 0
    bio_cache: dict[int, dict] = {}

    for i, rec in enumerate(scope, start=1):
        key = key_of(rec)
        element = classified.get(key, {})
        nhl_id = element.get("nhl_id")
        if not nhl_id:
            skipped += 1
            continue

        if nhl_id not in bio_cache:
            bio_cache[nhl_id] = cached_bio(nhl_id)
        bio = bio_cache[nhl_id]
        if not bio.get("birth_date"):
            skipped += 1
            continue

        results.append({
            "trade_id": rec["trade_id"],
            "receives_key": rec["receives_key"],
            "element_index": rec["element_index"],
            "nhl_id": nhl_id,
            "birth_date": bio["birth_date"],
            "age_at_trade": age_at(bio["birth_date"], rec["trade_date"]),
            "height_in": bio.get("height_in"),
            "draft_year": bio.get("draft_year"),
            "draft_round": bio.get("draft_round"),
            "draft_overall": bio.get("draft_overall"),
        })

        if i % 100 == 0:
            log.info("Progress: %d/%d (%d joueurs uniques vus)", i, len(scope), len(bio_cache))

    log.info("%d éléments sans nhl_id ou sans date de naissance, ignorés", skipped)

    BIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    results.sort(key=lambda r: (r["trade_id"], r["receives_key"], r["element_index"]))
    with open(BIO_PATH, "w") as out:
        for r in results:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    log.info("Terminé : %d bios écrites -> %s (%d joueurs uniques interrogés)", len(results), BIO_PATH, len(bio_cache))


if __name__ == "__main__":
    main()
