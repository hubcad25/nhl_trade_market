#!/usr/bin/env python3
"""Fusionne les trades historiques (nhltradetracker.com) dans trades.jsonl.

classify_elements.py et research_player.py lisent data/normalized/trades.jsonl en
dur (chemin non paramétrable) — la fusion doit donc écrire là, pas dans un fichier
séparé. Idempotent par trade_id (les historiques sont décalés de +1 000 000 par
normalize_nhltradetracker.py, donc jamais en collision avec les trade_id TSN) : une
fusion répétée n'ajoute rien de plus si les sources n'ont pas changé.

Fait un backup horodaté de trades.jsonl avant d'écrire, par précaution — data/ n'est
pas versionné dans ce repo (cf. .gitignore), donc c'est le seul filet.

Usage:
  python pipelines/merge_trade_sources.py
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

TSN_PATH = Path("data/normalized/trades.jsonl")
HISTORIC_PATH = Path("data/resolved/trades_pre_tsn_resolved.jsonl")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as fp:
        return [json.loads(line) for line in fp]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False))
            fp.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tsn", type=Path, default=TSN_PATH)
    parser.add_argument("--historic", type=Path, default=HISTORIC_PATH)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level),
                         format="%(asctime)s | %(levelname)s | %(message)s")

    tsn = load_jsonl(args.tsn)
    historic = load_jsonl(args.historic)

    by_id = {t["trade_id"]: t for t in tsn}
    n_before = len(by_id)
    for t in historic:
        by_id.setdefault(t["trade_id"], t)
    added = len(by_id) - n_before

    merged = sorted(by_id.values(), key=lambda t: (t["trade_date"], t["trade_id"]))

    if not args.no_backup:
        backup = args.tsn.with_suffix(f".jsonl.bak-{int(time.time())}")
        write_jsonl(backup, tsn)
        logging.info("Backup de %s vers %s (%d trades)", args.tsn, backup, len(tsn))

    write_jsonl(args.tsn, merged)
    logging.info("Fusion : %d trades TSN + %d ajoutés depuis nhltradetracker = %d trades dans %s",
                 n_before, added, len(merged), args.tsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
