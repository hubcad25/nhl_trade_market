#!/usr/bin/env python3
"""
E6 — Extraction du profil qualitatif

Un appel plain (pas d'agent, pas de web_search) par élément, à partir du brief brut
de l'étape 5. Le brief contient le nom du joueur, ses équipes, ses sources — tout ce
que le training set ne doit jamais voir. Cette étape en tire une prose courte,
anonyme, qui ne garde que ce qui décrit le joueur lui-même.

Lit  data/raw/briefs/{source_model}/{source_version}/*.json (briefs status=completed)
     data/normalized/trades.jsonl (pour les noms du côté retour, cf. validate())
Écrit data/raw/extractions/{model}/{version}/{trade_id}-{one|two}-{index}.json (cache)
      data/enriched/profiles.jsonl (assemblage final, réécrit à chaque run)

Usage:
  python pipelines/extract_profile.py --pilot
  python pipelines/extract_profile.py --limit 20
  python pipelines/extract_profile.py                  # passe complète
  python pipelines/extract_profile.py --retry-failed
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

BRIEFS_DIR = Path("data/raw/briefs")
EXTRACTIONS_DIR = Path("data/raw/extractions")
TRADES_PATH = Path("data/normalized/trades.jsonl")
PROFILES_PATH = Path("data/enriched/profiles.jsonl")

# Modèle/version source des briefs E5 à lire — surchargeable via --source-model
# pour absorber des briefs produits hors du script research_player.py (ex. recherche
# faite par Claude Code sur un sous-ensemble ciblé, issue b7e), tant qu'ils respectent
# le même schéma de fichier sous data/raw/briefs/{model}/{version}/.
SOURCE_MODEL = "gpt-5.4-mini"
SOURCE_PROMPT_VERSION = "v6"

# À incrémenter à chaque changement du prompt d'extraction — même logique que
# PROMPT_VERSION dans research_player.py.
# v1 : prose narrative française, 3-6 phrases — rejetée par Hubert, trop
#      journalistique/éditoriale, en décalage stylistique avec le reste du prompt
#      d'entraînement (champs télégraphiques en anglais, cf. README).
# v2 : phrases denses en anglais, sans sujet grammatical, calquées sur l'exemple du
#      README ("elite PK, energy and forecheck, locker room leadership...").
EXTRACT_VERSION = "v2"

MODEL = "gpt-5.4-mini"

PILOT_NAMES = [
    "Kyle Criscuolo",
    "Nils Juntorp",
    "Graham Sward",
    "Shea Weber",
    "Kevin Fiala",
]

SYSTEM_PROMPT = """You compress a raw scouting research report into a dense qualitative tag line for a training dataset — the "Context:" field of a trade-prediction model's input prompt.

Rules, in order:

1. Never name the player, his team, or his organization. Write as descriptive
   phrases with no subject noun — no "he" as a sentence subject either. Think
   tags, not sentences.

2. Never mention the trade return: what player, pick, or consideration went the
   other way, which team acquired him, or any evaluation of the trade (who won
   it, its perceived value). Those are the model's prediction targets —
   including them would corrupt the dataset.

3. Cover only what the report documents about the player himself: role and
   playing style, level of play and recent production, recognized strengths,
   scouting reservations, contract situation, health, organizational climate
   (conflict, captaincy, trade request). Skip any theme the report marks
   undocumented or doesn't cover — never invent or pad.

4. Output format: short comma/semicolon-separated descriptive phrases, grouped
   loosely by theme, the way a scout's cheat sheet reads — not full sentences,
   no narrative connectors ("he arrived as...", "the profile shows..."), no
   hedging phrases ("the report indicates that..."). State facts as bare noun
   phrases: "elite penalty killer" not "he is regarded as an elite penalty
   killer". Model this after: "defensive center known for elite PK, energy and
   forecheck, locker room leadership, reliable depth forward."

5. If the report has little to no substantive coverage, say so in one short
   phrase ("minimal scouting coverage available") rather than padding.

