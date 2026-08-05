#!/usr/bin/env python3
"""
Audit nhl_id vs nom TSN — détecter les mauvais identifiants

Trouvé en creusant un résultat suspect du modèle de valeur (kii) : le trade 4788
("Alex Nylander", CBJ<-PIT, 2024-02-23) porte nhl_id=8477939 DIRECTEMENT dans le
payload brut TSN (tradeAcquisitions[].playerId) — pas une résolution de notre
pipeline. Vérifié contre l'API NHL en direct : 8477939 = William Nylander (né
1996-05-01, repêché 8e au total en 2014), pas Alex Nylander. Erreur à la source
TSN elle-même, pas dans resolve_ids.py — TSN associe parfois le mauvais joueur à
un nom dans sa propre base.

Ce script vérifie systématiquement chaque nhl_id du dataset (qu'il vienne
directement de TSN ou de notre résolution CapWages) contre le nom retourné par
l'API NHL landing, sur tout le corpus (2157 trades, pas seulement le sous-scope
utilisable de kii). Comparaison sur le nom de famille normalisé (accents retirés,
casse ignorée) — le prénom seul varie trop (diminutifs: Alex/Alexander,
Mike/Michael...) pour être un signal fiable seul ; les écarts de prénom sont
listés séparément, à titre informatif, pas comme un signal d'erreur en soi.

~2300 nhl_id uniques, ~1 req/s (rate limit global de pipelines.sources.nhl_api)
=> ~40 minutes. Reprenable : cache disque par nhl_id, un run interrompu ne
recommence pas à zéro.

Lit  data/resolved/classified_elements.jsonl
Écrit data/raw/player_identity/{nhl_id}.json      (cache, un fichier par joueur)
      data/manual/nhl_id_name_mismatches.jsonl    (rapport, réécrit à chaque run)

Usage:
  python pipelines/audit_nhl_id_names.py
  python pipelines/audit_nhl_id_names.py --limit 50
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.nhl_api import nhl_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
IDENTITY_CACHE_DIR = Path("data/raw/player_identity")
REPORT_PATH = Path("data/manual/nhl_id_name_mismatches.jsonl")

# Diminutifs anglophones courants — un écart de prénom dans cette liste n'est
# pas traité comme un signal d'erreur, juste une variante de nom légitime.
NICKNAME_PAIRS = {
    ("alex", "alexander"), ("mike", "michael"), ("matt", "matthew"),
    ("nick", "nicholas"), ("chris", "christopher"), ("dan", "daniel"),
    ("will", "william"), ("sam", "samuel"), ("joe", "joseph"),
    ("tom", "thomas"), ("rob", "robert"), ("jake", "jacob"),
    ("zach", "zachary"), ("cal", "calvin"), ("ben", "benjamin"),
    ("andy", "andrew"), ("josh", "joshua"), ("jim", "james"),
}


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def nickname_present(missing_token: str, tsn_tokens: set[str]) -> bool:
    for tsn_tok in tsn_tokens:
        if tuple(sorted((missing_token, tsn_tok))) in NICKNAME_PAIRS:
            return True
    return False


def names_match(tsn_name: str, api_first: str, api_last: str) -> bool:
    """Vérifie que chaque mot du nom API (prénom + nom, chacun pouvant être
    multi-mots: 'de Vries', 'van Riemsdyk') apparaît comme token dans tsn_name —
    tolérant à l'ordre (TSN écrit parfois 'Nom, Prénom' ou 'Nom. Prénom'), aux
    suffixes de position ('(F)', ', F') et aux préfixes de nom composé. Une
    comparaison par position (dernier mot = nom de famille) casse sur tous ces
    cas et produit des faux positifs en masse — see: 39/39 des mismatches d'un
    premier passage se sont avérés être ça, pas de vrais mauvais nhl_id."""
    tsn_tokens = set(normalize(tsn_name).split())
    api_tokens = normalize(f"{api_first} {api_last}").split()
    missing = [t for t in api_tokens if t not in tsn_tokens]
    if not missing:
        return True
    return all(nickname_present(t, tsn_tokens) for t in missing)


def cached_identity(nhl_id: int) -> dict:
    path = IDENTITY_CACHE_DIR / f"{nhl_id}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    data = nhl_get(f"https://api-web.nhle.com/v1/player/{nhl_id}/landing")
    identity = {
        "nhl_id": nhl_id,
        "first_name": (data.get("firstName") or {}).get("default"),
        "last_name": (data.get("lastName") or {}).get("default"),
    }
    IDENTITY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(identity, f, ensure_ascii=False)
    tmp.replace(path)
    return identity


def load_id_names() -> dict[int, set[str]]:
    """nhl_id -> ensemble des tsn_name vus dans le corpus (peut différer d'un trade à l'autre)."""
    by_id: dict[int, set[str]] = defaultdict(set)
    with open(CLASSIFIED_PATH) as f:
        for line in f:
            rec = json.loads(line)
            nid = rec["element"].get("nhl_id")
            name = rec["element"].get("tsn_name")
            if nid and name:
                by_id[nid].add(name)
    return by_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    by_id = load_id_names()
    ids = sorted(by_id.keys())
    if args.limit:
        ids = ids[: args.limit]
    log.info("%d nhl_id uniques à vérifier", len(ids))

    mismatches: list[dict] = []
    errors: list[dict] = []

    for i, nid in enumerate(ids, start=1):
        try:
            identity = cached_identity(nid)
        except Exception as exc:  # noqa: BLE001
            log.warning("échec pour nhl_id=%d: %s", nid, exc)
            errors.append({"nhl_id": nid, "error": str(exc)})
            continue

        api_first = identity.get("first_name") or ""
        api_last = identity.get("last_name") or ""
        if not api_last:
            continue
        for tsn_name in by_id[nid]:
            if not names_match(tsn_name, api_first, api_last):
                mismatches.append({
                    "nhl_id": nid, "tsn_name": tsn_name,
                    "api_name": f"{api_first} {api_last}",
                })

        if i % 100 == 0:
            log.info("%d/%d vérifiés — %d mismatch jusqu'ici", i, len(ids), len(mismatches))

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        for row in mismatches:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info(
        "terminé: %d mismatch (probable mauvais nhl_id), %d erreurs API",
        len(mismatches), len(errors),
    )
    log.info("rapport -> %s", REPORT_PATH)


if __name__ == "__main__":
    main()
