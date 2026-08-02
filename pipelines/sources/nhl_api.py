"""
Source: NHL API (api-web.nhle.com) — landing de joueur et classement à une date.

Module partagé, pas de script à exécuter directement. Deux fonctions :

  get_player_landing(nhl_id, trade_date)
      Position, tir/attrape, date de naissance, détails de repêchage, et les
      saisons régulières (gameTypeId == 2) en cours ou juste avant trade_date —
      pas toute la carrière, pour rester un instantané à la date du trade.

  get_standings(date_str)
      Classement de chaque équipe à cette date (wins/losses/points/rang ligue),
      pour la formule de tier des picks de 1re/2e ronde (issue ki3).

Même stratégie de retry/rate-limit que classify_elements.py::nhl_get — l'API NHL
n'a pas de clé, une seule connexion globale suffit à rester sous les 429.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

import requests

log = logging.getLogger(__name__)

_request_lock = threading.Lock()
_last_request_time = 0.0
REQUEST_INTERVAL = 1.0  # secondes entre deux requêtes, tous appelants confondus


def nhl_get(url: str, retries: int = 8) -> dict:
    """GET avec backoff exponentiel sur 429/403/erreurs transitoires, et rate limit global."""
    global _last_request_time
    delay = 2.0
    for attempt in range(retries):
        with _request_lock:
            now = time.monotonic()
            wait_for = REQUEST_INTERVAL - (now - _last_request_time)
            if wait_for > 0:
                time.sleep(wait_for)
            _last_request_time = time.monotonic()

        try:
            r = requests.get(url, timeout=15)
            if r.status_code in (429, 403):
                wait = delay * (2 ** attempt)
                log.warning("%s sur %s — nouvelle tentative dans %.1fs", r.status_code, url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            wait = delay * (2 ** attempt)
            log.warning("Erreur %s sur %s — nouvelle tentative dans %.1fs", e, url, wait)
            time.sleep(wait)
    raise RuntimeError(f"Échec après {retries} tentatives : {url}")


def _season_id_for_date(d: date) -> int:
    """Saison LNH (format 20232024) contenant cette date. Bascule au 1er septembre,
    même convention que classify_elements.py::get_gp_and_position_before_date."""
    start_year = d.year if d.month >= 9 else d.year - 1
    return start_year * 10000 + (start_year + 1)


def get_player_landing(nhl_id: int, trade_date: str) -> dict:
    """
    Retourne un instantané du landing du joueur à la date du trade :
    position, shoots_catches, birth_date, draft_details, et season_totals
    limité à la saison en cours + la saison précédente (gameTypeId == 2 seulement).
    """
    data = nhl_get(f"https://api-web.nhle.com/v1/player/{nhl_id}/landing")

    trade_dt = date.fromisoformat(trade_date)
    current_season = _season_id_for_date(trade_dt)
    previous_season = (current_season // 10000 - 1) * 10000 + (current_season // 10000)

    season_totals = [
        s
        for s in data.get("seasonTotals", [])
        if s.get("gameTypeId") == 2 and s.get("season") in (current_season, previous_season)
    ]

    return {
        "position": data.get("position"),
        "shoots_catches": data.get("shootsCatches"),
        "birth_date": data.get("birthDate"),
        "draft_details": data.get("draftDetails"),
        "season_totals": season_totals,
    }


def get_standings(date_str: str) -> list[dict]:
    """
    Classement de chaque équipe à date_str (YYYY-MM-DD) : abréviation, wins,
    losses, ot_losses, points, et le rang ligue (leagueSequence).
    """
    data = nhl_get(f"https://api-web.nhle.com/v1/standings/{date_str}")

    return [
        {
            "team_abbrev": row.get("teamAbbrev", {}).get("default"),
            "wins": row.get("wins"),
            "losses": row.get("losses"),
            "ot_losses": row.get("otLosses"),
            "points": row.get("points"),
            "league_rank": row.get("leagueSequence"),
        }
        for row in data.get("standings", [])
    ]
