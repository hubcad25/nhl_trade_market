#!/usr/bin/env python3
"""Scrape nhltradetracker.com pour les trades antérieurs à la couverture TSN (2022-06+).

TSN n'a jamais eu de trades avant mi-2022 (fenêtre glissante, cf. issue scrape_tsn) ;
nhltradetracker.com couvre 1918-19 à aujourd'hui avec un format constant. On se limite
par défaut à l'ère du plafond salarial (2005-06+), qui est la seule pertinente pour le
modèle — les termes d'un trade avant le cap (pas de rétention, pas de cap hit) ne sont
pas comparables.

Une page = jusqu'à 20 tables de trade (`table[style*="border:1px solid #666666"]`,
un `<input type="hidden">` par table = l'identifiant interne du trade sur le site).
Pagination détectée par page vide plutôt que par les liens `<div class="pagination">`
(plus simple, robuste aux saisons à une seule page qui n'ont pas de pagination du tout).

Usage:
  python pipelines/scrape_nhltradetracker.py
  python pipelines/scrape_nhltradetracker.py --start-season 2005-06 --end-season 2021-22
  python pipelines/scrape_nhltradetracker.py --season 2010-11
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

BASE_URL = "https://www.nhltradetracker.com/user/trade_list_by_season"
USER_AGENT = "nhl-trade-market/1.0 (research; contact via github)"
SHOW_LINK_PATTERN = re.compile(r"javascript:show\('(\d+)\|\|\|\s*(.+?)'\)")

DEFAULT_START_SEASON = "2005-06"
DEFAULT_END_SEASON = "2021-22"  # dernière saison avant le début de la fenêtre TSN (2022-06-16)


def season_range(start: str, end: str) -> list[str]:
    """Génère les slugs de saison 'YYYY-YY' de start à end inclusivement."""
    start_year = int(start.split("-")[0])
    end_year = int(end.split("-")[0])
    return [f"{y}-{str(y + 1)[-2:]}" for y in range(start_year, end_year + 1)]


def fetch_page(season: str, page: int, *, timeout_seconds: int = 30, max_retries: int = 4) -> str:
    url = f"{BASE_URL}/{season}/{page}"
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=timeout_seconds) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as err:
            last_error = err
            if not (err.code == 429 or 500 <= err.code < 600) or attempt == max_retries:
                raise
        except (URLError, TimeoutError) as err:
            last_error = err
            if attempt == max_retries:
                raise

        wait = 1.0 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.3)
        logging.warning("Échec (tentative %d/%d) pour %s : %s. Nouvelle tentative dans %.1fs",
                         attempt, max_retries, url, last_error, wait)
        time.sleep(wait)

    raise RuntimeError(f"Échec du fetch pour {url}: {last_error}")


def parse_side(td) -> list[dict[str, Any]]:
    """Un côté d'échange : joueurs (avec l'id interne du site, tiré du lien JS) et
    tout le reste (picks, future considerations, rétention de salaire) en texte brut,
    classé par la normalisation en aval — pas ici."""
    items: list[dict[str, Any]] = []

    for span in td.find_all("span", class_="black"):
        text = span.get_text(strip=True)
        if text:
            items.append({"type": "other", "text": text})

    for span in td.find_all("span", class_="link"):
        a = span.find("a")
        if not a:
            continue
        match = SHOW_LINK_PATTERN.match(a.get("href", ""))
        if match:
            items.append({
                "type": "player",
                "site_player_id": int(match.group(1)),
                "name": match.group(2).strip(),
            })
        else:
            name = a.get_text(strip=True)
            if name:
                items.append({"type": "player", "site_player_id": None, "name": name})

    return items


def parse_page(html: str, season: str, page: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select('table[style*="border:1px solid #666666"]')

    trades = []
    for table in tables:
        hidden = table.find("input", {"type": "hidden"})
        site_trade_id = hidden["value"] if hidden and hidden.get("value") else None

        headers = [h.get_text(strip=True) for h in table.find_all("td", class_="label")]
        if len(headers) != 3:
            logging.warning("Table de trade avec %d en-têtes (attendu 3) — ignorée, season=%s page=%s",
                             len(headers), season, page)
            continue

        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        data_tds = rows[1].find_all("td", recursive=False)
        if len(data_tds) != 3:
            logging.warning("Table de trade avec %d colonnes de données (attendu 3) — ignorée, "
                             "season=%s page=%s trade=%s", len(data_tds), season, page, site_trade_id)
            continue

        team_one = headers[0].removesuffix(" acquire").strip()
        team_two = headers[2].removesuffix(" acquire").strip()
        date_text = data_tds[1].get_text(strip=True)

        trades.append({
            "site_trade_id": site_trade_id,
            "season": season,
            "date_text": date_text,
            "team_one": team_one,
            "team_two": team_two,
            # côté gauche du tableau = ce que team_one reçoit ; droite = team_two
            "team_one_receives": parse_side(data_tds[0]),
            "team_two_receives": parse_side(data_tds[2]),
        })

    return trades


def scrape_season(season: str, *, request_interval: float) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    page = 1
    while True:
        html = fetch_page(season, page)
        page_trades = parse_page(html, season, page)
        if not page_trades:
            break
        trades.extend(page_trades)
        logging.info("Saison %s, page %d : %d trades", season, page, len(page_trades))
        page += 1
        time.sleep(request_interval)
    return trades


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", help="Ne scraper qu'une seule saison (ex: 2010-11)")
    parser.add_argument("--start-season", default=DEFAULT_START_SEASON,
                         help=f"Première saison (défaut {DEFAULT_START_SEASON})")
    parser.add_argument("--end-season", default=DEFAULT_END_SEASON,
                         help=f"Dernière saison, incluse (défaut {DEFAULT_END_SEASON})")
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw/nhltradetracker"),
                         help="Dossier de sortie (défaut data/raw/nhltradetracker)")
    parser.add_argument("--request-interval", type=float, default=1.0,
                         help="Secondes entre deux requêtes (défaut 1.0)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level),
                         format="%(asctime)s | %(levelname)s | %(message)s")

    seasons = [args.season] if args.season else season_range(args.start_season, args.end_season)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_trades = []
    for season in seasons:
        target = args.output_dir / f"{season}.json"
        if target.exists():
            logging.info("Saison %s déjà scrapée (%s existe) — ignorée", season, target)
            with target.open() as fp:
                all_trades.extend(json.load(fp))
            continue

        season_trades = scrape_season(season, request_interval=args.request_interval)
        with target.open("w", encoding="utf-8") as fp:
            json.dump(season_trades, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
        logging.info("Saison %s : %d trades écrits dans %s", season, len(season_trades), target)
        all_trades.extend(season_trades)

    combined = args.output_dir / "all.json"
    with combined.open("w", encoding="utf-8") as fp:
        json.dump(all_trades, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    logging.info("Total : %d trades sur %d saisons, combiné dans %s", len(all_trades), len(seasons), combined)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
