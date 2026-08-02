#!/usr/bin/env python3
"""
E6b — Dé-identification des profils qualitatifs (issue c4j)

profiles.jsonl (sortie d'extract_profile.py) porte encore player_name, requis pour
la validation de fuite mais interdit dans les prompts d'entraînement (cf. README :
« Player names are excluded from prompts »).

Le retrait se fait ICI, après génération, de façon déterministe et vérifiable —
plutôt que de compter sur le prompt d'extraction pour ne jamais nommer le joueur.
Vérifié : sur les 727 profils actuels, aucune occurrence à frontière de mot du
prénom ou du nom de famille dans qualitative_summary (la consigne du prompt E6
suffit en pratique). Ce script est le filet, pas la seule ligne de défense.

Motifs retirés, à frontière de mot : nom complet, prénom seul, nom de famille seul
(sur nom composé, chaque partie séparément — ex. « Pierre-Luc » -> « Pierre » et
« Luc »). Pas de base de surnoms : aucune source structurée n'en fournit (cf.
research_player.py, aucun champ nickname) ; en ajouter un sans source vérifiable
serait moins fiable que de ne rien retirer.

Toute correspondance trouvée est une anomalie MAJEURE : la vérité contient encore le
nom quelque part que le retrait déterministe touche mais qui casse la lecture
recommandé — mieux vaut exclure l'entrée que produire une phrase trouée
("depth ___, energy player"), donc rejetée avec les mêmes règles que le module de
fuite d'extract_profile.py.

Lit  data/enriched/profiles.jsonl
Écrit data/enriched/profiles_deidentified.jsonl (sans player_name — le training set)
      data/enriched/deid_key.jsonl (trade_id/receives_key/element_index -> player_name,
      pour la traçabilité en debug ; jamais lu par l'assemblage du dataset)

Usage:
  python pipelines/deidentify_profiles.py
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROFILES_PATH = Path("data/enriched/profiles.jsonl")
OUTPUT_PATH = Path("data/enriched/profiles_deidentified.jsonl")
KEY_PATH = Path("data/enriched/deid_key.jsonl")


def name_patterns(player_name: str) -> list[re.Pattern]:
    parts = re.split(r"[\s-]+", player_name.strip())
    patterns = [player_name]
    patterns.extend(p for p in parts if len(p) > 2)
    return [re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE) for p in patterns]


def redact(summary: str, player_name: str) -> tuple[str, list[str]]:
    hits = []
    for pattern in name_patterns(player_name):
        if pattern.search(summary):
            hits.append(pattern.pattern)
    return summary, hits


def main() -> None:
    if not PROFILES_PATH.exists():
        raise SystemExit(f"{PROFILES_PATH} introuvable — lancer extract_profile.py d'abord")

    kept = 0
    rejected = 0

    with PROFILES_PATH.open() as src, OUTPUT_PATH.open("w", encoding="utf-8") as out, KEY_PATH.open(
        "w", encoding="utf-8"
    ) as keyf:
        for line in src:
            record = json.loads(line)
            player_name = record["player_name"]
            summary = record["qualitative_summary"]

            _, hits = redact(summary, player_name)
            if hits:
                rejected += 1
                log.warning(
                    "[%s] motifs %s trouvés dans qualitative_summary — exclu, à corriger à la main",
                    player_name,
                    hits,
                )
                continue

            deidentified = {k: v for k, v in record.items() if k != "player_name"}
            out.write(json.dumps(deidentified, ensure_ascii=False) + "\n")

            keyf.write(
                json.dumps(
                    {
                        "trade_id": record["trade_id"],
                        "receives_key": record["receives_key"],
                        "element_index": record["element_index"],
                        "player_name": player_name,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            kept += 1

    log.info("%d profils dé-identifiés -> %s", kept, OUTPUT_PATH)
    log.info("%d rejetés pour fuite de nom résiduelle", rejected)


if __name__ == "__main__":
    main()
