#!/usr/bin/env python3
"""
E: Estimation des tiers de picks (issue ki3)

Pour chaque élément 'pick' de data/resolved/classified_elements.jsonl, produit une
estimation du rang de sélection à la date du trade, sans jamais utiliser le
résultat réel du repêchage (qui n'existe pas encore à trade_date pour les picks
imminents, et fuirait le futur pour les autres).

Formule (picks de 1re ET 2e ronde, draft imminent = même année que le trade ou
l'année suivante, non conditionnels) :
  - classement NHL (pipelines.sources.nhl_api.get_standings) à trade_date
  - rang de repêchage de l'équipe propriétaire du pick = ligue à l'envers
    (pire équipe = rang 1 = 1er choix), avec repli sur le dernier classement
    disponible si trade_date tombe en entre-saison (droite règle avril-septembre)
  - ronde 2 = decalée de num_teams (ex: équipe classée pire de la ligue -> pick
    #1 en ronde 1, #(num_teams+1) en ronde 2)
  - fenêtre d'estimation ±2 autour du rang; phrasé 'top N' si le rang est parmi
    les 10 premiers (usage courant en repêchage), sinon 'lo-hi'

Picks conditionnels ou hors scope (ronde 3+, 2+ ans dans le futur, ou le passé) :
  pas de formule, juste l'info brute TSN (estimated_pick_range = null, ou
  'conditionnel' si applicable et par ailleurs éligible).

Gère les relocations de franchise dans la fenêtre 2005-présent (Atlanta/Winnipeg,
Phoenix/Arizona/Utah) : le classement NHL renvoie l'abréviation de l'époque
(ex: PHX avant 2014), alors que nos données normalisent au nom de franchise
actuel (ARI) — on essaie les alias connus avant d'abandonner.

Override par texte libre (issue trouvée en relisant wm9) : le schéma TSN structuré
des picks (round/year/isConditional/title) ne porte JAMAIS l'équipe d'origine — pas
un oubli de normalize_trades.py, c'est absent de la source. `original_owner` était
jusqu'ici juste giving_team (l'équipe qui envoie CE pick DANS CE trade), ce qui est
faux dès qu'un pick a déjà changé de mains une fois (ex: trade 1548 — le 2022 1er
tour envoyé par MTL à CHI pour Kirby Dach était en fait le pick des Islanders,
acquis par MTL le même jour dans le trade 1544, ré-échangé quelques heures plus
tard). Le champ libre `informations` de TSN donne parfois le numéro overall exact
et/ou l'équipe d'origine explicitement — extrait ici quand présent :
  - "TEAM receive(s)/acquire(s) No. X [and Y...] overall" → numéro exact du pick,
    remplace l'estimation par classement (pick_number_source=informations_text)
  - "originally belonging to TEAM" → équipe d'origine confirmée
    (original_owner_source=informations_text)
Corrige seulement les cas où le texte le dit explicitement (~10 trades sur 2157).
Le reste garde l'ancienne hypothèse original_owner=giving_team, non vérifiée —
tracée honnêtement via original_owner_source=giving_team_assumption. Un vrai
correctif général demanderait de rejouer tout l'historique des trades pour tracer
la lignée de chaque pick (issue à part, pas fait ici).

Lit  data/resolved/classified_elements.jsonl (éléments type_classified='pick')
     data/normalized/trades.jsonl (champ informations, texte libre)
Écrit data/raw/picks/{trade_id}-{one|two}-{index}.json (cache, un fichier par élément)
      data/enriched/picks.jsonl (assemblage final, réécrit à chaque run)

Usage:
  python pipelines/enrich_pick.py
  python pipelines/enrich_pick.py --limit 20
  python pipelines/enrich_pick.py --force
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.sources.nhl_api import get_standings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
TRADES_PATH = Path("data/normalized/trades.jsonl")
PICKS_CACHE_DIR = Path("data/raw/picks")
PICKS_PATH = Path("data/enriched/picks.jsonl")

NUMBER_RE = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)?\b(?!\s*(?:per\s*cent|percent|%))")
ORIGINALLY_BELONGING_RE = re.compile(
    r"originally\s+belonging\s+to\s+(?:the\s+)?([A-Za-z .'-]+?)(?:[.,]|$)", re.IGNORECASE
)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def team_aliases(team: dict) -> list[str]:
    """Nom complet sans accents + short + chaque mot significatif (>=4 lettres) —
    les blurbs TSN nomment parfois juste le surnom ('Blackhawks') ou la ville
    ('Montreal') plutôt que le nom complet de l'équipe."""
    name = strip_accents(team["name"])
    words = [w for w in name.split() if len(w) >= 4]
    return sorted(set([name, team["short"]] + words), key=len, reverse=True)


