#!/usr/bin/env python3
"""
Extraction de features qualitatives structurées pour le modèle de valeur (issue kii)

Même mécanique que E6 (extract_profile.py) : un appel plain par élément (pas
d'agent, pas de recherche web) à partir du brief brut déjà sur disque — juste un
schéma de sortie différent. E6 produit une ligne de prose pour le prompt
d'entraînement du LLM ; ce script produit un petit JSON de champs catégoriels/
numériques directement exploitables comme features de régression dans
fit_trade_value.py.

Schéma choisi après lecture manuelle d'environ 90 briefs (pas deviné a priori) :

  cap_hit_musd          float | null   AAV en millions $, seulement si un contrat
                                        est ACTIF à la date de référence du brief
  contract_status       entry_level | long_term_3plus | short_term_1_2 |
                         expiring_this_season | pending_rfa_no_contract |
                         pending_ufa_no_contract | non_documente
  injury_concern        none | minor | major | non_documente
  scouting_reservation  none | minor | significant | non_documente
  org_climate           no_conflict_documented | roster_or_depth_management |
                         contract_impasse | open_conflict_or_trade_request |
                         non_documente
  special_teams_role    pk_specialist | pp_specialist | both | not_mentioned

Cap hit et statut contractuel : présents dans la grande majorité des briefs avec
un contrat actif (le prompt de recherche v5-v6 recalcule déjà les durées
relatives à une date fixe). Climat organisationnel : presque toujours "aucun
conflit", avec une minorité nette et bien distincte à l'lecture — gestion
d'effectif pure (ballottage, cap) vs impasse contractuelle (refus de prolonger)
vs conflit ouvert (demande d'échange publique, agent qui parle). Écarté après
lecture : un tag d'archétype de rôle (top-6/depth/two-way) — trop redondant avec
les stats déjà dans le modèle, et le texte trop hétérogène pour le standardiser
sans halluciner des catégories.

Lit  data/raw/briefs/{SOURCE_MODEL}/{SOURCE_PROMPT_VERSION}/*.json (briefs status=completed)
Écrit data/raw/value_features/{trade_id}-{one|two}-{index}.json (cache, un fichier par élément)
      data/enriched/value_features.jsonl (assemblage final, réécrit à chaque run)

Usage:
  python pipelines/extract_value_features.py --dry-run
  python pipelines/extract_value_features.py --limit 20
  python pipelines/extract_value_features.py
  python pipelines/extract_value_features.py --retry-failed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv()

BRIEFS_DIR = Path("data/raw/briefs")
FEATURES_CACHE_DIR = Path("data/raw/value_features")
FEATURES_OUT_PATH = Path("data/enriched/value_features.jsonl")

SOURCE_MODEL = "gpt-5.4-mini"
SOURCE_PROMPT_VERSION = "v6"

MODEL = "gpt-5.4-mini"
VERSION = "v1"

ENUMS = {
    "contract_status": [
        "entry_level", "long_term_3plus", "short_term_1_2", "expiring_this_season",
        "pending_rfa_no_contract", "pending_ufa_no_contract", "non_documente",
    ],
    "injury_concern": ["none", "minor", "major", "non_documente"],
    "scouting_reservation": ["none", "minor", "significant", "non_documente"],
    "org_climate": [
        "no_conflict_documented", "roster_or_depth_management",
        "contract_impasse", "open_conflict_or_trade_request", "non_documente",
    ],
    "special_teams_role": ["pk_specialist", "pp_specialist", "both", "not_mentioned"],
}

SYSTEM_PROMPT = """You extract structured facts from a scouting research report about an NHL player or prospect, for a trade-value regression model. Output ONLY a single JSON object, nothing else — no markdown fences, no commentary before or after.

Required keys and allowed values, exactly as written:

"cap_hit_musd": a number (AAV in millions of dollars, e.g. 875000 -> 0.875) if the
  report documents a contract ACTIVE at its reference date, else null. Never use a
  cap hit belonging to a contract that had already expired, or to a contract signed
  AFTER the reference date. If the player is a pending free agent with no signed
  contract at the reference date, output null here (that fact belongs in
  contract_status, not here).