6. One dense paragraph, 200-400 characters. No section headers, no bullets, no
   preamble, no closing remarks."""

USER_TEMPLATE = """Type: {type_label}

Raw research report:
---
{brief}
---

Write the compressed tag line."""

TYPE_LABELS = {
    "nhl_skater": "established NHL skater",
    "nhl_goalie": "established NHL goalie",
    "skater_prospect": "skater prospect",
    "goalie_prospect": "goalie prospect",
}


def load_trades() -> dict[int, dict]:
    trades = {}
    with open(TRADES_PATH) as f:
        for line in f:
            t = json.loads(line)
            trades[t["trade_id"]] = t
    return trades


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if len(name) >= 2 and name[1] == " " and name[0].isalpha():
        name = name[2:]
    return name


def return_side_names(trade: dict, receives_key: str) -> list[str]:
    """Noms des joueurs de l'AUTRE côté du trade — ce contre quoi cet élément a été échangé."""
    other = "team_two_receives" if receives_key == "team_one_receives" else "team_one_receives"
    names = []
    for el in trade.get(other, []):
        if el.get("type") == "player" and el.get("name"):
            names.append(_clean_name(el["name"]))
    return names


def validate(summary: str, player_name: str, trade: dict, receives_key: str) -> list[str]:
    """Retourne une liste d'avertissements de fuite ; vide veut dire propre."""
    if not summary.strip():
        return []

    warnings = []
    lowered = summary.lower()

    own_surname = player_name.split()[-1]
    if len(own_surname) > 3 and re.search(rf"\b{re.escape(own_surname)}\b", lowered, re.IGNORECASE):
        warnings.append(f"nomme le joueur lui-même : {player_name}")

    for name in return_side_names(trade, receives_key):
        surname = name.split()[-1]
        if len(surname) > 3 and re.search(rf"\b{re.escape(surname)}\b", lowered, re.IGNORECASE):
            warnings.append(f"nomme un joueur du retour : {name}")

    for team in (trade.get("team_one", {}), trade.get("team_two", {})):
        team_name = team.get("name") or ""
        if team_name and team_name.lower() in lowered:
            warnings.append(f"nomme une équipe : {team_name}")

    return warnings


def brief_path(rec: dict) -> Path:
    side = "one" if rec["receives_key"] == "team_one_receives" else "two"
    return (
        BRIEFS_DIR / SOURCE_MODEL / SOURCE_PROMPT_VERSION
        / f"{rec['trade_id']}-{side}-{rec['element_index']}.json"
    )


def cache_path(rec: dict, model: str) -> Path:
    side = "one" if rec["receives_key"] == "team_one_receives" else "two"
    return (
        EXTRACTIONS_DIR / model / EXTRACT_VERSION
        / f"{rec['trade_id']}-{side}-{rec['element_index']}.json"
    )


def load_elements_with_briefs() -> list[dict]:
    """Éléments ayant un brief E5 complet et exploitable, sous forme d'un rec léger
    directement dérivé du brief (le brief porte déjà tout ce dont E6 a besoin)."""
    records = []
    briefs_dir = BRIEFS_DIR / SOURCE_MODEL / SOURCE_PROMPT_VERSION
    if not briefs_dir.exists():
        raise SystemExit(f"{briefs_dir} introuvable — l'étape 5 n'a pas produit de briefs pour ce modèle/version")

    for path in sorted(briefs_dir.glob("*.json")):
        with open(path) as f:
            brief_payload = json.load(f)
        if brief_payload.get("status") != "completed" or not (brief_payload.get("brief") or "").strip():
            continue
        records.append(brief_payload)
    return records