def find_team_mentions(text_flat: str, team_one: dict, team_two: dict) -> list[tuple[int, str]]:
    mentions = []
    for label, team in (("team_one", team_one), ("team_two", team_two)):
        for alias in team_aliases(team):
            for m in re.finditer(re.escape(alias), text_flat):
                mentions.append((m.start(), label))
    mentions.sort()
    dedup: list[tuple[int, str]] = []
    for pos, label in mentions:
        if any(abs(pos - p2) < 3 and l2 == label for p2, l2 in dedup):
            continue
        dedup.append((pos, label))
    dedup.sort()
    return dedup


def extract_numbers_by_side(informations: str | None, team_one: dict, team_two: dict) -> dict[str, list[int]]:
    """{'team_one': [numéros overall reçus par team_one], 'team_two': [...]} en
    scindant le texte sur les mentions d'équipe. Vide si le texte ne mentionne pas
    explicitement de numéro overall."""
    empty = {"team_one": [], "team_two": []}
    if not informations or not re.search(r"overall|No\.\s*\d|#\s*\d", informations):
        return empty

    text_flat = strip_accents(informations)
    mentions = find_team_mentions(text_flat, team_one, team_two)

    if not mentions:
        return empty

    result = {"team_one": [], "team_two": []}
    for i, (pos, label) in enumerate(mentions):
        end = mentions[i + 1][0] if i + 1 < len(mentions) else len(text_flat)
        chunk = text_flat[pos:end]
        result[label].extend(int(n) for n in NUMBER_RE.findall(chunk))
    return result


def build_pick_number_overrides(trades: dict[int, dict]) -> dict[tuple, int]:
    """{(trade_id, receives_key, element_index): numéro overall exact} — seulement
    quand le compte de numéros extraits égale le compte d'éléments 'pick' de ce
    côté (sinon ambiguïté, on n'assigne rien plutôt que de deviner l'ordre).

    Repli : si le texte ne mentionne aucune des deux équipes du trade (ex: "Draft
    pick is 37th overall, originally belonging to X" — X est un TROISIÈME club,
    ni team_one ni team_two) mais que le trade n'a qu'un seul élément 'pick' au
    total et un seul numéro dans le texte, l'affectation reste sans ambiguïté."""
    overrides: dict[tuple, int] = {}
    for trade_id, trade in trades.items():
        info = trade.get("informations")
        by_side = extract_numbers_by_side(info, trade["team_one"], trade["team_two"])
        assigned_this_trade = False
        for label, receives_key in (("team_one", "team_one_receives"), ("team_two", "team_two_receives")):
            nums = by_side[label]
            if not nums:
                continue
            pick_indices = [i for i, el in enumerate(trade.get(receives_key, [])) if el.get("type") == "pick"]
            if len(pick_indices) != len(nums):
                continue
            for idx, num in zip(pick_indices, nums):
                overrides[(trade_id, receives_key, idx)] = num
            assigned_this_trade = True

        if assigned_this_trade or not info or not re.search(r"overall|No\.\s*\d|#\s*\d", info):
            continue

        all_pick_keys = [
            (receives_key, i)
            for receives_key in ("team_one_receives", "team_two_receives")
            for i, el in enumerate(trade.get(receives_key, []))
            if el.get("type") == "pick"
        ]
        bare_numbers = NUMBER_RE.findall(strip_accents(info))
        if len(all_pick_keys) == 1 and len(bare_numbers) == 1:
            receives_key, idx = all_pick_keys[0]
            overrides[(trade_id, receives_key, idx)] = int(bare_numbers[0])
    return overrides


def build_team_registry(trades: dict[int, dict]) -> list[dict]:
    seen = {}
    for trade in trades.values():
        for team in (trade["team_one"], trade["team_two"]):
            seen[team["short"]] = team
    return list(seen.values())


