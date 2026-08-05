#!/usr/bin/env python3
"""
Modèle de valeur d'échange latente — équivalence révélée (issue kii)

Un trade n'a pas de prix observé : on observe seulement des paires de paquets que
deux DG ont jugés à peu près équivalents à trade_date. L'approche (type charts de
valeur de picks NFL, Massey-Thaler) pose une fonction de valeur f(features) à
coefficients inconnus et les ajuste pour que log f(côté A) ≈ log f(côté B) sur
tous les trades du corpus où les deux côtés sont entièrement featurisables. Les
coefficients ajustés SONT l'impact de chaque variable ; f() appliquée à un joueur
EST la mesure de valeur.

V1 était volontairement limité aux features déterministes déjà enrichies (âge,
position, stats coupées à trade_date, tier de pick) — la régression d'équivalence
a d'abord été validée sur ce qui était gratuit avant d'investir dans un pipeline
d'extraction. V2 a testé les 8 features qualitatives structurées extraites des
briefs (cap hit, statut contractuel, blessure, réserves scouting, climat
organisationnel x2, unités spéciales — voir extract_value_features.py) sur les
familles skater/goalie : PIRE que le déterministe seul sur holdout à tout niveau
de régularisation (476 trades ne supportent pas 16 paramètres de plus). Recherche
exhaustive sur les sous-ensembles, validée sur 10 splits train/holdout
indépendants : un trio (injury_ordinal, contract_impasse_flag,
reservation_ordinal — voir QUALI_FEATURES) gagne sur 9/10 splits. Les 5 autres
features avaient des coefficients théoriquement défendables mais coûtaient plus
de variance qu'elles n'apportaient de signal distinguable du bruit à 476 trades.
C'est la version retenue en production.

Non inclus (pas construits) : cap hit au-delà de ce test (a nui, pas aidé, une
fois inclus dans la régression — contrat structuré séparé reste l'issue i4e si
jamais revisité), signal de contexte d'équipe / classement (get_standings, déjà
branché pour ki3 mais pas utilisé ici — l'hypothèse acheteur/vendeur a été mise
de côté).

Trois familles de features, chacune avec ses propres coefficients :
  - skater  (nhl_skater + skater_prospect) : âge, taille, position, production
    saison/carrière, repêchage
  - goalie  (nhl_goalie + goalie_prospect)  : âge, taille, arrêts/GAA saison et
    carrière, repêchage
  - pick                                    : ronde, rang overall estimé, années
    avant le repêchage, conditionnel ou non

Un élément sans feature exploitable (future_consideration, unresolved, ou stats/bio
manquantes) rend son CÔTÉ du trade infeasible pour l'ajustement — pas juste
l'élément : on ne peut pas comparer un côté partiellement observé à l'autre. Le
trade entier est alors exclu de l'ajustement (mais garde ses autres éléments
ailleurs dans le pipeline, ceci ne touche que ce script).

value(e) = exp(w_famille · standardize(x(e)) + b_famille) — toujours positif,
sommable sur un côté. L'échelle globale n'est pas identifiée par construction (un
même facteur multiplicatif appliqué à tous les intercepts laisse le résidu
inchangé) : on ancre après coup sur un pick de fin de 1re ronde ~28e au total, 1 an
avant le repêchage, non conditionnel = 1.0 unité, pour que les valeurs rapportées
soient interprétables.

Lit  data/resolved/classified_elements.jsonl
     data/enriched/stats.jsonl
     data/enriched/bio.jsonl
     data/enriched/picks.jsonl
Écrit data/enriched/value_model.json  (coefficients, standardisation, ancre, diagnostics)
      data/enriched/values.jsonl      (valeur par élément, univers utilisable seulement)

Usage:
  python pipelines/fit_trade_value.py
  python pipelines/fit_trade_value.py --lambda-reg 0.01 --seed 7
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CLASSIFIED_PATH = Path("data/resolved/classified_elements.jsonl")
STATS_PATH = Path("data/enriched/stats.jsonl")
BIO_PATH = Path("data/enriched/bio.jsonl")
PICKS_PATH = Path("data/enriched/picks.jsonl")
VALUE_FEATURES_PATH = Path("data/enriched/value_features.jsonl")
MODEL_OUT_PATH = Path("data/enriched/value_model.json")
VALUES_OUT_PATH = Path("data/enriched/values.jsonl")

SKATER_TYPES = {"nhl_skater", "skater_prospect"}
GOALIE_TYPES = {"nhl_goalie", "goalie_prospect"}
PICK_TYPES = {"pick"}

AGE_CENTER = 27.0
LEAGUE_AVG_SAVE_PCTG = 0.900
LEAGUE_AVG_GAA = 3.00
UNDRAFTED_OVERALL = 224.0  # ~fin de 7e ronde, ligue à 32 équipes

# Dérivées de value_features.jsonl (extraction quali structurée, schéma détaillé
# dans extract_value_features.py). Le jeu complet (8 features: cap hit x2, statut
# FA, blessure, réserves, climat x2, unités spéciales) a été mesuré PIRE que le
# déterministe seul sur holdout (0.533 vs 0.509, à tout niveau de régularisation
# testé 0.01-0.8) — 476 trades ne supportent pas 16 paramètres de plus. Recherche
# exhaustive sur les 2^8 sous-ensembles (voir kii, notes du 2026-08) : ce trio
# gagne sur 9/10 splits train/holdout indépendants (0.5091 -> 0.5042 en moyenne),
# pas juste sur le split par défaut — les 5 autres features (cap hit, agent libre
# imminent, conflit ouvert, unités spéciales) coûtaient plus de variance qu'elles
# n'apportaient de signal, malgré des coefficients théoriquement défendables.
QUALI_FEATURES = ["injury_ordinal", "contract_impasse_flag", "reservation_ordinal"]

SKATER_FEATURES = [
    # age_c_sq retiré (validé) : signe instable/à contre-sens (voir kii, notes
    # d'août 2026) — le terme quadratique tentait de capter une baisse de valeur
    # en fin de carrière, mais les joueurs de 31-34 ans qui se font VRAIMENT
    # échanger sont pré-sélectionnés pour être encore bons (les autres sont
    # coupés/rachetés, jamais échangés) — biais de sélection, pas du bruit. Le
    # terme linéaire seul capture une pénalité d'âge monotone et stable (signe
    # négatif consistant sur 10 splits), sans la fausse remontée en fin de courbe.
    "age_c", "height_in", "is_defense",
    "season_gp", "season_ppg", "season_ppg_x_defense", "career_gp_log", "career_ppg",
    "draft_overall_norm", "undrafted",
] + QUALI_FEATURES
GOALIE_FEATURES = [
    "age_c", "age_c_sq", "height_in",
    "season_gp", "season_save_pctg", "season_gaa",
    "career_gp_log", "career_save_pctg",
    "draft_overall_norm", "undrafted",
] + QUALI_FEATURES
PICK_FEATURES = ["overall_estimate_log", "years_out", "is_conditional"]

FAMILY_FEATURES = {"skater": SKATER_FEATURES, "goalie": GOALIE_FEATURES, "pick": PICK_FEATURES}
FAMILIES = ["skater", "goalie", "pick"]


def load_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def key_of(rec: dict) -> tuple:
    return (rec["trade_id"], rec["receives_key"], rec["element_index"])


def parse_pick_overall(estimated_pick_range: str | None, round_: int) -> float:
    fallback = (round_ - 1) * 32 + 16  # milieu approx de la ronde, ligue à 32 équipes
    if not estimated_pick_range:
        return fallback
    s = estimated_pick_range.strip().lower()
    if s == "conditionnel":
        return fallback
    if s.startswith("top "):
        try:
            n = int(s.split()[1])
            return (1 + n) / 2
        except (ValueError, IndexError):
            return fallback
    if "-" in s:
        a, b = s.split("-", 1)
        try:
            return (int(a) + int(b)) / 2
        except ValueError:
            return fallback
    try:
        return float(s)
    except ValueError:
        return fallback


def featurize_quali(quali_rec: dict | None) -> dict:
    """Traduit les champs de value_features.jsonl (extract_value_features.py) en
    features numériques. quali_rec=None (pas de brief pour cet élément) retombe
    sur des valeurs neutres — mêmes valeurs qu'un brief qui n'aurait rien trouvé,
    pas une pénalité ni un bonus caché."""
    if quali_rec is None:
        fields = {}
    else:
        fields = quali_rec.get("fields") or {}

    injury_map = {"none": 0.0, "non_documente": 0.0, "minor": 1.0, "major": 2.0}
    injury_ordinal = injury_map.get(fields.get("injury_concern"), 0.0)

    reservation_map = {"none": 0.0, "non_documente": 0.0, "minor": 1.0, "significant": 2.0}
    reservation_ordinal = reservation_map.get(fields.get("scouting_reservation"), 0.0)

    contract_impasse_flag = 1.0 if fields.get("org_climate") == "contract_impasse" else 0.0

    return {
        "injury_ordinal": injury_ordinal,
        "reservation_ordinal": reservation_ordinal,
        "contract_impasse_flag": contract_impasse_flag,
    }


def featurize_skater(elem: dict, stats_rec: dict, bio_rec: dict, quali_rec: dict | None = None) -> dict | None:
    age = bio_rec.get("age_at_trade")
    height = bio_rec.get("height_in")
    if age is None or height is None:
        return None
    position = elem["element"].get("position")
    is_defense = 1.0 if position == "D" else 0.0

    season = stats_rec["stats"]["season"]
    career = stats_rec["stats"]["career"]
    season_gp = season.get("games_played") or 0
    season_ppg = (season.get("points") or 0) / season_gp if season_gp else 0.0
    career_gp = career.get("games_played") or 0
    career_ppg = (career.get("points") or 0) / career_gp if career_gp else 0.0

    draft_overall = bio_rec.get("draft_overall")
    undrafted = 1.0 if draft_overall is None else 0.0
    draft_overall_norm = (draft_overall if draft_overall is not None else UNDRAFTED_OVERALL) / UNDRAFTED_OVERALL

    age_c = age - AGE_CENTER
    return {
        "age_c": age_c,
        "age_c_sq": age_c ** 2,
        "height_in": height,
        "is_defense": is_defense,
        "season_gp": season_gp,
        "season_ppg": season_ppg,
        "season_ppg_x_defense": season_ppg * is_defense,
        "career_gp_log": math.log1p(career_gp),
        "career_ppg": career_ppg,
        "draft_overall_norm": draft_overall_norm,
        "undrafted": undrafted,
        **featurize_quali(quali_rec),
    }


def featurize_goalie(elem: dict, stats_rec: dict, bio_rec: dict, quali_rec: dict | None = None) -> dict | None:
    age = bio_rec.get("age_at_trade")
    height = bio_rec.get("height_in")
    if age is None or height is None:
        return None

    season = stats_rec["stats"]["season"]
    career = stats_rec["stats"]["career"]
    season_gp = season.get("games_played") or 0
    season_save_pctg = season.get("save_pctg", LEAGUE_AVG_SAVE_PCTG) if season_gp else LEAGUE_AVG_SAVE_PCTG
    season_gaa = season.get("goals_against_avg", LEAGUE_AVG_GAA) if season_gp else LEAGUE_AVG_GAA
    career_gp = career.get("games_played") or 0
    career_save_pctg = career.get("save_pctg", LEAGUE_AVG_SAVE_PCTG) if career_gp else LEAGUE_AVG_SAVE_PCTG

    draft_overall = bio_rec.get("draft_overall")
    undrafted = 1.0 if draft_overall is None else 0.0
    draft_overall_norm = (draft_overall if draft_overall is not None else UNDRAFTED_OVERALL) / UNDRAFTED_OVERALL

    age_c = age - AGE_CENTER
    return {
        "age_c": age_c,
        "age_c_sq": age_c ** 2,
        "height_in": height,
        "season_gp": season_gp,
        "season_save_pctg": season_save_pctg or LEAGUE_AVG_SAVE_PCTG,
        "season_gaa": season_gaa if season_gaa is not None else LEAGUE_AVG_GAA,
        "career_gp_log": math.log1p(career_gp),
        "career_save_pctg": career_save_pctg or LEAGUE_AVG_SAVE_PCTG,
        "draft_overall_norm": draft_overall_norm,
        "undrafted": undrafted,
        **featurize_quali(quali_rec),
    }


def featurize_pick(pick_rec: dict, trade_year: int) -> dict | None:
    pick = pick_rec["pick"]
    round_ = pick.get("round")
    if round_ is None:
        return None
    draft_year = pick.get("draft_year")
    years_out = max(0, draft_year - trade_year) if draft_year else 0
    overall_estimate = parse_pick_overall(pick.get("estimated_pick_range"), round_)
    return {
        "round": float(round_),
        "overall_estimate": overall_estimate,
        # value = exp(w . x) avec ce terme donne overall^w — une décroissance en
        # loi de puissance, la forme habituelle des charts de valeur de picks
        # (les tout premiers rangs valent disproportionnellement plus), que le
        # terme linéaire seul ne peut pas représenter. "round" et "overall_estimate"
        # bruts restent dans le dict pour debug mais ne sont plus dans PICK_FEATURES
        # (colinéaires avec ce terme — les garder tous les trois diluait le poids
        # entre 3 variables quasi redondantes plutôt que de le concentrer).
        "overall_estimate_log": math.log(overall_estimate),
        # 1/overall_estimate essayé pour une courbe encore plus convexe (le 1er
        # choix ressortait à peine 2x au-dessus du dernier rang, trop plat) —
        # abandonné : seulement 31 picks à overall<=5 dans tout le corpus,
        # 1/overall explose précisément dans cette zone la moins peuplée, et le
        # coefficient s'est mis à surapprendre sur cette poignée de points —
        # au point de rendre pick #1 MOINS valorisé que pick #5 (non
        # monotone). Le corpus n'a simplement pas assez de trades avec un pick
        # de tout haut de repêchage pour contraindre un terme aussi sensible.
        "years_out": float(years_out),
        "is_conditional": 1.0 if pick.get("is_conditional") else 0.0,
    }


def build_universe() -> tuple[list[dict], dict]:
    """Groupe les éléments par trade, featurise, ne garde que les trades où les
    deux côtés sont entièrement featurisables. Retourne (trades_usables, stats_diagnostic)."""
    elements = load_jsonl(CLASSIFIED_PATH)
    stats_by_key = {key_of(r): r for r in load_jsonl(STATS_PATH)}
    bio_by_key = {key_of(r): r for r in load_jsonl(BIO_PATH)}
    picks_by_key = {key_of(r): r for r in load_jsonl(PICKS_PATH)}
    quali_by_key = {key_of(r): r for r in load_jsonl(VALUE_FEATURES_PATH)} if VALUE_FEATURES_PATH.exists() else {}

    by_trade: dict[int, list[dict]] = defaultdict(list)
    for e in elements:
        by_trade[e["trade_id"]].append(e)

    skipped_by_type: dict[str, int] = defaultdict(int)
    trades: list[dict] = []

    for trade_id, elems in by_trade.items():
        sides: dict[str, list[dict]] = defaultdict(list)
        for e in elems:
            sides[e["receives_key"]].append(e)
        if len(sides) != 2:
            continue

        trade_date = elems[0]["trade_date"]
        trade_year = int(trade_date[:4])

        side_features: dict[str, list[tuple[str, dict]]] = {}
        feasible = True
        for side_key, side_elems in sides.items():
            featurized_side = []
            for e in side_elems:
                t = e["element"]["type_classified"]
                k = key_of(e)
                feats = None
                if t in SKATER_TYPES and k in stats_by_key and k in bio_by_key:
                    feats = featurize_skater(e, stats_by_key[k], bio_by_key[k], quali_by_key.get(k))
                    family = "skater"
                elif t in GOALIE_TYPES and k in stats_by_key and k in bio_by_key:
                    feats = featurize_goalie(e, stats_by_key[k], bio_by_key[k], quali_by_key.get(k))
                    family = "goalie"
                elif t in PICK_TYPES and k in picks_by_key:
                    feats = featurize_pick(picks_by_key[k], trade_year)
                    family = "pick"
                else:
                    family = None

                if feats is None:
                    skipped_by_type[t] += 1
                    feasible = False
                    break
                featurized_side.append((family, feats))
            if not feasible or not featurized_side:
                feasible = False
                break
            side_features[side_key] = featurized_side

        if feasible and len(side_features) == 2:
            a, b = side_features.values()
            trades.append({"trade_id": trade_id, "sides": [a, b]})

    diagnostics = {
        "n_trades_total": len(by_trade),
        "n_trades_usable": len(trades),
        "skipped_element_types": dict(skipped_by_type),
    }
    return trades, diagnostics


def standardize_families(trades: list[dict]) -> dict:
    """Moyenne/écart-type par feature et par famille, calculés sur l'univers
    utilisable. Retourne {famille: {"mean": array, "std": array}}."""
    raw: dict[str, list[list[float]]] = {fam: [] for fam in FAMILIES}
    for t in trades:
        for side in t["sides"]:
            for family, feats in side:
                raw[family].append([feats[f] for f in FAMILY_FEATURES[family]])

    stand = {}
    for fam in FAMILIES:
        arr = np.array(raw[fam]) if raw[fam] else np.zeros((0, len(FAMILY_FEATURES[fam])))
        mean = arr.mean(axis=0) if len(arr) else np.zeros(len(FAMILY_FEATURES[fam]))
        std = arr.std(axis=0) if len(arr) else np.ones(len(FAMILY_FEATURES[fam]))
        std[std < 1e-6] = 1.0
        stand[fam] = {"mean": mean, "std": std}
    return stand


def to_std_vector(family: str, feats: dict, stand: dict) -> np.ndarray:
    raw = np.array([feats[f] for f in FAMILY_FEATURES[family]])
    return (raw - stand[family]["mean"]) / stand[family]["std"]


def pack_trades_for_fit(trades: list[dict], stand: dict) -> list[dict]:
    packed = []
    for t in trades:
        sides_std = []
        for side in t["sides"]:
            sides_std.append([(fam, to_std_vector(fam, feats, stand)) for fam, feats in side])
        packed.append({"trade_id": t["trade_id"], "sides": sides_std})
    return packed


def param_layout() -> dict:
    """Offsets du vecteur de paramètres plat: pour chaque famille, len(features) poids + 1 biais."""
    layout = {}
    offset = 0
    for fam in FAMILIES:
        n = len(FAMILY_FEATURES[fam])
        layout[fam] = {"w_slice": slice(offset, offset + n), "b_index": offset + n}
        offset += n + 1
    layout["_total"] = offset
    return layout


def unpack_params(params: np.ndarray, layout: dict) -> dict:
    out = {}
    for fam in FAMILIES:
        w = params[layout[fam]["w_slice"]]
        b = params[layout[fam]["b_index"]]
        out[fam] = (w, b)
    return out


def side_log_value(side: list[tuple], wb: dict) -> float:
    total = 0.0
    for fam, x_std in side:
        w, b = wb[fam]
        total += math.exp(float(np.dot(w, x_std)) + b)
    return math.log(total)


def residuals(params: np.ndarray, packed_trades: list[dict], layout: dict) -> np.ndarray:
    wb = unpack_params(params, layout)
    res = np.empty(len(packed_trades))
    for i, t in enumerate(packed_trades):
        side_a, side_b = t["sides"]
        res[i] = side_log_value(side_a, wb) - side_log_value(side_b, wb)
    return res


def loss(params: np.ndarray, packed_trades: list[dict], layout: dict, lambda_reg: float) -> float:
    r = residuals(params, packed_trades, layout)
    reg = 0.0
    wb = unpack_params(params, layout)
    for fam in FAMILIES:
        w, _b = wb[fam]
        reg += float(np.dot(w, w))
    return float(np.mean(r ** 2) + lambda_reg * reg)


def fit(packed_trades: list[dict], layout: dict, lambda_reg: float) -> np.ndarray:
    x0 = np.zeros(layout["_total"])
    result = minimize(
        loss, x0, args=(packed_trades, layout, lambda_reg),
        method="L-BFGS-B", options={"maxiter": 2000},
    )
    if not result.success:
        log.warning("optimisation non convergée proprement: %s", result.message)
    return result.x


def rmse(params: np.ndarray, packed_trades: list[dict], layout: dict) -> float:
    if not packed_trades:
        return float("nan")
    r = residuals(params, packed_trades, layout)
    return float(np.sqrt(np.mean(r ** 2)))


def main() -> None:
    global SKATER_FEATURES, GOALIE_FEATURES, FAMILY_FEATURES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lambda-reg", type=float, default=0.01, help="pénalité L2 sur les poids (pas les biais)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--holdout-frac", type=float, default=0.15)
    parser.add_argument(
        "--no-quali", action="store_true",
        help="exclut QUALI_FEATURES (injury_ordinal, contract_impasse_flag, "
             "reservation_ordinal) — pour comparer contre le déterministe seul. "
             "Le jeu de 3 features actuel est validé (9/10 splits train/holdout "
             "indépendants, 0.5091 -> 0.5042 en moyenne) et actif par défaut ; un "
             "jeu de 8 features testé avant lui était pire à tout niveau de "
             "régularisation (476 trades ne supportent pas 16 paramètres de plus) "
             "— voir QUALI_FEATURES pour l'historique.",
    )
    args = parser.parse_args()

    if args.no_quali:
        SKATER_FEATURES = [f for f in SKATER_FEATURES if f not in QUALI_FEATURES]
        GOALIE_FEATURES = [f for f in GOALIE_FEATURES if f not in QUALI_FEATURES]
        FAMILY_FEATURES = {"skater": SKATER_FEATURES, "goalie": GOALIE_FEATURES, "pick": PICK_FEATURES}

    trades, diag = build_universe()
    log.info(
        "univers: %d/%d trades utilisables (deux côtés featurisables); éléments exclus par type: %s",
        diag["n_trades_usable"], diag["n_trades_total"], diag["skipped_element_types"],
    )
    if len(trades) < 30:
        log.error("trop peu de trades utilisables (%d) pour ajuster quoi que ce soit de fiable", len(trades))
        return

    stand = standardize_families(trades)
    packed = pack_trades_for_fit(trades, stand)
    layout = param_layout()

    rng = np.random.default_rng(args.seed)
    order = rng.permutation(len(packed))
    n_holdout = max(1, int(len(packed) * args.holdout_frac))
    holdout_idx = set(order[:n_holdout].tolist())
    train_split = [packed[i] for i in range(len(packed)) if i not in holdout_idx]
    holdout_split = [packed[i] for i in range(len(packed)) if i in holdout_idx]

    log.info("ajustement diagnostic: %d trades train / %d holdout", len(train_split), len(holdout_split))
    params_diag = fit(train_split, layout, args.lambda_reg)
    train_rmse = rmse(params_diag, train_split, layout)
    holdout_rmse = rmse(params_diag, holdout_split, layout)
    log.info("résidu log-ratio — train RMSE: %.4f, holdout RMSE: %.4f", train_rmse, holdout_rmse)
    if holdout_rmse > 1.5 * train_rmse:
        log.warning(
            "holdout RMSE nettement > train RMSE (%.4f vs %.4f) — signe de surapprentissage, "
            "envisager d'augmenter --lambda-reg", holdout_rmse, train_rmse,
        )

    log.info("ajustement final sur les %d trades utilisables au complet", len(packed))
    params_full = fit(packed, layout, args.lambda_reg)
    full_rmse = rmse(params_full, packed, layout)
    log.info("résidu log-ratio — full-fit RMSE: %.4f", full_rmse)

    wb_full = unpack_params(params_full, layout)

    anchor_overall = 28.0  # fin de 1re ronde — repère plus lisible qu'un pick de 3e ronde
    anchor_feats = {
        "overall_estimate_log": math.log(anchor_overall),
        "years_out": 1.0,
        "is_conditional": 0.0,
    }
    anchor_std = to_std_vector("pick", anchor_feats, stand)
    w_pick, b_pick = wb_full["pick"]
    anchor_raw_value = math.exp(float(np.dot(w_pick, anchor_std)) + b_pick)

    print("\n=== Coefficients (par écart-type standardisé; exp(w) = facteur multiplicatif de valeur) ===")
    for fam in FAMILIES:
        w, b = wb_full[fam]
        print(f"\n-- {fam} (biais brut={b:.3f}) --")
        rows = sorted(zip(FAMILY_FEATURES[fam], w), key=lambda kv: -abs(kv[1]))
        for name, coef in rows:
            print(f"  {name:22s} w={coef:+.3f}  facteur/écart-type={math.exp(coef):.3f}x")

    print(f"\nAncre de normalisation: pick de fin de 1re ronde, ~28e au total, 1 an avant repêchage, non conditionnel = 1.0 unité")
    print(f"(valeur brute de l'ancre: {anchor_raw_value:.4f})")

    write_values_output(trades, wb_full, stand, anchor_raw_value)

    model_out = {
        "families": {
            fam: {
                "features": FAMILY_FEATURES[fam],
                "mean": stand[fam]["mean"].tolist(),
                "std": stand[fam]["std"].tolist(),
                "weight": wb_full[fam][0].tolist(),
                "bias": float(wb_full[fam][1]),
            }
            for fam in FAMILIES
        },
        "age_center": AGE_CENTER,
        "lambda_reg": args.lambda_reg,
        "anchor": {
            "description": "pick de fin de 1re ronde, ~28e au total, 1 an avant le repêchage, non conditionnel",
            "features": anchor_feats,
            "raw_value": anchor_raw_value,
        },
        "diagnostics": {
            **diag,
            "n_trades_train": len(train_split),
            "n_trades_holdout": len(holdout_split),
            "train_rmse": train_rmse,
            "holdout_rmse": holdout_rmse,
            "full_fit_rmse": full_rmse,
        },
    }
    MODEL_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MODEL_OUT_PATH.open("w") as f:
        json.dump(model_out, f, ensure_ascii=False, indent=2)
    log.info("modèle écrit -> %s", MODEL_OUT_PATH)


def write_values_output(trades: list[dict], wb_full: dict, stand: dict, anchor_raw_value: float) -> None:
    """Réassocie chaque élément featurisé à ses clés (trade_id/receives_key/element_index)
    et son type d'origine pour écrire values.jsonl. Refait la featurisation avec les clés
    conservées plutôt que de complexifier build_universe() avec un deuxième format de retour."""
    elements = load_jsonl(CLASSIFIED_PATH)
    stats_by_key = {key_of(r): r for r in load_jsonl(STATS_PATH)}
    bio_by_key = {key_of(r): r for r in load_jsonl(BIO_PATH)}
    picks_by_key = {key_of(r): r for r in load_jsonl(PICKS_PATH)}
    quali_by_key = {key_of(r): r for r in load_jsonl(VALUE_FEATURES_PATH)} if VALUE_FEATURES_PATH.exists() else {}
    usable_trade_ids = {t["trade_id"] for t in trades}

    by_trade: dict[int, list[dict]] = defaultdict(list)
    for e in elements:
        if e["trade_id"] in usable_trade_ids:
            by_trade[e["trade_id"]].append(e)

    rows = []
    for trade_id, elems in by_trade.items():
        trade_year = int(elems[0]["trade_date"][:4])
        for e in elems:
            t = e["element"]["type_classified"]
            k = key_of(e)
            if t in SKATER_TYPES and k in stats_by_key and k in bio_by_key:
                feats = featurize_skater(e, stats_by_key[k], bio_by_key[k], quali_by_key.get(k))
                family = "skater"
            elif t in GOALIE_TYPES and k in stats_by_key and k in bio_by_key:
                feats = featurize_goalie(e, stats_by_key[k], bio_by_key[k], quali_by_key.get(k))
                family = "goalie"
            elif t in PICK_TYPES and k in picks_by_key:
                feats = featurize_pick(picks_by_key[k], trade_year)
                family = "pick"
            else:
                continue
            if feats is None:
                continue
            x_std = to_std_vector(family, feats, stand)
            w, b = wb_full[family]
            raw_value = math.exp(float(np.dot(w, x_std)) + b)
            rows.append({
                "trade_id": trade_id,
                "receives_key": e["receives_key"],
                "element_index": e["element_index"],
                "type_classified": t,
                "family": family,
                "raw_value": raw_value,
                "normalized_value": raw_value / anchor_raw_value,
            })

    VALUES_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with VALUES_OUT_PATH.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    log.info("valeurs écrites -> %s (%d éléments)", VALUES_OUT_PATH, len(rows))


if __name__ == "__main__":
    main()
