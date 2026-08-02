#!/usr/bin/env python3
"""Résout les nhl_id des éléments joueur de trades_pre_tsn.jsonl via la recherche NHL.

CapWages (utilisé par resolve_ids.py pour les trades TSN) est trop récent pour
couvrir 2005-2021 correctement. On utilise plutôt l'API de recherche publique de la
LNH (search.d3.nhle.com), qui indexe tout joueur ayant un jour eu un ID NHL — y
compris les espoirs jamais montés dans la LNH, tant qu'ils ont été repêchés/signés.

Contrairement à resolve_ids.py, qui produit une table plate nom → id (une seule
résolution par nom, réutilisée pour toutes ses occurrences), on résout ici par
occurrence : deux joueurs différents peuvent porter le même nom à des époques
différentes (cf. les deux « Petr Sykora » du jeu de données, actifs 1995-2006 et
2004-2012). Écrire le nhl_id directement sur l'élément — plutôt que dans une table
par nom — laisse classify_elements.py fonctionner sans modification : il lit
`el.get("nhl_id")` avant de retomber sur la table par nom.

Désambiguïsation quand la recherche retourne plusieurs candidats pour un nom : on
choisit celui dont la dernière saison connue se termine au plus tôt après la date du
trade (le candidat dont la carrière couvre plausiblement le trade). Si aucun
candidat n'a joué après le trade, on prend celui dont la carrière s'est terminée le
plus tard avant — mieux qu'un abandon silencieux, mais moins fiable ; ces cas sont
comptés séparément dans les logs.

Lit  data/normalized/trades_pre_tsn.jsonl
Écrit data/resolved/trades_pre_tsn_resolved.jsonl (même schéma, nhl_id peuplé)
      data/resolved/nhltradetracker_name_cache.json (cache des résultats de recherche, par nom)

Usage:
  python pipelines/resolve_nhltradetracker_ids.py
  python pipelines/resolve_nhltradetracker_ids.py --checkpoint-every 50
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

SEARCH_URL = "https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=20&q={query}"
INPUT_PATH = Path("data/normalized/trades_pre_tsn.jsonl")
OUTPUT_PATH = Path("data/resolved/trades_pre_tsn_resolved.jsonl")
CACHE_PATH = Path("data/resolved/nhltradetracker_name_cache.json")

log = logging.getLogger(__name__)

_request_lock = threading.Lock()
_last_request_time = 0.0
REQUEST_INTERVAL = 0.3


def search_player(name: str, retries: int = 5) -> list[dict[str, Any]]:
    global _last_request_time
    url = SEARCH_URL.format(query=quote(name))
    delay = 2.0

    for attempt in range(retries):
        with _request_lock:
            now = time.monotonic()
            wait_for = REQUEST_INTERVAL - (now - _last_request_time)
            if wait_for > 0:
                time.sleep(wait_for)
            _last_request_time = time.monotonic()

        try:
            req = Request(url, headers={"User-Agent": "nhl-trade-market/1.0"})
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                wait = delay * (2 ** attempt)
                log.warning("HTTP %s pour %r — retry dans %.1fs", e.code, name, wait)
                time.sleep(wait)
                continue
            log.error("HTTP %s pour %r — abandon", e.code, name)
            return []
        except (URLError, TimeoutError) as e:
            wait = delay * (2 ** attempt)
            log.warning("Erreur %s pour %r — retry dans %.1fs", e, name, wait)
            time.sleep(wait)

    log.error("Échec définitif pour %r après %d tentatives", name, retries)
    return []


REVERSED_NAME_PATTERN = re.compile(r"^(\w[\w\-]*)\.\s+(\w[\w\-]*)$")


def normalize_for_match(name: str) -> str:
    """Nettoyage tolérant pour comparer deux graphies du même nom — pas pour la
    requête de recherche elle-même. Corrige les artefacts de scraping (espaces
    doubles, apostrophe dactylographique) et le motif 'Nom. Prénom' inversé
    (3 cas observés : 'Carter. Jeff', 'Dvorak. Christian', 'Persson. Joel')."""
    cleaned = name.replace("`", "'").replace("’", "'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    m = REVERSED_NAME_PATTERN.match(cleaned)
    if m:
        cleaned = f"{m.group(2)} {m.group(1)}"
    cleaned = "".join(c for c in unicodedata.normalize("NFKD", cleaned) if not unicodedata.combining(c))
    return cleaned.casefold()


def season_end_year(candidate: dict[str, Any]) -> int | None:
    raw = candidate.get("lastSeasonId")
    if not raw:
        return None
    return int(str(raw)[4:8])


def pick_candidate(candidates: list[dict[str, Any]], name: str, trade_date: str) -> tuple[int | None, bool]:
    """Retourne (nhl_id, ambigu). ambigu=True si plus d'un candidat au nom exact existait.

    La recherche NHL fait du matching flou par jeton — chercher « Todd Fedoruk »
    retourne aussi « Todd Simpson », « Todd MacDonald », etc., partageant juste le
    prénom. On ne désambiguïse donc jamais dans ce bassin flou : sans candidat au
    nom exact (après normalisation — espaces, apostrophes, « Nom. Prénom » inversé),
    on renvoie non résolu plutôt que de deviner. Un vrai nom manqué (nickname
    différent de l'état civil — « Alex Burrows » pour « Alexandre Burrows »,
    « Evgeny Dadonov » pour « Evgenii Dadonov ») est préférable à un mauvais joueur
    assigné en silence : le premier laisse l'élément hors recherche, le second
    produirait un brief entier sur la mauvaise personne."""
    target = normalize_for_match(name)
    exact = [c for c in candidates if normalize_for_match(c["name"]) == target]
    if not exact:
        return None, False
    if len(exact) == 1:
        return int(exact[0]["playerId"]), False

    trade_year = int(trade_date[:4])
    after = [c for c in exact if (ey := season_end_year(c)) is not None and ey >= trade_year]
    if after:
        best = min(after, key=season_end_year)
    else:
        dated = [c for c in exact if season_end_year(c) is not None]
        best = max(dated, key=season_end_year) if dated else exact[0]
    return int(best["playerId"]), True


def load_cache(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    with path.open() as fp:
        return json.load(fp)


def collect_names(trades: list[dict[str, Any]]) -> set[str]:
    names = set()
    for trade in trades:
        for side in ("team_one_receives", "team_two_receives"):
            for el in trade[side]:
                if el["type"] == "player":
                    names.add(el["name"])
    return names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level),
                         format="%(asctime)s %(levelname)s %(message)s")

    with args.input.open() as fp:
        trades = [json.loads(line) for line in fp]

    names = collect_names(trades)
    cache = load_cache(args.cache)
    todo = sorted(n for n in names if n not in cache)
    log.info("%d noms uniques, %d déjà en cache, %d à résoudre", len(names), len(names) - len(todo), len(todo))

    args.cache.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    done = 0

    def process(name: str) -> tuple[str, list[dict[str, Any]]]:
        return name, search_player(name)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process, name): name for name in todo}
        for future in as_completed(futures):
            name, candidates = future.result()
            with lock:
                cache[name] = candidates
                done += 1
                if done % args.checkpoint_every == 0:
                    with args.cache.open("w") as fp:
                        json.dump(cache, fp, ensure_ascii=False, indent=2)
                    log.info("Checkpoint : %d/%d noms résolus", done, len(todo))

    with args.cache.open("w") as fp:
        json.dump(cache, fp, ensure_ascii=False, indent=2)
    log.info("Recherche terminée : %d noms en cache", len(cache))

    n_unresolved = 0
    n_ambiguous = 0
    n_resolved = 0
    for trade in trades:
        for side in ("team_one_receives", "team_two_receives"):
            for el in trade[side]:
                if el["type"] != "player":
                    continue
                candidates = cache.get(el["name"], [])
                nhl_id, ambiguous = pick_candidate(candidates, el["name"], trade["trade_date"])
                el["nhl_id"] = nhl_id
                if ambiguous:
                    el["nhl_id_ambiguous"] = True
                    n_ambiguous += 1
                if nhl_id is None:
                    n_unresolved += 1
                else:
                    n_resolved += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fp:
        for trade in trades:
            fp.write(json.dumps(trade, ensure_ascii=False))
            fp.write("\n")

    log.info("Résolu %d éléments joueur (%d ambigus désambiguïsés par date, %d non résolus) vers %s",
              n_resolved, n_ambiguous, n_unresolved, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
