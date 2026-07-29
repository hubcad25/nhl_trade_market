"""
E5 — Agent de recherche par joueur

Une exécution d'agent par paire (joueur, date de trade) via l'API Responses Azure,
avec l'outil de recherche web hébergé. Le modèle boucle côté serveur : on envoie un
POST, on reçoit des blocs reasoning / web_search_call / message.

Produit un brief en prose du joueur tel qu'il était perçu à la date du trade, avec
les URLs sources tirées des annotations du bloc message.

Lit  data/resolved/classified_elements.jsonl (éléments joueurs seulement)
Écrit data/raw/briefs/{modèle}/{version de prompt}/{trade_id}-{one|two}-{index}.json
      (un fichier = un cache)

La fuite temporelle est tolérée ici : c'est du brut. L'extraction (E6) fait le ménage.

Usage:
  python pipelines/research_player.py --pilot          # les cas de plan.md
  python pipelines/research_player.py --pilot --model gpt-5.4-mini
  python pipelines/research_player.py --limit 20
  python pipelines/research_player.py                  # passe complète (727 éléments)
  python pipelines/research_player.py --retry-failed    # reprend les échecs seulement
"""

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

ELEMENTS_PATH = Path("data/resolved/classified_elements.jsonl")
BRIEFS_DIR = Path("data/raw/briefs")

PLAYER_TYPES = {"nhl_skater", "nhl_goalie", "skater_prospect", "goalie_prospect"}

# Tarifs en $US par million de tokens : (entrée, entrée en cache, sortie).
# Relevés le 2026-07-28 sur l'API de prix Azure (prices.azure.com), compteurs
# « … Gl 1M Tokens » — Gl parce que les deux déploiements sont en GlobalStandard
# (vérifié avec `az cognitiveservices account deployment list`).
#
# L'outil web_search n'a aucun compteur publié : sur les 29 394 tarifs du service
# Foundry Models, le seul compteur d'appels d'outil est file-search. La recherche
# semble donc facturée uniquement par les tokens qu'elle réinjecte. À confirmer sur
# la facture — l'absence d'un compteur public n'est pas une preuve de gratuité.
PRICES_PER_M = {
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
}

