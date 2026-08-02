"""
E0.5 — Classification des éléments de trade

Lit data/normalized/trades.jsonl + data/resolved/player_id_map.json.
Pour chaque élément de type 'player', détermine le type classificé:
  - pick / future_consideration → trivial
  - nhl_id résolu → game logs NHL API avant trade_date → GP >= 25 → nhl_skater/nhl_goalie
                                                         → GP <  25 → skater_prospect/goalie_prospect
  - is_nhl=False dans player_id_map → prospect (skater_prospect par défaut)
  - non résolu → unresolved

Output: data/resolved/classified_elements.jsonl
"""

import sys
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.nhl_api import nhl_get, get_available_seasons  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

NHL_PROSPECT_GP_THRESHOLD = 25

TRADES_PATH = Path("data/normalized/trades.jsonl")
PLAYER_MAP_PATH = Path("data/resolved/player_id_map.json")
OUTPUT_PATH = Path("data/resolved/classified_elements.jsonl")


def get_gp_and_position_before_date(nhl_id: int, trade_date: str) -> tuple[int, str]:
    """
    Returns (total_nhl_gp_before_trade, position_code).
    position_code: 'G' for goalie, anything else for skater.
    """
    trade_dt = date.fromisoformat(trade_date)

    # Get position from landing (fast, single call)
    landing = nhl_get(f"https://api-web.nhle.com/v1/player/{nhl_id}/landing")
    position = landing.get("position", "").upper()

    seasons = get_available_seasons(nhl_id)

    total_gp = 0
    for season in seasons:
        # season id like 20222023 — season ends in the second year
        season_end_year = season % 10000
        season_start_year = season // 10000

        # Skip seasons that start after the trade date entirely
        if date(season_start_year, 9, 1) > trade_dt:
            continue

        data = nhl_get(f"https://api-web.nhle.com/v1/player/{nhl_id}/game-log/{season}/2")
        game_log = data.get("gameLog", [])

        gp_this_season = sum(
            1 for g in game_log if g.get("gameDate", "9999-99-99") < trade_date
        )
        total_gp += gp_this_season

        # If the season end year is before the trade year, all games are before trade
        # and we already counted them correctly above — no early exit needed.

    return total_gp, position


def classify_element(el: dict, trade_date: str, player_map: dict) -> tuple[str, str | None]:
    el_type = el.get("type", "")

    if el_type == "pick":
        return "pick", None

    if el_type == "future_consideration":
        return "future_consideration", None

    # Resolve nhl_id: from element directly or via player_map
    nhl_id = el.get("nhl_id")
    name = el.get("name")

    map_entry = player_map.get(name, {}) if name else {}

    if not nhl_id and map_entry:
        nhl_id = map_entry.get("nhl_id")

    # is_nhl=False → prospect (no NHL career)
    if not nhl_id:
        if map_entry.get("is_nhl") is False:
            return "skater_prospect", None
        return "unresolved", None

    # Has nhl_id — check GP via game logs
    try:
        gp, position = get_gp_and_position_before_date(nhl_id, trade_date)
    except Exception as e:
        log.error("Failed to get game log for nhl_id=%s name=%s: %s", nhl_id, name, e)
        return "unresolved", None

    is_goalie = position == "G"

    if gp >= NHL_PROSPECT_GP_THRESHOLD:
        return ("nhl_goalie" if is_goalie else "nhl_skater"), position
    else:
        return ("goalie_prospect" if is_goalie else "skater_prospect"), position


def load_done_keys(path: Path) -> set[tuple]:
    """Load (trade_id, receives_key, element_index) already written to output."""
    done = set()
    if not path.exists():
        return done
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
                done.add((r["trade_id"], r["receives_key"], r["element_index"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main():
    with open(PLAYER_MAP_PATH) as f:
        player_map = json.load(f)

    trades = []
    with open(TRADES_PATH) as f:
        for line in f:
            trades.append(json.loads(line))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    done_keys = load_done_keys(OUTPUT_PATH)
    if done_keys:
        log.info("Resuming — %d elements already classified", len(done_keys))

    # Build work list
    work = []
    for trade in trades:
        trade_date = trade["trade_date"]
        trade_id = trade["trade_id"]
        for receives_key in ["team_one_receives", "team_two_receives"]:
            receiving_team = trade.get("team_one" if receives_key == "team_one_receives" else "team_two", {})
            giving_team = trade.get("team_two" if receives_key == "team_one_receives" else "team_one", {})
            for el_idx, el in enumerate(trade.get(receives_key, [])):
                key = (trade_id, receives_key, el_idx)
                if key in done_keys:
                    continue
                work.append((trade_id, trade_date, receives_key, el_idx, el, receiving_team, giving_team))

    total = sum(
        len(t.get("team_one_receives", [])) + len(t.get("team_two_receives", []))
        for t in trades
    )
    done = len(done_keys)
    write_lock = threading.Lock()

    def process(item):
        trade_id, trade_date, receives_key, el_idx, el, receiving_team, giving_team = item
        classified_type, position = classify_element(el, trade_date, player_map)
        return {
            "trade_id": trade_id,
            "trade_date": trade_date,
            "receives_key": receives_key,
            "element_index": el_idx,
            "receiving_team": receiving_team,
            "giving_team": giving_team,
            "element": {
                "type_classified": classified_type,
                "nhl_id": el.get("nhl_id") or (player_map.get(el.get("name", ""), {}).get("nhl_id")),
                "tsn_name": el.get("name"),
                "capwages_slug": player_map.get(el.get("name", ""), {}).get("capwages_slug"),
                "position": position,
                "raw_tsn_element": el,
            },
        }

    with open(OUTPUT_PATH, "a") as out:
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {executor.submit(process, item): item for item in work}
            for future in as_completed(futures):
                try:
                    record = future.result()
                except Exception as e:
                    item = futures[future]
                    log.error("Failed item trade_id=%s el=%s: %s", item[0], item[4].get("name"), e)
                    continue

                with write_lock:
                    done += 1
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
                    if done % 50 == 0:
                        log.info("Progress: %d/%d elements classified", done, total)

    log.info("Done. %d elements written to %s", done, OUTPUT_PATH)


if __name__ == "__main__":
    main()