"contract_status": one of "entry_level", "long_term_3plus" (3+ years remaining
  including current season), "short_term_1_2" (1-2 years remaining), "expiring_this_season",
  "pending_rfa_no_contract", "pending_ufa_no_contract", "non_documente".

"injury_concern": one of "none" (explicitly no issue found), "minor" (short
  absence, day-to-day, fully recovered before the date), "major" (multi-week/month
  absence, surgery, recurring issue, or unresolved at the reference date),
  "non_documente" (health simply not discussed).

"scouting_reservation": one of "none", "minor" (a single mild caveat, e.g. needs to
  add strength), "significant" (a real doubt about NHL readiness, ceiling, or a
  recurring weakness repeated across sources), "non_documente" (no scouting
  reservations section with actual content).

"org_climate": one of "no_conflict_documented" (explicitly checked, nothing found),
  "roster_or_depth_management" (waivers, cap-driven move, buried on the depth
  chart — no personal conflict), "contract_impasse" (team/player at odds over a
  new deal, arbitration filed, refuses to sign long-term — no public trade
  request), "open_conflict_or_trade_request" (public trade request by player or
  agent, captaincy stripped, disciplinary issue, explicit conflict with coach/
  management), "non_documente" (the report doesn't address this at all).

"special_teams_role": one of "pk_specialist" (explicitly flagged as a penalty-kill
  contributor/regular), "pp_specialist" (explicitly flagged for power-play usage),
  "both", "not_mentioned" (the report simply never brings up special teams —
  this is the default for most role players, not an error).

Never infer a value from silence or from what would be typical for a player like
this — every field reflects only what the report explicitly states. When in doubt
between two categories, pick the one requiring less inference."""

USER_TEMPLATE = """Reference date: {trade_date}

Raw research report:
---
{brief}
---

Output the JSON object."""


def load_elements_with_briefs() -> list[dict]:
    records = []
    briefs_dir = BRIEFS_DIR / SOURCE_MODEL / SOURCE_PROMPT_VERSION
    if not briefs_dir.exists():
        raise SystemExit(f"{briefs_dir} introuvable")
    for path in sorted(briefs_dir.glob("*.json")):
        with open(path) as f:
            payload = json.load(f)
        if payload.get("status") != "completed" or not (payload.get("brief") or "").strip():
            continue
        records.append(payload)
    return records


def cache_path(rec: dict) -> Path:
    side = "one" if rec["receives_key"] == "team_one_receives" else "two"
    return FEATURES_CACHE_DIR / VERSION / f"{rec['trade_id']}-{side}-{rec['element_index']}.json"


def parse_json_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


FIELD_ALIASES = {
    # Le modèle généralise naturellement "non_documente" (utilisé pour 4 des 6
    # champs) au champ special_teams_role, dont la valeur "aucune mention" est
    # nommée différemment ("not_mentioned", pas "non_documente" — la distinction
    # sémantique voulue: absence de fait vs absence de rôle spécial) — et une
    # variante orthographique sur org_climate. Corrections cosmétiques, pas des
    # erreurs de contenu.
    "special_teams_role": {"non_documente": "not_mentioned", "non_mentioned": "not_mentioned"},
    "org_climate": {"non_conflict_documented": "no_conflict_documented"},
}


def normalize_fields(fields: dict) -> dict:
    for key, aliases in FIELD_ALIASES.items():
        if fields.get(key) in aliases:
            fields[key] = aliases[fields[key]]
    return fields


def validate_fields(fields: dict) -> list[str]:
    warnings = []
    for key, allowed in ENUMS.items():
        val = fields.get(key)
        if val not in allowed:
            warnings.append(f"{key}: valeur hors énum reçue = {val!r}")
    cap = fields.get("cap_hit_musd")
    if cap is not None and not isinstance(cap, (int, float)):
        warnings.append(f"cap_hit_musd: pas un nombre = {cap!r}")
    if isinstance(cap, (int, float)) and cap > 20:
        warnings.append(f"cap_hit_musd: valeur suspecte (>{cap}M$, probablement pas convertie en millions)")
    return warnings


def call_extraction(brief_payload: dict, session: requests.Session, retries: int = 5) -> dict:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/openai/v1/responses"
    params = {"api-version": os.environ.get("AZURE_OPENAI_API_VERSION", "preview")}
    headers = {"api-key": os.environ["AZURE_OPENAI_API_KEY"], "Content-Type": "application/json"}
    user_content = USER_TEMPLATE.format(trade_date=brief_payload["trade_date"], brief=brief_payload["brief"])
    body = {
        "model": MODEL,
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
    text = "\n".join(text_parts).strip()

    try:
        return parse_json_response(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON invalide reçu du modèle: {e}\n{text[:500]}")


def is_broken(payload: dict) -> bool:
    return bool(payload.get("parse_error")) or not payload.get("fields")


def extract(brief_payload: dict, session: requests.Session, force: bool) -> tuple[dict, bool]:
    rec_key = {
        "trade_id": brief_payload["trade_id"],
        "receives_key": brief_payload["receives_key"],
        "element_index": brief_payload["element_index"],
    }
    path = cache_path(rec_key)
    if path.exists() and not force:
        with open(path) as f:
            return json.load(f), True

    payload = {
        "trade_id": brief_payload["trade_id"],
        "trade_date": brief_payload["trade_date"],
        "receives_key": brief_payload["receives_key"],
        "element_index": brief_payload["element_index"],
        "type_classified": brief_payload["type_classified"],
        "model": MODEL,
        "version": VERSION,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    try:
        fields = normalize_fields(call_extraction(brief_payload, session))
        warnings = validate_fields(fields)
        payload["fields"] = fields
        payload["warnings"] = warnings
    except (RuntimeError, json.JSONDecodeError) as e:
        payload["parse_error"] = str(e)
        payload["fields"] = None
        payload["warnings"] = []

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

    return payload, False


def build_output() -> int:
    cache_dir = FEATURES_CACHE_DIR / VERSION
    entries = []
    flagged = 0
    for path in sorted(cache_dir.glob("*.json")):
        with open(path) as f:
            payload = json.load(f)
        if is_broken(payload):
            continue
        if payload.get("warnings"):
            flagged += 1
        entries.append(payload)

    FEATURES_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FEATURES_OUT_PATH, "w") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    if flagged:
        log.warning("%d entrées avec au moins un avertissement de validation (gardées, voir warnings)", flagged)
    return len(entries)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    briefs = load_elements_with_briefs()
    total = len(briefs)

    if args.limit:
        briefs = briefs[: args.limit]

    if args.retry_failed:
        broken = []
        for b in briefs:
            rec_key = {"trade_id": b["trade_id"], "receives_key": b["receives_key"], "element_index": b["element_index"]}
            path = cache_path(rec_key)
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
        print(USER_TEMPLATE.format(trade_date=briefs[0]["trade_date"], brief=briefs[0]["brief"][:1000] + "..."))
        print(f"\n--- {len(briefs)} éléments seraient traités (sur {total} briefs disponibles) ---")
        return

    for var in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
        if not os.environ.get(var):
            raise SystemExit(f"{var} manquant — voir .env.example")

    FEATURES_CACHE_DIR.joinpath(VERSION).mkdir(parents=True, exist_ok=True)
    cached = sum(1 for b in briefs if cache_path(
        {"trade_id": b["trade_id"], "receives_key": b["receives_key"], "element_index": b["element_index"]}
    ).exists())
    log.info("%d éléments à traiter (%d déjà en cache) sur %d briefs disponibles", len(briefs), cached, total)

    session = requests.Session()
    done = 0
    broken_count = 0
    warned = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(extract, b, session, args.force): b for b in briefs}
        for future in as_completed(futures):
            payload, from_cache = future.result()
            with lock:
                done += 1
                if is_broken(payload):
                    broken_count += 1
                    log.warning("échec %s-%s-%s: %s", payload["trade_id"], payload["receives_key"], payload["element_index"], payload.get("parse_error"))
                elif payload.get("warnings"):
                    warned += 1
                if done % 50 == 0 or done == len(briefs):
                    log.info("%d/%d (%d échecs, %d avertissements)%s", done, len(briefs), broken_count, warned, " [cache]" if from_cache else "")

    n = build_output()
    log.info("%s écrit : %d entrées (%d échecs exclus)", FEATURES_OUT_PATH, n, broken_count)


if __name__ == "__main__":
    main()