def build_original_owner_overrides(trades: dict[int, dict], registry: list[dict]) -> dict[tuple, tuple[str, str]]:
    """{(trade_id, receives_key, element_index): (short d'équipe, 'informations_text')}
    depuis un blurb 'originally belonging to TEAM' — s'applique seulement quand le
    côté receveur n'a qu'un seul élément 'pick' (pas d'ambiguïté sur lequel)."""
    overrides: dict[tuple, tuple[str, str]] = {}
    for trade_id, trade in trades.items():
        info = trade.get("informations")
        if not info:
            continue
        m = ORIGINALLY_BELONGING_RE.search(strip_accents(info))
        if not m:
            continue
        mentioned = m.group(1).strip()
        match = None
        for team in registry:
            if any(alias == mentioned or alias in mentioned or mentioned in alias for alias in team_aliases(team)):
                match = team["short"]
                break
        if not match:
            continue
        for receives_key in ("team_one_receives", "team_two_receives"):
            pick_indices = [i for i, el in enumerate(trade.get(receives_key, [])) if el.get("type") == "pick"]
            if len(pick_indices) == 1:
                overrides[(trade_id, receives_key, pick_indices[0])] = (match, "informations_text")
    return overrides


MANUAL_OWNER_OVERRIDES_PATH = Path("data/manual/pick_owner_overrides.json")


def load_manual_owner_overrides() -> dict[tuple, tuple[str, str]]:
    """{(trade_id, receives_key, element_index): (short, 'manual_research')} — cas
    trouvés par recherche web manuelle (issue 4v9), pas par le texte TSN. Fichier
    data/manual/pick_owner_overrides.json, clé "trade_id-receives_key-element_index"."""
    if not MANUAL_OWNER_OVERRIDES_PATH.exists():
        return {}
    with open(MANUAL_OWNER_OVERRIDES_PATH) as f:
        raw = json.load(f)
    overrides = {}
    for key_str, entry in raw.items():
        trade_id_str, receives_key, idx_str = key_str.rsplit("-", 2)
        overrides[(int(trade_id_str), receives_key, int(idx_str))] = (entry["original_owner"], "manual_research")
    return overrides

# Relocations de franchise dans la fenêtre couverte (2005-présent). Le classement
# NHL renvoie l'abréviation en usage à trade_date ; nos données normalisent au
# nom de franchise actuel.
FRANCHISE_ALIASES = {
    "ARI": ["ARI", "PHX", "UTA"],
    "PHX": ["PHX", "ARI", "UTA"],
    "UTA": ["UTA", "ARI", "PHX"],
    "ATL": ["ATL", "WPG"],
    "WPG": ["WPG", "ATL"],
}

# Entre-saison approximative (fin saison régulière -> début suivante) : le
# classement à une date dans cette fenêtre est vide, on retombe sur la fin de
# la saison qui vient de se terminer.
OFFSEASON_START = (4, 20)
OFFSEASON_END = (9, 15)

EARLY_PICK_THRESHOLD = 10
RANGE_MARGIN = 2


def load_picks(limit: int | None) -> list[dict]:
    if not CLASSIFIED_PATH.exists():
        raise SystemExit(f"{CLASSIFIED_PATH} introuvable")

    picks = []
    with open(CLASSIFIED_PATH) as f:
        for line in f:
            rec = json.loads(line)
            if rec["element"]["type_classified"] == "pick":
                picks.append(rec)

    if limit:
        picks = picks[:limit]
    return picks


def cache_path(trade_id: int, receives_key: str, element_index: int) -> Path:
    side = "one" if receives_key == "team_one_receives" else "two"
    return PICKS_CACHE_DIR / f"{trade_id}-{side}-{element_index}.json"


def _candidate_dates(trade_date: str) -> list[date]:
    d = date.fromisoformat(trade_date)
    candidates = [d]
    if OFFSEASON_START <= (d.month, d.day) < OFFSEASON_END:
        candidates.append(date(d.year, 4, 19))
    for offset in (15, 30, 60, 90, 120, 150, 180):
        candidates.append(d - timedelta(days=offset))
    return candidates


def get_standings_near(trade_date: str, cache: dict[str, list[dict]]) -> list[dict]:
    """Classement à trade_date, avec repli sur les dates antérieures si vide
    (entre-saison). Mémoïsé — beaucoup d'éléments partagent la même trade_date."""
    if trade_date in cache:
        return cache[trade_date]

    standings: list[dict] = []
    for cand in _candidate_dates(trade_date):
        key = cand.isoformat()
        if key in cache:
            standings = cache[key]
        else:
            standings = get_standings(key)
            cache[key] = standings
        if standings:
            break

    cache[trade_date] = standings
    return standings


def resolve_team_row(standings: list[dict], short: str) -> dict | None:
    by_abbrev = {row["team_abbrev"]: row for row in standings}
    for alias in FRANCHISE_ALIASES.get(short, [short]):
        if alias in by_abbrev:
            return by_abbrev[alias]
    return None


