"""
E5b — Escalade des briefs maigres vers un modèle plus cher

Stratégie hybride : la passe complète tourne sur gpt-5.4-mini (~14 $ pour 727
éléments), puis ce script relit les briefs produits, repère ceux qui sont trop
minces, et relance ceux-là seulement sur gpt-5.5 (0,57 $ pièce).

Le mini est honnête sur ses trous — le prompt v3 lui impose « non documenté » plutôt
que de combler — mais il cherche deux fois moins. Un brief à trois sources n'est pas
faux, il est pauvre. Le critère d'escalade se lit donc sur le brief lui-même, sans
jugement humain.

Lit   data/raw/briefs/{source}/{version}/*.json
Écrit data/raw/briefs/{cible}/{version}/*.json (via research_player.research)

Usage:
  python pipelines/escalate_briefs.py --dry-run      # combien, et pour quel coût
  python pipelines/escalate_briefs.py
"""

import sys
import json
import logging
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipelines.research_player import (  # noqa: E402
    BRIEFS_DIR,
    PRICES_PER_M,
    PROMPT_VERSION,
    cache_path,
    load_elements,
    research,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Seuils par défaut, calés sur le pilote : le mini produit 3 à 7 sources par brief,
# gpt-5.5 en produit 8 à 15. En dessous de 5 sources, le brief repose sur trop peu de
# matériel pour qu'on lui fasse confiance ; au-delà de 2 « non documenté », c'est le
# modèle lui-même qui signale qu'il n'a pas trouvé.
DEFAULT_MIN_SOURCES = 5
DEFAULT_MAX_UNDOCUMENTED = 2


def reasons_to_escalate(brief: dict, min_sources: int, max_undocumented: int) -> list[str]:
    """Pourquoi ce brief mérite un second passage. Liste vide = il est bon."""
    reasons = []

    if brief.get("status") != "completed":
        reasons.append(f"status={brief.get('status')}")

    text = brief.get("brief") or ""
    if not text.strip():
        reasons.append("brief vide")

    n_sources = len(brief.get("sources") or [])
    if n_sources < min_sources:
        reasons.append(f"{n_sources} sources")

    n_undocumented = text.count("non documenté")
    if n_undocumented > max_undocumented:
        reasons.append(f"{n_undocumented} « non documenté »")

    return reasons


def load_brief(rec: dict, model: str) -> dict | None:
    path = BRIEFS_DIR / model / PROMPT_VERSION / cache_path(rec, model).name
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="gpt-5.4-mini", choices=sorted(PRICES_PER_M),
                    help="modèle dont on relit les briefs")
    ap.add_argument("--target", default="gpt-5.5", choices=sorted(PRICES_PER_M),
                    help="modèle vers lequel escalader")
    ap.add_argument("--min-sources", type=int, default=DEFAULT_MIN_SOURCES)
    ap.add_argument("--max-undocumented", type=int, default=DEFAULT_MAX_UNDOCUMENTED)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true",
                    help="compter les candidats et estimer le coût, sans appeler")
    args = ap.parse_args()

    records = load_elements()

    candidates = []
    missing = 0
    kept = 0
    for rec in records:
        brief = load_brief(rec, args.source)
        if brief is None:
            missing += 1
            continue
        reasons = reasons_to_escalate(brief, args.min_sources, args.max_undocumented)
        if reasons:
            candidates.append((rec, brief, reasons))
        else:
            kept += 1

    log.info("%d briefs %s (%s) : %d suffisants, %d à escalader, %d absents",
             len(records), args.source, PROMPT_VERSION, kept, len(candidates), missing)

    if missing:
        log.warning("%d éléments n'ont pas de brief %s — lancer d'abord "
                    "research_player.py --model %s", missing, args.source, args.source)

    # Le coût d'un brief cible, estimé sur ce que le pilote a mesuré pour ce modèle.
    p_in, _, p_out = PRICES_PER_M[args.target]
    est_unit = 80_000 / 1e6 * p_in + 5_200 / 1e6 * p_out
    log.info("Coût estimé de l'escalade : %.0f $ (%d × ~%.2f $)",
             len(candidates) * est_unit, len(candidates), est_unit)

    if args.dry_run:
        from collections import Counter
        motifs = Counter(r.split()[-1] if "sources" in r else r.split("=")[0]
                         for _, _, rs in candidates for r in rs)
        for motif, n in motifs.most_common():
            log.info("  motif %-22s %d", motif, n)
        for rec, brief, reasons in candidates[:15]:
            log.info("  %-22s %s", brief["player_name"], " · ".join(reasons))
        if len(candidates) > 15:
            log.info("  … et %d autres", len(candidates) - 15)
        return

    if not candidates:
        return

    session = requests.Session()
    done = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(research, rec, args.target, session, False): (rec, reasons)
            for rec, _, reasons in candidates
        }
        for future in as_completed(futures):
            rec, reasons = futures[future]
            try:
                payload, from_cache = future.result()
            except Exception as e:
                log.error("Échec %s : %s", rec["element"]["tsn_name"], e)
                continue
            with lock:
                done += 1
                if not from_cache:
                    log.info("[%d/%d] %s — %d sources (était : %s)",
                             done, len(candidates), payload["player_name"],
                             len(payload["sources"]), " · ".join(reasons))

    log.info("Escalade terminée — %d/%d éléments repassés en %s",
             done, len(candidates), args.target)


if __name__ == "__main__":
    main()