def call_extraction(brief_payload: dict, model: str, session: requests.Session, retries: int = 5) -> str:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/openai/v1/responses"
    params = {"api-version": os.environ.get("AZURE_OPENAI_API_VERSION", "preview")}
    headers = {
        "api-key": os.environ["AZURE_OPENAI_API_KEY"],
        "Content-Type": "application/json",
    }
    user_content = USER_TEMPLATE.format(
        type_label=TYPE_LABELS.get(brief_payload["type_classified"], brief_payload["type_classified"]),
        brief=brief_payload["brief"],
    )
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }

    delay = 5.0
    for attempt in range(retries):
        try:
            r = session.post(url, params=params, headers=headers, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504):
                wait = delay * (2 ** attempt)
                log.warning("HTTP %s — nouvelle tentative dans %.0fs", r.status_code, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            response = r.json()
            break
        except requests.RequestException as e:
            wait = delay * (2 ** attempt)
            log.warning("Erreur %s — nouvelle tentative dans %.0fs", e, wait)
            time.sleep(wait)
    else:
        raise RuntimeError(f"Échec après {retries} tentatives")

    text_parts = []
    for block in response.get("output", []):
        if block.get("type") != "message":
            continue
        for part in block.get("content", []):
            if part.get("type") == "output_text":
                text_parts.append(part.get("text", ""))
    return "\n".join(text_parts).strip()


def is_broken(payload: dict) -> bool:
    return not (payload.get("qualitative_summary") or "").strip()


def extract(brief_payload: dict, model: str, trades: dict, session: requests.Session, force: bool) -> tuple[dict, bool]:
    rec_key = {
        "trade_id": brief_payload["trade_id"],
        "receives_key": brief_payload["receives_key"],
        "element_index": brief_payload["element_index"],
    }
    path = cache_path(rec_key, model)
    if path.exists() and not force:
        with open(path) as f:
            return json.load(f), True

    summary = call_extraction(brief_payload, model, session)
    trade = trades[brief_payload["trade_id"]]
    warnings = validate(summary, brief_payload["player_name"], trade, brief_payload["receives_key"])

    payload = {
        "trade_id": brief_payload["trade_id"],
        "trade_date": brief_payload["trade_date"],
        "receives_key": brief_payload["receives_key"],
        "element_index": brief_payload["element_index"],
        "type_classified": brief_payload["type_classified"],
        "player_name": brief_payload["player_name"],
        "model": model,
        "extract_version": EXTRACT_VERSION,
        "source_model": brief_payload["model"],
        "source_prompt_version": brief_payload["prompt_version"],
        "qualitative_summary": summary,
        "leakage_warnings": warnings,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

    return payload, False


def revalidate(model: str, trades: dict) -> int:
    """Réapplique validate() au cache existant sans rappeler l'API — utile après une
    révision du validateur (cf. les faux positifs de vocabulaire d'échange/pick)."""
    cache_dir = EXTRACTIONS_DIR / model / EXTRACT_VERSION
    changed = 0
    for path in sorted(cache_dir.glob("*.json")):
        with open(path) as f:
            payload = json.load(f)
        player_name = payload.get("player_name")
        if not player_name:
            with open(brief_path(payload)) as bf:
                player_name = json.load(bf)["player_name"]
        trade = trades[payload["trade_id"]]
        new_warnings = validate(payload["qualitative_summary"], player_name, trade, payload["receives_key"])
        if new_warnings != payload.get("leakage_warnings", []) or player_name != payload.get("player_name"):
            payload["player_name"] = player_name
            payload["leakage_warnings"] = new_warnings
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
            changed += 1
    return changed


def build_profiles_jsonl(model: str) -> int:
    """Réassemble data/enriched/profiles.jsonl depuis le cache. Exclut ce qui a des
    avertissements de fuite — à traiter manuellement plutôt qu'à inclure taisement."""
    cache_dir = EXTRACTIONS_DIR / model / EXTRACT_VERSION
    entries = []
    flagged = 0
    for path in sorted(cache_dir.glob("*.json")):
        with open(path) as f:
            payload = json.load(f)
        if payload.get("leakage_warnings"):
            flagged += 1
            continue
        if is_broken(payload):
            continue
        entries.append(payload)

    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_PATH, "w") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    if flagged:
        log.warning("%d éléments exclus de profiles.jsonl pour avertissement de fuite — voir le cache", flagged)
    return len(entries)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument(
        "--source-model", default=SOURCE_MODEL,
        help="dossier data/raw/briefs/{source-model}/{source-version}/ à lire (défaut: gpt-5.4-mini)",
    )
    ap.add_argument(
        "--source-version", default=SOURCE_PROMPT_VERSION,
        help="version de prompt E5 des briefs à lire (défaut: v6)",
    )
    ap.add_argument(
        "--revalidate", action="store_true",
        help="ne rappelle pas l'API — réapplique validate() au cache et reconstruit profiles.jsonl",
    )
    args = ap.parse_args()

    model = args.model
    global SOURCE_MODEL, SOURCE_PROMPT_VERSION
    SOURCE_MODEL = args.source_model
    SOURCE_PROMPT_VERSION = args.source_version

    if args.revalidate:
        trades = load_trades()
        changed = revalidate(model, trades)
        log.info("%d entrées revalidées différemment", changed)
        n = build_profiles_jsonl(model)
        log.info("%s réécrit : %d profils", PROFILES_PATH, n)
        return

    briefs = load_elements_with_briefs()
    total = len(briefs)

    if args.pilot:
        wanted = {n.lower() for n in PILOT_NAMES}
        picked = {}
        for b in briefs:
            name = b["player_name"].lower()
            if name in wanted and name not in picked:
                picked[name] = b
        briefs = list(picked.values())

    if args.limit:
        briefs = briefs[: args.limit]

    if args.retry_failed:
        broken = []
        for b in briefs:
            rec_key = {"trade_id": b["trade_id"], "receives_key": b["receives_key"], "element_index": b["element_index"]}
            path = cache_path(rec_key, model)
            if not path.exists():
                broken.append(b)
                continue
            with open(path) as f:
                if is_broken(json.load(f)):
                    broken.append(b)
        log.info("--retry-failed : %d/%d éléments à reprendre", len(broken), len(briefs))
        briefs = broken
        args.force = True

    if args.dry_run:
        print(SYSTEM_PROMPT)
        print("\n---\n")
        print(USER_TEMPLATE.format(
            type_label=TYPE_LABELS.get(briefs[0]["type_classified"], briefs[0]["type_classified"]),
            brief=briefs[0]["brief"][:1000] + "...",
        ))
        print(f"\n--- {len(briefs)} éléments seraient traités (sur {total} briefs disponibles) ---")
        return

    for var in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
        if not os.environ.get(var):
            raise SystemExit(f"{var} manquant — voir .env.example")

    trades = load_trades()
    (EXTRACTIONS_DIR / model / EXTRACT_VERSION).mkdir(parents=True, exist_ok=True)
    cached = sum(1 for b in briefs if cache_path(
        {"trade_id": b["trade_id"], "receives_key": b["receives_key"], "element_index": b["element_index"]}, model
    ).exists())
    log.info("%d éléments à traiter (%d déjà en cache) sur %d briefs disponibles", len(briefs), cached, total)

    session = requests.Session()
    done = 0
    warned = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(extract, b, model, trades, session, args.force): b for b in briefs}
        for future in as_completed(futures):
            b = futures[future]
            try:
                payload, from_cache = future.result()
            except Exception as e:
                log.error("Échec %s (trade %s) : %s", b["player_name"], b["trade_id"], e)
                continue

            with lock:
                done += 1
                if not from_cache:
                    if payload["leakage_warnings"]:
                        warned += 1
                        log.warning("[%d/%d] %s — %s", done, len(briefs), b["player_name"], "; ".join(payload["leakage_warnings"]))
                    else:
                        log.info("[%d/%d] %s — ok (%d car.)", done, len(briefs), b["player_name"], len(payload["qualitative_summary"]))

    log.info("Terminé : %d traités, %d avertissements de fuite", done, warned)

    n = build_profiles_jsonl(model)
    log.info("%s réécrit : %d profils", PROFILES_PATH, n)


if __name__ == "__main__":
    main()
