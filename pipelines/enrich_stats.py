#!/usr/bin/env python3
"""
E7 — Stats NHL coupées à la date du trade (issue n7z)

Calcule, pour chaque élément joueur déjà passé par la recherche E5 (data/raw/briefs),
ses stats réelles coupées à la veille du trade (pipelines.sources.nhl_api.get_stats_before_date) —
saison en cours ET carrière, exactes, jamais approximées.

Un détecteur d'hallucination par regex (comparer les chiffres cités dans le brief
aux vraies stats) a été tenté et abandonné : les briefs sont en français, mélangent
librement saison et carrière, et les faux positifs (ids d'URL, stats de ligue
mineure, formulations variées) dominaient largement le signal sur un échantillon
test. La revue manuelle (issue c4z) reste le filet qualité.

Scope volontairement limité aux 727 éléments déjà chargés dans
data/raw/briefs/{SOURCE_MODEL}/{SOURCE_PROMPT_VERSION}/ — pas la passe étendue
(5046 éléments post-fusion nhltradetracker), qui n'a pas encore de briefs E5.

Lit  data/raw/briefs/gpt-5.4-mini/v6/*.json (status=completed)
     data/resolved/classified_elements.jsonl (pour nhl_id/position)
Écrit data/raw/stats/{trade_id}-{one|two}-{index}.json (cache, un fichier par élément)
      data/enriched/stats.jsonl (assemblage final, réécrit à chaque run)

Usage:
  python pipelines/enrich_stats.py
  python pipelines/enrich_stats.py --limit 20
  python pipelines/enrich_stats.py --force
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.nhl_api import get_stats_before_date  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BRIEFS_DIR = Path("data/raw/briefs")
# Surchargeable via --source-model/--source-version pour traiter des briefs venus
# d'une autre source E5 (ex. claude-sonnet-5/v6 pour le sous-ensemble élite, issue
# b7e) sans perdre les stats déjà calculées pour gpt-5.4-mini/v6 — voir merge dans
# main(), qui réassemble stats.jsonl depuis TOUT le cache disque, pas seulement le
# scope du run courant.
SOURCE_MODEL = "gpt-5.4-mini"
SOURCE_PROMPT_VERSION = "v6"

CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
STATS_CACHE_DIR = Path("data/raw/stats")
STATS_PATH = Path("data/enriched/stats.jsonl")


def load_briefs(limit: int | None) -> list[dict]:
    briefs_dir = BRIEFS_DIR / SOURCE_MODEL / SOURCE_PROMPT_VERSION
    if not briefs_dir.exists():
        raise SystemExit(f"{briefs_dir} introuvable — l'étape 5 n'a pas produit de briefs pour ce modèle/version")

    briefs = []
    for path in sorted(briefs_dir.glob("*.json")):
        with open(path) as f:
            payload = json.load(f)
        if payload.get("status") != "completed":
            continue
        briefs.append(payload)

    if limit:
        briefs = briefs[:limit]
    return briefs


def load_classified_index() -> dict[tuple, dict]:
    index = {}
    with open(CLASSIFIED_PATH) as f:
        for line in f:
            rec = json.loads(line)
            key = (rec["trade_id"], rec["receives_key"], rec["element_index"])
            index[key] = rec["element"]
    return index


def cache_path(trade_id: int, receives_key: str, element_index: int) -> Path:
    side = "one" if receives_key == "team_one_receives" else "two"
    return STATS_CACHE_DIR / f"{trade_id}-{side}-{element_index}.json"


def process(brief: dict, element: dict, force: bool) -> dict:
    path = cache_path(brief["trade_id"], brief["receives_key"], brief["element_index"])
    if path.exists() and not force:
        with open(path) as f:
            return json.load(f)

    stats = get_stats_before_date(element["nhl_id"], brief["trade_date"], element["position"])

    payload = {
        "trade_id": brief["trade_id"],
        "trade_date": brief["trade_date"],
        "receives_key": brief["receives_key"],
        "element_index": brief["element_index"],
        "nhl_id": element["nhl_id"],
        "type_classified": element["type_classified"],
        "stats": stats,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--source-model", default=SOURCE_MODEL,
        help="dossier data/raw/briefs/{source-model}/{source-version}/ à lire (défaut: gpt-5.4-mini)",
    )
    ap.add_argument(
        "--source-version", default=SOURCE_PROMPT_VERSION,
        help="version de prompt E5 des briefs à lire (défaut: v6)",
    )
    args = ap.parse_args()

    global SOURCE_MODEL, SOURCE_PROMPT_VERSION
    SOURCE_MODEL = args.source_model
    SOURCE_PROMPT_VERSION = args.source_version

    briefs = load_briefs(args.limit)
    classified = load_classified_index()

    work = []
    skipped = 0
    for brief in briefs:
        key = (brief["trade_id"], brief["receives_key"], brief["element_index"])
        element = classified.get(key)
        if not element or not element.get("nhl_id"):
            skipped += 1
            continue
        work.append((brief, element))

    log.info("%d éléments à traiter (%d sans nhl_id résolu, ignorés)", len(work), skipped)

    STATS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    lock = threading.Lock()
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process, brief, element, args.force): brief for brief, element in work}
        for future in as_completed(futures):
            brief = futures[future]
            try:
                payload = future.result()
            except Exception as e:
                log.error("Échec trade_id=%s: %s", brief["trade_id"], e)
                continue
            with lock:
                done += 1
                results.append(payload)
                if done % 100 == 0:
                    log.info("Progress: %d/%d", done, len(work))

    # Réassemble stats.jsonl depuis TOUT le cache disque, pas seulement le scope du
    # run courant (SOURCE_MODEL/SOURCE_PROMPT_VERSION) — sinon un run scopé à une
    # autre source de briefs écraserait silencieusement les stats déjà calculées
    # pour les autres. trade_id suffit à dédupliquer (TSN et nhltradetracker ne se
    # chevauchent jamais).
    merged: dict[tuple, dict] = {}
    for path in STATS_CACHE_DIR.glob("*.json"):
        with open(path) as f:
            r = json.load(f)
        merged[(r["trade_id"], r["receives_key"], r["element_index"])] = r
    for r in results:
        merged[(r["trade_id"], r["receives_key"], r["element_index"])] = r

    all_results = sorted(merged.values(), key=lambda r: (r["trade_id"], r["receives_key"], r["element_index"]))
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_PATH, "w") as out:
        for r in all_results:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    log.info("Terminé : %d/%d nouvelles stats (%d au total dans le cache) -> %s",
              len(results), len(work), len(all_results), STATS_PATH)


if __name__ == "__main__":
    main()
