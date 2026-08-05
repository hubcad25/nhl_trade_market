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

  get_available_seasons(nhl_id) / get_game_log(nhl_id, season) / get_stats_before_date(...)
      Logique de game log — extraite de classify_elements.py, qui ne s'en servait
      que pour compter les GP avant le trade. Réutilisée ici pour produire des
      stats complètes coupées à la date (issue n7z).

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


class PlayerNotFoundError(Exception):
    """404 permanent (ex: nhl_id d'un ancien format absent de l'API moderne) —
    pas une erreur transitoire, ne pas retenter."""


def nhl_get(url: str, retries: int = 8) -> dict:
    """GET avec backoff exponentiel sur 429/403/erreurs transitoires, et rate limit global.
    Un 404 échoue immédiatement (PlayerNotFoundError), sans retry : retenter avec
    backoff exponentiel un identifiant qui n'existe pas ne fait qu'attendre plusieurs
    minutes pour le même résultat."""
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
            if r.status_code == 404:
                raise PlayerNotFoundError(url)
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


def get_player_bio(nhl_id: int) -> dict:
    """Date de naissance, gabarit et détails de repêchage — aucun des trois ne
    dépend de trade_date, contrairement à get_player_landing."""
    data = nhl_get(f"https://api-web.nhle.com/v1/player/{nhl_id}/landing")
    draft = data.get("draftDetails") or {}
    return {
        "birth_date": data.get("birthDate"),
        "height_in": data.get("heightInInches"),
        "weight_lb": data.get("weightInPounds"),
        "draft_year": draft.get("year"),
        "draft_round": draft.get("round"),
        "draft_overall": draft.get("overallPick"),
    }


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


def get_available_seasons(nhl_id: int) -> list[int]:
    """Saisons régulières (format 20222023) disponibles pour ce joueur."""
    data = nhl_get(f"https://api-web.nhle.com/v1/player/{nhl_id}/game-log/now")
    return [
        s["season"]
        for s in data.get("playerStatsSeasons", [])
        if 2 in s.get("gameTypes", [])
    ]


def get_game_log(nhl_id: int, season: int) -> list[dict]:
    """Log de match brut pour une saison régulière donnée (format 20222023)."""
    data = nhl_get(f"https://api-web.nhle.com/v1/player/{nhl_id}/game-log/{season}/2")
    return data.get("gameLog", [])


def _toi_to_seconds(toi: str) -> int:
    if not toi:
        return 0
    minutes, _, seconds = toi.partition(":")
    return int(minutes) * 60 + int(seconds or 0)


def _seconds_to_toi(total_seconds: int) -> str:
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def _aggregate(games: list[dict], is_goalie: bool) -> dict:
    if is_goalie:
        shots_against = sum(g.get("shotsAgainst") or 0 for g in games)
        goals_against = sum(g.get("goalsAgainst") or 0 for g in games)
        return {
            "games_played": len(games),
            "wins": sum(1 for g in games if g.get("decision") == "W"),
            "losses": sum(1 for g in games if g.get("decision") == "L"),
            "shutouts": sum(g.get("shutouts") or 0 for g in games),
            "shots_against": shots_against,
            "goals_against": goals_against,
            "save_pctg": round((shots_against - goals_against) / shots_against, 4) if shots_against else None,
            "goals_against_avg": round(goals_against / len(games), 2) if games else None,
            "toi": _seconds_to_toi(sum(_toi_to_seconds(g.get("toi", "")) for g in games)),
        }

    total_toi_s = sum(_toi_to_seconds(g.get("toi", "")) for g in games)
    return {
        "games_played": len(games),
        "goals": sum(g.get("goals") or 0 for g in games),
        "assists": sum(g.get("assists") or 0 for g in games),
        "points": sum(g.get("points") or 0 for g in games),
        "pim": sum(g.get("pim") or 0 for g in games),
        "plus_minus": sum(g.get("plusMinus") or 0 for g in games),
        "power_play_points": sum(g.get("powerPlayPoints") or 0 for g in games),
        "shots": sum(g.get("shots") or 0 for g in games),
        "avg_toi": _seconds_to_toi(total_toi_s // len(games)) if games else None,
    }


def get_stats_before_date(nhl_id: int, trade_date: str, position: str) -> dict:
    """
    Stats coupées à la date du trade, en deux blocs :
      - season  : uniquement la saison contenant trade_date, jusqu'au dernier
                  match strictement avant. C'est la ligne du prompt d'entraînement
                  (cf. README : "43 GP, 8G 4A, ..." — une saison, pas une carrière).
      - career  : cumul de toutes les saisons disponibles jusqu'à la même date.

    Ni hits ni FO% : ces stats n'existent pas au niveau du match dans l'API NHL,
    seulement en agrégat de saison complète — donc pas disponibles coupées à la
    date sans fuite. Omis plutôt qu'approximés.
    """
    trade_dt = date.fromisoformat(trade_date)
    is_goalie = (position or "").upper() == "G"
    current_season = _season_id_for_date(trade_dt)

    season_games = []
    career_games = []
    for season in get_available_seasons(nhl_id):
        season_start_year = season // 10000
        if date(season_start_year, 9, 1) > trade_dt:
            continue
        for g in get_game_log(nhl_id, season):
            if g.get("gameDate", "9999-99-99") < trade_date:
                career_games.append(g)
                if season == current_season:
                    season_games.append(g)

    return {
        "season": _aggregate(season_games, is_goalie),
        "career": _aggregate(career_games, is_goalie),
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
