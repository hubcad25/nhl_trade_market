#!/usr/bin/env python3
"""
E8 — Assemblage du dataset d'entraînement (issue wm9)

Un exemple (prompt, JSON) par élément joueur. Le prompt décrit CE joueur ; le champ
traded_with du prompt donne le profil complet des autres actifs envoyés dans le même
paquet (même receives_key, mêmes indices sauf le sien). Le JSON de sortie est le
retour obtenu en échange — les éléments de l'AUTRE receives_key de la même trade —
et n'est jamais généré par un modèle : profils complets pour les joueurs (mêmes
champs que traded_with, sans nom), picks depuis data/enriched/picks.jsonl (ki3).

Scope : les 727 éléments joueurs qui ont un profil E6 complet (data/enriched/
profiles_deidentified.jsonl). C'est le même sous-ensemble que stats.jsonl (E7) —
les 399 trades TSN de la passe de recherche E5, pas l'extension nhltradetracker
2005-2022 qui n'a pas de briefs.

Aucun nom de joueur ni d'équipe dans le prompt (cf. README « Key Design
Decisions ») — seulement profiles_deidentified.jsonl est lu, jamais profiles.jsonl.

Âge et taille viennent de data/enriched/bio.jsonl (E7b) — un appel NHL API par
nhl_id unique, âge recalculé à trade_date localement (jamais un âge "actuel").
Poids délibérément absent (varie dans le temps, cf. enrich_bio.py). Cap hit et
statut contractuel structuré restent absents faute de source déterministe — le
qualitatif research en parle en prose, mais rien n'est inventé pour combler.

Lit  data/enriched/profiles_deidentified.jsonl
     data/enriched/stats.jsonl
     data/enriched/bio.jsonl
     data/resolved/classified_elements.jsonl
     data/enriched/picks.jsonl
     data/normalized/trades.jsonl
Écrit data/training/dataset.jsonl

Usage:
  python pipelines/build_training_dataset.py
  python pipelines/build_training_dataset.py --limit 20
  python pipelines/build_training_dataset.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROFILES_PATH = Path("data/enriched/profiles_deidentified.jsonl")
STATS_PATH = Path("data/enriched/stats.jsonl")
BIO_PATH = Path("data/enriched/bio.jsonl")
CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
PICKS_PATH = Path("data/enriched/picks.jsonl")
TRADES_PATH = Path("data/normalized/trades.jsonl")
OUTPUT_PATH = Path("data/training/dataset.jsonl")

TYPE_LABELS = {
    "nhl_skater": "NHL skater",
    "nhl_goalie": "NHL goalie",
    "skater_prospect": "skater prospect",
    "goalie_prospect": "goalie prospect",
}

POSITION_LABELS = {
    "C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G",
}

# Approximation grossière — la date exacte de la limite des échanges varie chaque
# année (toujours début mars), donc "1-15 mars" est une bande, pas une date. Pas de
# calendrier officiel des dates de deadline dans les données du pipeline.
OFFSEASON_MONTHS = {6, 7, 8}


def season_phase(trade_date: str) -> str:
    month, day = int(trade_date[5:7]), int(trade_date[8:10])
    if month in OFFSEASON_MONTHS:
        return "offseason"
    if month == 3 and day <= 15:
        return "near trade deadline"
    return "in-season"


def key_of(rec: dict) -> tuple:
    return (rec["trade_id"], rec["receives_key"], rec["element_index"])


def load_jsonl_indexed(path: Path) -> dict[tuple, dict]:
    index = {}
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            index[key_of(rec)] = rec
    return index


def load_trades() -> dict[int, dict]:
    trades = {}
    with open(TRADES_PATH) as f:
        for line in f:
            t = json.loads(line)
            trades[t["trade_id"]] = t
    return trades


def format_stats_line(type_classified: str, stats: dict | None) -> str:
    if stats is None:
        return "no NHL stats available"

    season = stats["season"]
    is_goalie = type_classified in ("nhl_goalie", "goalie_prospect")
    gp = season["games_played"]

    if gp == 0:
        return "no NHL games played this season"

    if is_goalie:
        record = f"{season['wins']}-{season['losses']}"
        gaa = season["goals_against_avg"]
        sv = season["save_pctg"]
        sv_str = f"{sv * 100:.1f}%" if sv is not None else "n/a"
        return f"{gp} GP, {record}, {gaa} GAA, {sv_str} SV%"

    pts_per_gp = round(season["points"] / gp, 2)
    toi = season["avg_toi"] or "n/a"
    return f"{gp} GP, {season['goals']}G {season['assists']}A, {pts_per_gp} pts/GP, {toi} TOI"


def format_height(height_in: int | None) -> str | None:
    if not height_in:
        return None
    feet, inches = divmod(height_in, 12)
    return f"{feet}'{inches}\""


def format_player_asset(profile: dict, element: dict, stats_rec: dict | None, bio_rec: dict | None) -> dict:
    """Représentation complète d'un actif joueur — utilisée pour le sujet du prompt,
    pour traded_with, ET pour le retour cible (même schéma, jamais de nom)."""
    return {
        "type": TYPE_LABELS.get(profile["type_classified"], profile["type_classified"]),
        "position": POSITION_LABELS.get(element.get("position"), element.get("position")),
        "age": bio_rec["age_at_trade"] if bio_rec else None,
        "height": format_height(bio_rec["height_in"]) if bio_rec else None,
        "stats": format_stats_line(profile["type_classified"], stats_rec["stats"] if stats_rec else None),
        "context": profile["qualitative_summary"],
    }


def format_pick_asset(pick_rec: dict) -> dict:
    p = pick_rec["pick"]
    label = f"round {p['round']}" if p["round"] else "unspecified round"
    bits = [f"{p['draft_year']} {label} pick"]
    if p["is_conditional"]:
        bits.append("conditional")
    if p["estimated_pick_range"] and p["estimated_pick_range"] != "conditionnel":
        bits.append(f"estimated range {p['estimated_pick_range']}")
    return {"type": "pick", "round": p["round"], "draft_year": p["draft_year"],
            "conditional": p["is_conditional"], "estimated_pick_range": p["estimated_pick_range"],
            "description": ", ".join(bits)}


def side_elements(trade: dict, receives_key: str) -> list[dict]:
    return trade.get(receives_key, [])


def format_player_or_fallback(key: tuple, profiles: dict, stats: dict, classified: dict, bio: dict) -> dict:
    profile = profiles.get(key)
    if profile is None:
        # Hors scope E5/E6 (pas de brief) — on ne peut pas décrire cet actif sans
        # inventer, donc on le nomme sans détail plutôt que de l'omettre silencieusement.
        return {"type": "player", "context": "no research profile available"}
    element = classified.get(key, {}).get("element", {})
    return format_player_asset(profile, element, stats.get(key), bio.get(key))


def format_traded_with(
    trade: dict, receives_key: str, own_index: int,
    profiles: dict, stats: dict, classified: dict, bio: dict,
    trade_id: int, picks: dict,
) -> list[dict]:
    """Autres actifs envoyés dans le même paquet que le joueur sujet (mêmes
    trade_id/receives_key, indices différents)."""
    out = []
    for idx, el in enumerate(side_elements(trade, receives_key)):
        if idx == own_index:
            continue
        key = (trade_id, receives_key, idx)
        if el.get("type") == "player":
            out.append(format_player_or_fallback(key, profiles, stats, classified, bio))
        elif el.get("type") == "pick":
            pick_rec = picks.get(key)
            out.append(format_pick_asset(pick_rec) if pick_rec else {"type": "pick", "description": "pick, details unavailable"})
        elif el.get("type") == "future_consideration":
            out.append({"type": "future_consideration", "description": el.get("raw_text") or "future considerations"})
    return out


def build_return(
    trade: dict, other_receives_key: str,
    profiles: dict, stats: dict, classified: dict, bio: dict, picks: dict, trade_id: int,
) -> dict:
    """Cible déterministe : composition du paquet reçu en retour, même niveau de
    détail que traded_with (profil complet sans nom pour les joueurs) — la cible
    n'est pas qu'une catégorie, sinon le modèle apprend à peine plus qu'un
    classificateur."""
    players, pick_list, future = [], [], []
    for idx, el in enumerate(side_elements(trade, other_receives_key)):
        key = (trade_id, other_receives_key, idx)
        if el.get("type") == "player":
            players.append(format_player_or_fallback(key, profiles, stats, classified, bio))
        elif el.get("type") == "pick":
            pick_rec = picks.get(key)
            if pick_rec:
                p = pick_rec["pick"]
                pick_list.append({
                    "round": p["round"], "draft_year": p["draft_year"],
                    "conditional": p["is_conditional"],
                    "estimated_pick_range": p["estimated_pick_range"],
                })
            else:
                pick_list.append({"round": el.get("round"), "draft_year": el.get("year"), "conditional": el.get("is_conditional")})
        elif el.get("type") == "future_consideration":
            future.append(el.get("raw_text") or "future considerations")

    return {"players": players, "picks": pick_list, "future_considerations": future}


def format_player_inline(a: dict) -> str:
    bits = [a["type"], f"({a.get('position', '?')})"]
    if a.get("age") is not None:
        bits.append(f"age {a['age']}")
    if a.get("height"):
        bits.append(a["height"])
    return " ".join(bits) + f" — {a.get('context', '')}"


def build_prompt(trade_date: str, subject: dict, traded_with: list[dict], phase: str) -> str:
    lines = [
        f"Date: {trade_date}",
        f"Type: {subject['type']}",
        f"Position: {subject['position']}",
        f"Age: {subject['age'] if subject['age'] is not None else 'unknown'}",
        f"Height: {subject['height'] or 'unknown'}",
        f"Stats: {subject['stats']}",
        f"Context: {subject['context']}",
    ]
    if traded_with:
        parts = []
        for a in traded_with:
            if a["type"] == "pick":
                parts.append(a.get("description", "pick"))
            elif a["type"] == "future_consideration":
                parts.append(a["description"])
            else:
                parts.append(format_player_inline(a))
        lines.append("Traded with: " + "; ".join(parts))
    else:
        lines.append("Traded with: []")
    lines.append(f"Market context: {phase}")
    lines.append("")
    lines.append("What package does this player return in a trade?")
    return "\n".join(lines)


def build_example(
    profile: dict, classified: dict, stats: dict, bio: dict, picks: dict, trades: dict, profiles: dict,
) -> dict:
    trade_id = profile["trade_id"]
    receives_key = profile["receives_key"]
    element_index = profile["element_index"]
    trade_date = profile["trade_date"]
    trade = trades[trade_id]
    key = key_of(profile)

    element = classified.get(key, {}).get("element", {})
    subject = format_player_asset(profile, element, stats.get(key), bio.get(key))
    traded_with = format_traded_with(trade, receives_key, element_index, profiles, stats, classified, bio, trade_id, picks)
    phase = season_phase(trade_date)

    other_key = "team_two_receives" if receives_key == "team_one_receives" else "team_one_receives"
    target = build_return(trade, other_key, profiles, stats, classified, bio, picks, trade_id)

    return {
        "trade_id": trade_id,
        "receives_key": receives_key,
        "element_index": element_index,
        "prompt": build_prompt(trade_date, subject, traded_with, phase),
        "output": target,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    profiles = load_jsonl_indexed(PROFILES_PATH)
    stats = load_jsonl_indexed(STATS_PATH)
    bio = load_jsonl_indexed(BIO_PATH)
    classified = load_jsonl_indexed(CLASSIFIED_PATH)
    picks = load_jsonl_indexed(PICKS_PATH)
    trades = load_trades()

    profile_list = list(profiles.values())
    if args.limit:
        profile_list = profile_list[: args.limit]

    log.info("%d profils à assembler", len(profile_list))

    examples = []
    for profile in profile_list:
        try:
            examples.append(build_example(profile, classified, stats, bio, picks, trades, profiles))
        except Exception as e:
            log.error("Échec trade_id=%s receives_key=%s idx=%s: %s",
                       profile["trade_id"], profile["receives_key"], profile["element_index"], e)

    if args.dry_run:
        for ex in examples[:3]:
            print("=" * 60)
            print(ex["prompt"])
            print("-" * 20)
            print(json.dumps(ex["output"], indent=2, ensure_ascii=False))
        print(f"\n--- {len(examples)} exemples seraient écrits (aperçu limité aux 3 premiers) ---")
        return

    examples.sort(key=lambda e: (e["trade_id"], e["receives_key"], e["element_index"]))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    log.info("Terminé : %d exemples écrits -> %s", len(examples), OUTPUT_PATH)


if __name__ == "__main__":
    main()