def estimate_pick_range(draft_rank: int, round_num: int, num_teams: int) -> str:
    """rang ± quelques positions, ex: rang 5 en ronde 1 -> 'top 7'."""
    if round_num == 1:
        base = draft_rank
        round_start, round_end = 1, num_teams
    else:
        base = num_teams + draft_rank
        round_start, round_end = num_teams + 1, num_teams * 2

    lo = max(round_start, base - RANGE_MARGIN)
    hi = min(round_end, base + RANGE_MARGIN)

    if base <= EARLY_PICK_THRESHOLD:
        return f"top {hi}"
    return f"{lo}-{hi}"


def enrich(
    rec: dict,
    standings_cache: dict[str, list[dict]],
    number_overrides: dict[tuple, int],
    owner_overrides: dict[tuple, str],
) -> dict:
    raw = rec["element"]["raw_tsn_element"]
    round_num = raw["round"]
    draft_year = raw["year"]
    is_conditional = raw["is_conditional"]
    trade_date = rec["trade_date"]
    trade_year = int(trade_date[:4])
    key = (rec["trade_id"], rec["receives_key"], rec["element_index"])

    owner_hint = owner_overrides.get(key)
    if owner_hint:
        original_owner, original_owner_source = owner_hint
    else:
        original_owner = rec["giving_team"]["short"]
        original_owner_source = "giving_team_assumption"

    number_hint = number_overrides.get(key)
    pick_number_source: str | None = None
    estimated_pick_range: str | None = None

    if number_hint is not None and not is_conditional:
        estimated_pick_range = str(number_hint)
        pick_number_source = "informations_text"
    else:
        delta = draft_year - trade_year if draft_year is not None else None
        formula_scope = round_num in (1, 2) and delta in (0, 1)
        if formula_scope:
            if is_conditional:
                estimated_pick_range = "conditionnel"
            else:
                standings = get_standings_near(trade_date, standings_cache)
                row = resolve_team_row(standings, original_owner) if standings else None
                if row is None:
                    log.warning(
                        "Classement introuvable pour %s à %s (trade_id=%s) — estimated_pick_range=null",
                        original_owner, trade_date, rec["trade_id"],
                    )
                else:
                    num_teams = len(standings)
                    draft_rank = num_teams + 1 - row["league_rank"]
                    estimated_pick_range = estimate_pick_range(draft_rank, round_num, num_teams)
                pick_number_source = "standings_formula"

    return {
        "trade_id": rec["trade_id"],
        "trade_date": trade_date,
        "receives_key": rec["receives_key"],
        "element_index": rec["element_index"],
        "pick": {
            "type": "pick",
            "round": round_num,
            "draft_year": draft_year,
            "original_owner": original_owner,
            "original_owner_source": original_owner_source,
            "is_conditional": is_conditional,
            "estimated_pick_range": estimated_pick_range,
            "pick_number_source": pick_number_source,
        },
    }


def load_trades() -> dict[int, dict]:
    trades = {}
    with open(TRADES_PATH) as f:
        for line in f:
            t = json.loads(line)
            trades[t["trade_id"]] = t
    return trades


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    picks = load_picks(args.limit)
    log.info("%d éléments 'pick' à traiter", len(picks))

    trades = load_trades()
    number_overrides = build_pick_number_overrides(trades)
    owner_overrides = build_original_owner_overrides(trades, build_team_registry(trades))
    manual_overrides = load_manual_owner_overrides()
    owner_overrides.update(manual_overrides)
    log.info(
        "%d numéros de pick confirmés (texte TSN) ; %d équipes d'origine confirmées "
        "(%d texte TSN + %d recherche manuelle, issue 4v9)",
        len(number_overrides), len(owner_overrides),
        len(owner_overrides) - len(manual_overrides), len(manual_overrides),
    )

    PICKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    standings_cache: dict[str, list[dict]] = {}
    results = []

    for i, rec in enumerate(picks, start=1):
        path = cache_path(rec["trade_id"], rec["receives_key"], rec["element_index"])
        if path.exists() and not args.force:
            with open(path) as f:
                results.append(json.load(f))
            continue

        payload = enrich(rec, standings_cache, number_overrides, owner_overrides)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        results.append(payload)

        if i % 100 == 0:
            log.info("Progress: %d/%d", i, len(picks))

    PICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    results.sort(key=lambda r: (r["trade_id"], r["receives_key"], r["element_index"]))
    with open(PICKS_PATH, "w") as out:
        for r in results:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    log.info("Terminé : %d picks écrits -> %s", len(results), PICKS_PATH)


if __name__ == "__main__":
    main()
