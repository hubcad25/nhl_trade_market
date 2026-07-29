"""
E5 — Agent de recherche par joueur

Une exécution d'agent par paire (joueur, date de trade) via l'API Responses Azure,
avec l'outil de recherche web hébergé. Le modèle boucle côté serveur : on envoie un
POST, on reçoit des blocs reasoning / web_search_call / message.

Produit un brief en prose du joueur tel qu'il était perçu à la date du trade, avec
les URLs sources tirées des annotations du bloc message.

Lit  data/resolved/classified_elements.jsonl (éléments joueurs seulement)
Écrit data/raw/briefs/{trade_id}-{one|two}-{element_index}.json (un fichier = un cache)

La fuite temporelle est tolérée ici : c'est du brut. L'extraction (E6) fait le ménage.

Usage:
  python pipelines/research_player.py --pilot          # les 5 cas de plan.md
  python pipelines/research_player.py --limit 20
  python pipelines/research_player.py                  # passe complète (727 éléments)
"""

import os
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

ELEMENTS_PATH = Path("data/resolved/classified_elements.jsonl")
BRIEFS_DIR = Path("data/raw/briefs")

PLAYER_TYPES = {"nhl_skater", "nhl_goalie", "skater_prospect", "goalie_prospect"}

# Les cas déjà validés manuellement (plan.md, section Pilote), plus un joueur NHL
# établi et un prospect d'élite pour couvrir les trois stades de carrière.
PILOT_NAMES = [
    "Kyle Criscuolo",     # vétéran AHL anonyme, aucune couverture
    "Nils Juntorp",       # pièce accessoire d'un blockbuster
    "Graham Sward",       # défenseur WHL, échange de second plan
    "Shea Weber",         # vétéran NHL de premier plan
    "Kevin Fiala",        # attaquant NHL établi en pleine valeur
]

POSITION_LABELS = {
    "L": "ailier gauche",
    "R": "ailier droit",
    "C": "centre",
    "D": "défenseur",
    "G": "gardien",
}

# Ne pas exposer le seuil de 25 matchs qui produit cette classification : le pilote
# a montré que l'agent le recopie tel quel dans la prose ("il demeure sous le seuil
# des 25 matchs"), ce qui injecte un artefact de notre pipeline dans le brief.
STAGE_LABELS = {
    "nhl_skater": "joueur établi de la LNH",
    "nhl_goalie": "gardien établi de la LNH",
    "skater_prospect": "espoir, sans poste régulier dans la LNH",
    "goalie_prospect": "gardien espoir, sans poste régulier dans la LNH",
}

PROMPT_TEMPLATE = """Recherche sur le web et produis un portrait de **{name}** tel qu'il était perçu au **{date}**, au moment où il a été échangé.

Contexte de l'échange (pour t'aider à trouver les bons articles — ne le restitue pas dans ta réponse) :
{context}

Écris comme si tu rédigeais la veille de l'échange, sans aucune connaissance de ce qui s'est passé depuis. N'évalue pas l'échange, ne mentionne pas ce qu'il a rapporté ni qui est allé dans l'autre sens, et n'utilise jamais la carrière ultérieure du joueur — ni explicitement, ni pour choisir quoi mettre en avant.

Couvre : statut et âge, rang de repêchage, niveau de jeu et production récente, forces reconnues, réserves des recruteurs, projection consensuelle, situation contractuelle, santé.

Cite tes sources avec leur date de publication. Si l'information est mince, dis-le explicitement plutôt que de combler."""


def cache_path(rec: dict) -> Path:
    """Un fichier par élément. `element_index` est relatif au côté de l'échange,
    donc la clé doit inclure `receives_key` — sinon les deux côtés d'un même trade
    se marchent dessus."""
    side = "one" if rec["receives_key"] == "team_one_receives" else "two"
    return BRIEFS_DIR / f"{rec['trade_id']}-{side}-{rec['element_index']}.json"