# Les cas déjà validés manuellement (plan.md, section Pilote), plus un joueur NHL
# établi et un prospect d'élite pour couvrir les trois stades de carrière.
PILOT_NAMES = [
    "Kyle Criscuolo",     # vétéran AHL anonyme, aucune couverture
    "Nils Juntorp",       # pièce accessoire d'un blockbuster
    "Graham Sward",       # défenseur WHL, échange de second plan
    "Shea Weber",         # vétéran NHL de premier plan
    "Kevin Fiala",        # attaquant NHL établi en pleine valeur
    "Joonas Korpisalo",   # gardien établi de la LNH
    "Michael DiPietro",   # gardien espoir
    "Charlie Coyle",      # trade récent (2025) — fraîcheur de la recherche web
    "D Brock Faber",       # espoir en 2022, devenu défenseur étoile — pire cas de rétro-cadrage
                           # (tsn_name garde le préfixe de position pour ce joueur — non nettoyé
                           # en amont, cf. classified_elements.jsonl)
    "Cole Schwindt",      # second espoir, échange de profondeur 2022
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

# Le numéro de version fait partie du chemin de cache : un changement de prompt rend
# les briefs incomparables entre eux, et on veut pouvoir mesurer l'effet d'une
# révision sans perdre le lot précédent. À incrémenter à chaque modification.
PROMPT_VERSION = "v6"

# v1 : la version de plan.md. Deux clauses portaient le poids — « ni pour choisir quoi
#      mettre en avant » et « dis-le explicitement plutôt que de combler ». Tenues par
#      gpt-5.5, pas par gpt-5.4-mini, qui écrivait 2 400 caractères par source citée
#      et se contredisait sur la main de tir de Juntorp.
# v2 : ajoute un protocole de recherche (le mini ne cherchait pas — 2,4 requêtes contre
#      11), l'interdiction de la signalétique non sourcée (le point exact où il a
#      halluciné), et l'inversion explicite du compromis longueur/sourçage.
# v3 : le brief est un document, pas une réponse de chat. gpt-5.4-mini terminait 5
#      briefs sur 5 par « Si tu veux, je peux aussi te le reformater… » et les ouvrait
#      par un préambule sur sa propre méthode. gpt-5.5 ne le faisait jamais, mais la
#      clause est inoffensive pour lui et indispensable si on lit du mini.
# v4 : la clause anti-rétrospective de v1-v3 interdisait de *mentionner* le retour de
#      l'échange, mais rien n'interdisait de *se servir* d'une page qui l'annonce.
#      Sur le pilote, gpt-5.4-mini a construit 33 % de ses sources (16/18 citations
#      pour Shea Weber) sur des communiqués de transaction — des textes écrits après
#      coup, qui encadrent le joueur par ce qu'il a rapporté même quand le brief n'en
#      reprend pas le contenu. gpt-5.5 le faisait aussi, à 13-22 %. v4 interdit la
#      catégorie de source, pas seulement son contenu.
#
#      Relecture manuelle des 10 cas du pilote (pas seulement leurs métriques) :
#      sur 5 briefs citant quand même une annonce malgré l'interdiction, 4 le
#      faisaient pour un fait neutre et daté (contrat, statut de repêchage) tiré
#      de l'annonce du trade RECHERCHÉ — bas risque, le fait ne change pas selon
#      qui le rapporte. Le seul cas réellement dangereux (Charlie Coyle) citait
#      l'annonce d'un trade ULTÉRIEUR et différent du même joueur — vérifié après
#      coup, le fait lui-même (contrat) s'est avéré exact, mais rien dans le
#      prompt ne le garantissait. Conclusion : filtrer par catégorie de source
#      (mot-clé dans l'URL) chasse le mauvais signal. Ce qui compte est daté
#      relativement à quoi, pas d'où ça vient.
# v5 : cible le vrai risque identifié en v4 — une durée relative (« il reste un an »,
#      « agent libre l'an prochain ») est exprimée par rapport à la date de
#      publication de la source, pas à la date du trade. Une source plus récente
#      que le trade peut donc donner un chiffre juste au moment où elle a été
#      écrite mais faux une fois reporté tel quel à la date recherchée.
# v6 : test hors-échantillon sur Jack Eichel (Buffalo → Vegas, 2021-11-04, hors
#      dataset qui commence en 2022-06). Aucune des 8 rubriques v5 ne correspond à
#      un conflit joueur-organisation ; le brief a capturé le désaccord médical
#      prolongé (sous « santé » et « réserves ») mais pas le retrait du capitanat
#      ni la demande d'échange explicite — les deux faits les plus significatifs
#      de ce cas, sans rubrique où atterrir. Ajoute une 9e rubrique dédiée.
PROMPT_TEMPLATE = """Recherche sur le web et produis un portrait de **{name}** tel qu'il était perçu au **{date}**, au moment où il a été échangé.

Contexte de l'échange (pour t'aider à trouver les bons articles — ne le restitue pas dans ta réponse) :
{context}

Méthode. Fais une recherche distincte pour chacune des rubriques ci-dessous avant de commencer à rédiger. Si une recherche ne donne rien d'exploitable, reformule-la au moins une fois avant d'abandonner la rubrique. Ne rédige qu'une fois tes recherches terminées.

Rubriques à couvrir : statut et âge, rang de repêchage, niveau de jeu et production récente, forces reconnues, réserves des recruteurs, projection consensuelle, situation contractuelle, santé, climat avec l'organisation.

La rubrique « climat avec l'organisation » couvre tout ce qui explique pourquoi ce joueur est disponible au-delà de sa valeur sportive : conflit ouvert avec la direction ou l'entraîneur, capitanat retiré ou refusé, demande d'échange rendue publique par le joueur ou son agent, dossier disciplinaire, ou toute autre tension rapportée avant la date du trade. S'il n'y a rien de tel dans les sources, dis-le simplement — l'absence de conflit est aussi une information.

Écris comme si tu rédigeais la veille de l'échange, sans aucune connaissance de ce qui s'est passé depuis. N'évalue pas l'échange, ne mentionne pas ce qu'il a rapporté ni qui est allé dans l'autre sens, et n'utilise jamais la carrière ultérieure du joueur — ni explicitement, ni pour choisir quoi mettre en avant.

N'utilise et ne cite jamais un article qui annonce, commente ou récapitule cet échange — reconnaissable à son titre ou son URL (« trade », « traded », « acquire », « acquires », « receives », « roster transaction », « in exchange for »), ou à une date de publication le jour même ou après le {date}. Même si un tel article contient une information par ailleurs correcte, cherche-la ailleurs, dans une source antérieure et indépendante de l'échange. Si tu ne trouves cette information dans aucune autre source, traite-la comme non documentée.

Règles de sourçage, sans exception :

- Chaque affirmation factuelle est suivie de sa source et de sa date de publication.
- Une affirmation que tu ne peux rattacher à une page que tu as réellement consultée ne doit pas être écrite. Écris « non documenté » à la place.
- Cela vaut d'abord pour la signalétique — taille, poids, main de tir, date de naissance, rang de repêchage, termes du contrat. Ces chiffres ne s'écrivent jamais de mémoire : soit tu les as lus dans une source consultée, soit ils sont « non documenté ».
- Si deux sources se contredisent, donne les deux et dis laquelle tu retiens.
- Une durée relative (« il reste un an sur son contrat », « il devient agent libre la saison prochaine », « il revient de blessure dans deux semaines ») est vraie à la date où la source a été écrite, pas forcément au {date}. Recalcule-la toi-même à partir d'une date fixe (signature du contrat, date de la blessure) plutôt que de recopier la formulation de la source. Si tu ne peux pas la recalculer avec certitude, écris le fait sous forme de date fixe, ou marque-le non documenté.

Un portrait court et entièrement sourcé vaut mieux qu'un portrait complet à moitié deviné. Une rubrique vide est une information utile — ne la comble pas.

Forme. Ta réponse est le portrait, rien d'autre. Pas de préambule sur ta méthode ou sur tes recherches, pas de conclusion s'adressant à un lecteur, pas d'offre de reformuler ou de compléter. Écris une rubrique par section, titrée."""


def cache_path(rec: dict, model: str) -> Path:
    """Un fichier par élément, sous un dossier par modèle.

    `element_index` est relatif au côté de l'échange, donc la clé doit inclure
    `receives_key` — sinon les deux côtés d'un même trade se marchent dessus. Le
    dossier par modèle et par version de prompt permet de comparer deux modèles, ou
    deux révisions du prompt, sur les mêmes éléments sans que l'un écrase l'autre.
    """
    side = "one" if rec["receives_key"] == "team_one_receives" else "two"
    return (
        BRIEFS_DIR / model / PROMPT_VERSION
        / f"{rec['trade_id']}-{side}-{rec['element_index']}.json"
    )


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


def call_agent(prompt: str, model: str, session: requests.Session, retries: int = 5) -> dict:
    """POST /openai/v1/responses avec l'outil web_search. Le modèle boucle côté serveur."""
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/openai/v1/responses"
    params = {"api-version": os.environ.get("AZURE_OPENAI_API_VERSION", "preview")}
    headers = {
        "api-key": os.environ["AZURE_OPENAI_API_KEY"],
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
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


# v4 demande au modèle de ne jamais citer une page qui annonce une transaction —
# une instruction, pas une garantie. Sur le pilote élargi à 10 cas, le brief de
# Charlie Coyle a quand même cité un article sur son échange VERS Columbus pour
# documenter son contrat au moment de l'échange DEPUIS Boston vers Colorado — un
# trade ultérieur du même joueur, cité comme s'il était contemporain. Ce filtre
# post-hoc ne corrige rien dans le brief, il sert de signal d'escalade fiable
# (escalate_briefs.py) là où l'instruction seule ne suffit pas.
#
# Pas de filtre par date : les annotations de l'API ne portent pas de date de
# publication par source, seulement une URL et un titre. Un mot-clé dans l'URL est
# ce qui a effectivement débusqué le cas Coyle ; une date de publication exigerait
# de récupérer chaque page.
TRADE_ANNOUNCEMENT_PATTERN = re.compile(
    r"(?i)(\btrade\b|traded|trades|\bacquire\b|acquires|acquired|"
    r"roster-transaction|\breceives\b|\bexchange\b)"
)


def flag_trade_announcement_sources(sources: list[dict]) -> list[str]:
    """URLs de sources dont le titre ou l'adresse trahit un article sur une
    transaction — n'importe laquelle, pas forcément celle qu'on recherche."""
    flagged = []
    for s in sources:
        haystack = f"{s.get('url', '')} {s.get('title', '') or ''}"
        if TRADE_ANNOUNCEMENT_PATTERN.search(haystack):
            flagged.append(s["url"])
    return flagged


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
        "trade_announcement_sources": flag_trade_announcement_sources(unique_sources),
        "queries": queries,
        "n_searches": len(queries),
        "status": response.get("status"),
        "incomplete_details": response.get("incomplete_details"),
        # Les tokens d'entrée en cache sont facturés 10x moins cher (0,50 $/M contre
        # 5,00 $/M sur GlobalStandard) — sans ce détail, l'estimation de coût est
        # fausse d'un ordre de grandeur, la boucle serveur réinjectant le contexte
        # à chaque tour.
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "cached_input_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def is_broken(payload: dict) -> bool:
    """Un vrai échec — pas de contenu — pas un jugement sur sa qualité."""
    return payload.get("status") != "completed" or not (payload.get("brief") or "").strip()


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


def research(rec: dict, model: str, session: requests.Session, force: bool) -> tuple[dict, bool]:
    """Retourne (payload, from_cache)."""
    path = cache_path(rec, model)
    if path.exists() and not force:
        with open(path) as f:
            return json.load(f), True

    prompt = build_prompt(rec)
    started = time.monotonic()
    response = call_agent(prompt, model, session)
    parsed = parse_response(response)

    payload = {
        "trade_id": rec["trade_id"],
        "trade_date": rec["trade_date"],
        "receives_key": rec["receives_key"],
        "element_index": rec["element_index"],
        "player_name": rec["element"]["tsn_name"],
        "nhl_id": rec["element"]["nhl_id"],
        "type_classified": rec["element"]["type_classified"],
        "model": model,
        "prompt_version": PROMPT_VERSION,
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


def summarize(payloads: list[dict], total_elements: int, model: str) -> None:
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

    log.info("--- Mesures sur %d appels réels (%s) ---", n, model)
    log.info("tokens entrée   : %7d  (moy. %.0f)", inp, inp / n)
    log.info("tokens sortie   : %7d  (moy. %.0f)", out, out / n)
    log.info("recherches web  : %7d  (moy. %.1f)", searches, searches / n)
    log.info("durée           : %6.0fs  (moy. %.0fs)", elapsed, elapsed / n)
    if empty:
        log.warning("briefs vides    : %d", empty)
    cached = sum((p["usage"].get("cached_input_tokens") or 0) for p in fresh)
    p_in, p_cached, p_out = PRICES_PER_M[model]
    cost = (
        (inp - cached) / 1e6 * p_in
        + cached / 1e6 * p_cached
        + out / 1e6 * p_out
    )
    log.info("dont en cache   : %7d  (%.0f%%)", cached, 100 * cached / inp if inp else 0)
    log.info("coût            : %9.2f $  (moy. %.3f $)", cost, cost / n)
    log.info(
        "Extrapolation sur %d éléments : %.0fk tokens entrée, %.0fk sortie, "
        "%.0f recherches, %.0f $",
        total_elements,
        inp / n * total_elements / 1000,
        out / n * total_elements / 1000,
        searches / n * total_elements,
        cost / n * total_elements,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot", action="store_true", help="ne traiter que les cas de test de plan.md")
    ap.add_argument("--limit", type=int, help="nombre maximum d'éléments à traiter")
    ap.add_argument("--workers", type=int, default=4, help="appels concurrents (défaut 4)")
    ap.add_argument("--force", action="store_true", help="ignorer le cache et refaire les appels")
    ap.add_argument(
        "--retry-failed",
        action="store_true",
        help="ne traiter que les éléments sans cache ou avec un cache en échec "
             "(status ≠ completed, brief vide) pour --model ; force ces derniers",
    )
    ap.add_argument("--dry-run", action="store_true", help="afficher un prompt et sortir")
    ap.add_argument(
        "--model",
        default=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5.5"),
        choices=sorted(PRICES_PER_M),
        help="déploiement Azure à interroger (défaut : AZURE_OPENAI_DEPLOYMENT)",
    )
    args = ap.parse_args()

    model = args.model

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

    if args.retry_failed:
        broken = []
        for rec in records:
            path = cache_path(rec, model)
            if not path.exists():
                broken.append(rec)
                continue
            with open(path) as f:
                if is_broken(json.load(f)):
                    broken.append(rec)
        log.info("--retry-failed : %d/%d éléments à reprendre pour %s",
                 len(broken), len(records), model)
        records = broken
        args.force = True  # sans effet sur les absents, nécessaire pour les cassés

    if args.dry_run:
        print(build_prompt(records[0]))
        print(f"\n--- {len(records)} éléments seraient traités (sur {total_elements}) ---")
        return

    for var in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
        if not os.environ.get(var):
            raise SystemExit(f"{var} manquant — voir .env.example")

    (BRIEFS_DIR / model / PROMPT_VERSION).mkdir(parents=True, exist_ok=True)
    cached = sum(1 for rec in records if cache_path(rec, model).exists())
    log.info("%d éléments à traiter (%d déjà en cache) sur %d au total",
             len(records), cached, total_elements)

    session = requests.Session()
    payloads = []
    done = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(research, rec, model, session, args.force): rec for rec in records}
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
    summarize(payloads, total_elements, model)


if __name__ == "__main__":
    main()