def build_context(rec: dict) -> str:
    """Le contexte du trade, en clair pour l'agent.

    L'âge et les matchs NHL avant le trade ne sont pas encore disponibles :
    classified_elements.jsonl ne porte pas ces champs, ils viendront du module
    stats NHL (issue pfn). Le stade de carrière ci-dessous est ce qu'on a en
    attendant — il vient du seuil de 25 matchs appliqué à la classification.
    """
    el = rec["element"]
    lines = [
        f"- échangé le {rec['trade_date']}",
        f"- des {rec['giving_team']['name']} vers les {rec['receiving_team']['name']}",
        f"- statut au moment de l'échange : {STAGE_LABELS[el['type_classified']]}",
    ]
    position = el.get("position")
    if position:
        lines.append(f"- position : {POSITION_LABELS.get(position, position)}")
    return "\n".join(lines)


def build_prompt(rec: dict) -> str:
    return PROMPT_TEMPLATE.format(
        name=rec["element"]["tsn_name"],
        date=rec["trade_date"],
        context=build_context(rec),
    )


def call_agent(prompt: str, session: requests.Session, retries: int = 5) -> dict:
    """POST /openai/v1/responses avec l'outil web_search. Le modèle boucle côté serveur."""
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/openai/v1/responses"
    params = {"api-version": os.environ.get("AZURE_OPENAI_API_VERSION", "preview")}
    headers = {
        "api-key": os.environ["AZURE_OPENAI_API_KEY"],
        "Content-Type": "application/json",
    }
    body = {
        "model": os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5.5"),
        "input": prompt,
        "tools": [{"type": "web_search"}],
    }

    delay = 5.0
    for attempt in range(retries):
        try:
            r = session.post(url, params=params, headers=headers, json=body, timeout=900)
            if r.status_code in (429, 500, 502, 503, 504):
                wait = delay * (2 ** attempt)
                log.warning("HTTP %s — nouvelle tentative dans %.0fs", r.status_code, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            wait = delay * (2 ** attempt)
            log.warning("Erreur %s — nouvelle tentative dans %.0fs", e, wait)
            time.sleep(wait)
    raise RuntimeError(f"Échec après {retries} tentatives")


def parse_response(response: dict) -> dict:
    """Extrait le texte du bloc message, ses annotations de sources, et les requêtes
    de recherche effectuées. Les blocs reasoning sont ignorés (chiffrés ou vides)."""
    text_parts = []
    sources = []
    queries = []

    for block in response.get("output", []):
        btype = block.get("type")

        if btype == "web_search_call":
            action = block.get("action") or {}
            query = action.get("query")
            if query:
                queries.append(query)

        elif btype == "message":
            for part in block.get("content", []):
                if part.get("type") != "output_text":
                    continue
                text_parts.append(part.get("text", ""))
                for ann in part.get("annotations") or []:
                    url = ann.get("url")
                    if url:
                        sources.append({"url": url, "title": ann.get("title")})

    # Dédoublonner les sources en gardant l'ordre d'apparition
    seen = set()
    unique_sources = []
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique_sources.append(s)

    usage = response.get("usage") or {}
    return {
        "brief": "\n".join(text_parts).strip(),
        "sources": unique_sources,
        "queries": queries,
        "n_searches": len(queries),
        "status": response.get("status"),
        "incomplete_details": response.get("incomplete_details"),
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def load_elements() -> list[dict]:
    records = []
    with open(ELEMENTS_PATH) as f:
        for line in f:
            rec = json.loads(line)
            if rec["element"]["type_classified"] not in PLAYER_TYPES:
                continue
            if not rec["element"].get("tsn_name"):
                continue
            records.append(rec)
    return records


def research(rec: dict, session: requests.Session, force: bool) -> tuple[dict, bool]:
    """Retourne (payload, from_cache)."""
    path = cache_path(rec)
    if path.exists() and not force:
        with open(path) as f:
            return json.load(f), True

    prompt = build_prompt(rec)
    started = time.monotonic()
    response = call_agent(prompt, session)
    parsed = parse_response(response)

    payload = {
        "trade_id": rec["trade_id"],
        "trade_date": rec["trade_date"],
        "receives_key": rec["receives_key"],
        "element_index": rec["element_index"],
        "player_name": rec["element"]["tsn_name"],
        "nhl_id": rec["element"]["nhl_id"],
        "type_classified": rec["element"]["type_classified"],
        "model": os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5.5"),
        "api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "preview"),
        "prompt": prompt,
        "elapsed_s": round(time.monotonic() - started, 1),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **parsed,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

    return payload, False


def summarize(payloads: list[dict], total_elements: int) -> None:
    """Ce qu'il faut pour extrapoler le coût de la passe complète (issue o80)."""
    fresh = [p for p in payloads if p.get("usage", {}).get("total_tokens")]
    if not fresh:
        return

    n = len(fresh)
    inp = sum(p["usage"]["input_tokens"] or 0 for p in fresh)
    out = sum(p["usage"]["output_tokens"] or 0 for p in fresh)
    searches = sum(p["n_searches"] for p in fresh)
    elapsed = sum(p.get("elapsed_s") or 0 for p in fresh)
    empty = sum(1 for p in fresh if not p["brief"])

    log.info("--- Mesures sur %d appels réels ---", n)
    log.info("tokens entrée   : %7d  (moy. %.0f)", inp, inp / n)
    log.info("tokens sortie   : %7d  (moy. %.0f)", out, out / n)
    log.info("recherches web  : %7d  (moy. %.1f)", searches, searches / n)
    log.info("durée           : %6.0fs  (moy. %.0fs)", elapsed, elapsed / n)
    if empty:
        log.warning("briefs vides    : %d", empty)
    log.info(
        "Extrapolation sur %d éléments : %.0fk tokens entrée, %.0fk sortie, %.0f recherches",
        total_elements,
        inp / n * total_elements / 1000,
        out / n * total_elements / 1000,
        searches / n * total_elements,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot", action="store_true", help="ne traiter que les cas de test de plan.md")
    ap.add_argument("--limit", type=int, help="nombre maximum d'éléments à traiter")
    ap.add_argument("--workers", type=int, default=4, help="appels concurrents (défaut 4)")
    ap.add_argument("--force", action="store_true", help="ignorer le cache et refaire les appels")
    ap.add_argument("--dry-run", action="store_true", help="afficher un prompt et sortir")
    args = ap.parse_args()

    records = load_elements()
    total_elements = len(records)

    if args.pilot:
        wanted = {n.lower() for n in PILOT_NAMES}
        picked = {}
        for rec in records:
            name = rec["element"]["tsn_name"].lower()
            if name in wanted and name not in picked:
                picked[name] = rec
        missing = wanted - set(picked)
        if missing:
            log.warning("Cas pilote introuvables dans les éléments : %s", ", ".join(sorted(missing)))
        records = list(picked.values())

    if args.limit:
        records = records[: args.limit]

    if args.dry_run:
        print(build_prompt(records[0]))
        print(f"\n--- {len(records)} éléments seraient traités (sur {total_elements}) ---")
        return

    for var in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
        if not os.environ.get(var):
            raise SystemExit(f"{var} manquant — voir .env.example")

    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    cached = sum(1 for rec in records if cache_path(rec).exists())
    log.info("%d éléments à traiter (%d déjà en cache) sur %d au total",
             len(records), cached, total_elements)

    session = requests.Session()
    payloads = []
    done = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(research, rec, session, args.force): rec for rec in records}
        for future in as_completed(futures):
            rec = futures[future]
            try:
                payload, from_cache = future.result()
            except Exception as e:
                log.error("Échec %s (trade %s) : %s", rec["element"]["tsn_name"], rec["trade_id"], e)
                continue

            with lock:
                done += 1
                if not from_cache:
                    payloads.append(payload)
                    log.info(
                        "[%d/%d] %s (%s) — %d recherches, %d sources, %d tokens",
                        done, len(records), payload["player_name"], payload["trade_date"],
                        payload["n_searches"], len(payload["sources"]),
                        payload["usage"]["total_tokens"] or 0,
                    )

    log.info("Terminé — %d/%d éléments, %d nouveaux appels", done, len(records), len(payloads))
    summarize(payloads, total_elements)


if __name__ == "__main__":
    main()
