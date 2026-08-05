#!/usr/bin/env python3
"""
Modèle de valeur d'échange latente — équivalence révélée, hiérarchique bayésien (issue 1op)

Remplace la V2 (régression ridge à estimation ponctuelle, scipy.optimize) par un
vrai modèle à variable latente : value(e) = exp(w_famille · standardize(x(e)) +
b_famille) reste la même forme fonctionnelle et la même contrainte d'équivalence
(log V_A ≈ log V_B sur les trades entièrement featurisables des deux côtés), mais
w_famille et b_famille sont maintenant des variables aléatoires avec distribution
postérieure (MCMC, PyMC/NUTS) plutôt qu'un point optimal unique. Deux bénéfices
directs, motivés par la nuit du 2026-08-04/05 (voir issue 1op pour le détail) :

1. POOLING PARTIEL. skater et goalie partagent neuf features (age_c, height_in,
   season_gp, career_gp_log, draft_overall_norm, undrafted, + les trois
   QUALI_FEATURES) mais étaient ajustés en vase clos en V2 — goalie (poignée
   d'éléments face à skater) n'avait aucune information de skater pour stabiliser
   ses coefficients. Ici chaque feature partagée a un hyper-prior commun
   (mu_f, sigma_f) dont skater ET goalie tirent leur coefficient : quand goalie
   a peu de signal propre, son coefficient se rétrécit vers l'estimé (mieux
   informé) de skater : pooling automatique, pas de recherche de sous-ensemble
   feature-par-feature à la main. pick n'a aucune feature en commun avec les deux
   autres familles — pas de hiérarchie à faire là, coefficients indépendants
   comme en V2.

2. INCERTITUDE PAR PRÉDICTION. value(e) est maintenant une distribution
   postérieure, pas un chiffre : chaque élément de values.jsonl porte une
   moyenne, un écart-type et un intervalle de crédibilité à 90%, calculés en
   appliquant CHAQUE tirage postérieur (mêmes tirages pour w, b ET l'ancre) à ses
   features — pas une heuristique de distance après-coup. Cas concret qui motive
   ça : Erik Karlsson (trade 4625) ressort premier à 3.18 en V2 sur la base d'une
   saison de 101 points, statistiquement rare pour un défenseur de 33 ans — un
   seul chiffre, aucun moyen d'exprimer que c'est une extrapolation risquée.

Les familles de features, la fonction featurize_*, la standardisation, l'univers
utilisable et l'ancrage de l'échelle sont inchangés depuis la V2 — voir kii pour
l'historique de sélection de features (recherche exhaustive de sous-ensembles,
476 trades ne supportent pas plus de 16 paramètres qualitatifs — QUALI_FEATURES
est le trio retenu). Ce script ne change QUE la méthode d'estimation.

Ce script dépend de PyMC/ArviZ, installés dans ~/myenv (pas dans l'environnement
du projet — dépendance lourde, isolée délibérément). Toujours l'exécuter avec :

  ~/myenv/bin/python pipelines/fit_trade_value.py

Lit  data/resolved/classified_elements.jsonl
     data/enriched/stats.jsonl
     data/enriched/bio.jsonl
     data/enriched/picks.jsonl
     data/enriched/value_features.jsonl
Écrit data/enriched/value_model.json  (résumés postérieurs, standardisation, ancre, diagnostics MCMC)
      data/enriched/values.jsonl      (valeur moyenne + incertitude par élément, univers utilisable seulement)

Usage:
  ~/myenv/bin/python pipelines/fit_trade_value.py
  ~/myenv/bin/python pipelines/fit_trade_value.py --draws 2000 --tune 2000 --chains 4 --seed 7
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

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

# Voir kii pour l'historique complet : ce trio de features qualitatives (extraites
# par extract_value_features.py) gagne sur 9/10 splits train/holdout indépendants
# contre le jeu complet de 8 (cap hit x2, statut FA, blessure, réserves, climat x2,
# unités spéciales), qui coûtait plus de variance qu'il n'apportait de signal à
# 476 trades sous estimation ponctuelle. Le pooling hiérarchique ici pourrait
# changer ce calcul (moins besoin de "protéger" chaque paramètre individuellement)
# mais on garde le même point de départ que la V2 pour isoler l'effet de la seule
# méthode d'estimation — retester le jeu de 8 est un suivi possible, pas fait ici.
QUALI_FEATURES = ["injury_ordinal", "contract_impasse_flag", "reservation_ordinal"]

SKATER_FEATURES = [
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

# Features présentes dans plus d'une famille : reçoivent un hyper-prior commun
# (mu_f, sigma_f) partagé entre les familles qui les utilisent — le mécanisme de
# pooling partiel. Calculé automatiquement plutôt que codé en dur pour rester
# cohérent si FAMILY_FEATURES change.
_feature_to_families: dict[str, list[str]] = defaultdict(list)
for _fam, _feats in FAMILY_FEATURES.items():
    for _f in _feats:
        _feature_to_families[_f].append(_fam)
SHARED_FEATURES = sorted(f for f, fams in _feature_to_families.items() if len(fams) > 1)
EXCLUSIVE_FEATURES = {
    fam: [f for f in FAMILY_FEATURES[fam] if f not in SHARED_FEATURES] for fam in FAMILIES
}


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
        "overall_estimate_log": math.log(overall_estimate),
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


# ---------------------------------------------------------------------------
# Assemblage vectorisé pour PyMC : chaque trade usable produit deux "côtés"
# (rangée dans un array plat de côtés). Chaque élément appartient à une famille
# et à un côté ; on construit, par famille, une matrice de design (n_elems_fam x
# n_features_fam) et une matrice d'appartenance côté (n_sides x n_elems_fam) à
# valeurs 0/1, pour que side_value_fam = M_fam @ exp(X_fam @ w_fam + b_fam) se
# fasse en une multiplication matricielle plutôt qu'une boucle Python par trade
# — indispensable pour que NUTS explore des milliers de fois sans ralentir.
# ---------------------------------------------------------------------------

def pack_for_model(trades: list[dict], stand: dict) -> dict:
    n_sides = 2 * len(trades)
    fam_rows: dict[str, list[np.ndarray]] = {fam: [] for fam in FAMILIES}
    fam_side_idx: dict[str, list[int]] = {fam: [] for fam in FAMILIES}

    for t_i, t in enumerate(trades):
        for side_offset, side in enumerate(t["sides"]):
            side_idx = 2 * t_i + side_offset
            for family, feats in side:
                fam_rows[family].append(to_std_vector(family, feats, stand))
                fam_side_idx[family].append(side_idx)

    packed = {"n_sides": n_sides, "n_trades": len(trades)}
    for fam in FAMILIES:
        n_feats = len(FAMILY_FEATURES[fam])
        X = np.array(fam_rows[fam]) if fam_rows[fam] else np.zeros((0, n_feats))
        idx = np.array(fam_side_idx[fam], dtype=int)
        M = np.zeros((n_sides, len(idx)))
        M[idx, np.arange(len(idx))] = 1.0
        packed[fam] = {"X": X, "M": M}
    return packed


def build_pymc_model(packed: dict):
    import pymc as pm
    import pytensor.tensor as pt

    n_trades = packed["n_trades"]
    coords = {fam: FAMILY_FEATURES[fam] for fam in FAMILIES}
    coords["shared_feature"] = SHARED_FEATURES
    coords["trade"] = np.arange(n_trades)

    with pm.Model(coords=coords) as model:
        # Hyper-priors pour les features partagées entre >=2 familles : le
        # mécanisme de pooling partiel. mu_f = effet "générique" de la feature,
        # sigma_f = à quel point les familles peuvent s'en écarter (petit sigma
        # -> pooling fort -> goalie hérite presque du coefficient de skater ;
        # grand sigma -> chaque famille garde son propre signal si les données
        # le justifient).
        mu_shared = pm.Normal("mu_shared", mu=0.0, sigma=1.0, dims="shared_feature")
        sigma_shared = pm.HalfNormal("sigma_shared", sigma=0.5, dims="shared_feature")

        w_by_family = {}
        b_by_family = {}
        for fam in FAMILIES:
            feats = FAMILY_FEATURES[fam]
            n_feats = len(feats)
            w_slices = []
            for f in feats:
                if f in SHARED_FEATURES:
                    j = SHARED_FEATURES.index(f)
                    offset = pm.Normal(f"z_{fam}_{f}", mu=0.0, sigma=1.0)
                    w_slices.append(mu_shared[j] + sigma_shared[j] * offset)
                else:
                    w_slices.append(pm.Normal(f"w_{fam}_{f}", mu=0.0, sigma=1.0))
            w_by_family[fam] = pt.stack(w_slices) if n_feats else pt.zeros((0,))
            b_by_family[fam] = pm.Normal(f"b_{fam}", mu=0.0, sigma=2.0)

        side_value = pt.zeros(packed["n_sides"])
        for fam in FAMILIES:
            X, M = packed[fam]["X"], packed[fam]["M"]
            if X.shape[0] == 0:
                continue
            elem_value = pt.exp(pt.dot(X, w_by_family[fam]) + b_by_family[fam])
            side_value = side_value + pt.dot(M, elem_value)

        log_side = pt.log(side_value)
        log_side_a = log_side[0::2]
        log_side_b = log_side[1::2]
        residual = pm.Deterministic("residual", log_side_a - log_side_b, dims="trade")

        sigma_obs = pm.HalfNormal("sigma_obs", sigma=1.0)
        pm.Normal("resid_obs", mu=residual, sigma=sigma_obs, observed=np.zeros(n_trades), dims="trade")

    return model, w_by_family, b_by_family


def sample_model(model, draws: int, tune: int, chains: int, seed: int):
    import pymc as pm

    # PyTensor ne trouve pas de BLAS système à lier (numpy en a un via
    # scipy-openblas64, mais dans un chemin pip privé que le compilateur C de
    # PyTensor ne détecte pas) — le sampler NUTS par défaut de PyMC (compilé en
    # C) tombe alors sur des produits matriciels non vectorisés, catastrophique
    # (200 tirages n'ont pas terminé en 10+ minutes lors du smoke test). nutpie
    # compile le graphe via Numba (JIT, indépendant de la liaison BLAS système)
    # et implémente NUTS en Rust — évite complètement le problème.
    with model:
        idata = pm.sample(
            draws=draws, tune=tune, chains=chains, cores=chains,
            random_seed=seed, target_accept=0.9, progressbar=True,
            nuts_sampler="nutpie",
        )
    return idata


def posterior_diagnostics(idata) -> dict:
    import arviz as az

    summary = az.summary(idata, var_names=["mu_shared", "sigma_shared", "sigma_obs"] + [
        v for v in idata.posterior.data_vars if v.startswith("w_") or v.startswith("b_") or v.startswith("z_")
    ])
    n_divergent = int(idata.sample_stats["diverging"].sum())
    return {
        "max_rhat": float(summary["r_hat"].max()),
        "min_ess_bulk": float(summary["ess_bulk"].min()),
        "n_divergences": n_divergent,
    }


def posterior_family_weights(idata, fam: str) -> np.ndarray:
    """(n_draws_total, n_features) tirages postérieurs aplatis chaînes+draws pour
    les poids d'une famille, dans l'ordre de FAMILY_FEATURES[fam]."""
    post = idata.posterior
    n_shared = {f: i for i, f in enumerate(SHARED_FEATURES)}
    mu_shared = post["mu_shared"].values  # (chain, draw, shared_feature)
    sigma_shared = post["sigma_shared"].values
    n_chain, n_draw = mu_shared.shape[0], mu_shared.shape[1]
    n_total = n_chain * n_draw

    cols = []
    for f in FAMILY_FEATURES[fam]:
        if f in n_shared:
            j = n_shared[f]
            z = post[f"z_{fam}_{f}"].values.reshape(n_total)
            mu = mu_shared[:, :, j].reshape(n_total)
            sigma = sigma_shared[:, :, j].reshape(n_total)
            cols.append(mu + sigma * z)
        else:
            cols.append(post[f"w_{fam}_{f}"].values.reshape(n_total))
    return np.stack(cols, axis=1)


def posterior_family_bias(idata, fam: str) -> np.ndarray:
    post = idata.posterior
    b = post[f"b_{fam}"].values
    return b.reshape(b.shape[0] * b.shape[1])


def rmse_at_point(packed: dict, w_point: dict, b_point: dict) -> float:
    side_value = np.zeros(packed["n_sides"])
    for fam in FAMILIES:
        X, M = packed[fam]["X"], packed[fam]["M"]
        if X.shape[0] == 0:
            continue
        elem_value = np.exp(X @ w_point[fam] + b_point[fam])
        side_value += M @ elem_value
    log_side = np.log(side_value)
    residual = log_side[0::2] - log_side[1::2]
    return float(np.sqrt(np.mean(residual ** 2)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--draws", type=int, default=1500)
    parser.add_argument("--tune", type=int, default=1500)
    parser.add_argument("--chains", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--holdout-frac", type=float, default=0.15)
    parser.add_argument(
        "--ci", type=float, default=0.90,
        help="largeur de l'intervalle de crédibilité rapporté dans values.jsonl (défaut 90%%)",
    )
    args = parser.parse_args()

    trades, diag = build_universe()
    log.info(
        "univers: %d/%d trades utilisables (deux côtés featurisables); éléments exclus par type: %s",
        diag["n_trades_usable"], diag["n_trades_total"], diag["skipped_element_types"],
    )
    if len(trades) < 30:
        log.error("trop peu de trades utilisables (%d) pour ajuster quoi que ce soit de fiable", len(trades))
        return

    stand = standardize_families(trades)

    rng = np.random.default_rng(args.seed)
    order = rng.permutation(len(trades))
    n_holdout = max(1, int(len(trades) * args.holdout_frac))
    holdout_idx = set(order[:n_holdout].tolist())
    train_trades = [trades[i] for i in range(len(trades)) if i not in holdout_idx]
    holdout_trades = [trades[i] for i in range(len(trades)) if i in holdout_idx]

    log.info("ajustement diagnostic (MCMC): %d trades train / %d holdout", len(train_trades), len(holdout_trades))
    packed_train = pack_for_model(train_trades, stand)
    model_train, w_train, b_train = build_pymc_model(packed_train)
    idata_train = sample_model(model_train, args.draws, args.tune, args.chains, args.seed)
    diag_stats = posterior_diagnostics(idata_train)
    log.info(
        "diagnostic MCMC (train): max r_hat=%.3f, min ess_bulk=%.0f, divergences=%d",
        diag_stats["max_rhat"], diag_stats["min_ess_bulk"], diag_stats["n_divergences"],
    )

    w_point_train = {fam: posterior_family_weights(idata_train, fam).mean(axis=0) for fam in FAMILIES}
    b_point_train = {fam: posterior_family_bias(idata_train, fam).mean() for fam in FAMILIES}
    train_rmse = rmse_at_point(packed_train, w_point_train, b_point_train)
    packed_holdout = pack_for_model(holdout_trades, stand)
    holdout_rmse = rmse_at_point(packed_holdout, w_point_train, b_point_train)
    log.info("résidu log-ratio (moyenne postérieure) — train RMSE: %.4f, holdout RMSE: %.4f", train_rmse, holdout_rmse)
    if holdout_rmse > 1.5 * train_rmse:
        log.warning(
            "holdout RMSE nettement > train RMSE (%.4f vs %.4f) — signe de surapprentissage malgré le pooling",
            holdout_rmse, train_rmse,
        )

    log.info("ajustement final (MCMC) sur les %d trades utilisables au complet", len(trades))
    packed_full = pack_for_model(trades, stand)
    model_full, w_full, b_full = build_pymc_model(packed_full)
    idata_full = sample_model(model_full, args.draws, args.tune, args.chains, args.seed + 1)
    diag_stats_full = posterior_diagnostics(idata_full)
    log.info(
        "diagnostic MCMC (full): max r_hat=%.3f, min ess_bulk=%.0f, divergences=%d",
        diag_stats_full["max_rhat"], diag_stats_full["min_ess_bulk"], diag_stats_full["n_divergences"],
    )

    w_draws = {fam: posterior_family_weights(idata_full, fam) for fam in FAMILIES}  # (n_draws, n_feats)
    b_draws = {fam: posterior_family_bias(idata_full, fam) for fam in FAMILIES}  # (n_draws,)
    w_mean = {fam: w_draws[fam].mean(axis=0) for fam in FAMILIES}
    w_sd = {fam: w_draws[fam].std(axis=0) for fam in FAMILIES}
    b_mean = {fam: float(b_draws[fam].mean()) for fam in FAMILIES}
    b_sd = {fam: float(b_draws[fam].std()) for fam in FAMILIES}
    full_rmse = rmse_at_point(packed_full, w_mean, b_mean)
    log.info("résidu log-ratio (moyenne postérieure) — full-fit RMSE: %.4f", full_rmse)

    anchor_overall = 28.0  # fin de 1re ronde — repère plus lisible qu'un pick de 3e ronde
    anchor_feats = {"overall_estimate_log": math.log(anchor_overall), "years_out": 1.0, "is_conditional": 0.0}
    anchor_std = to_std_vector("pick", anchor_feats, stand)
    anchor_draws = np.exp(w_draws["pick"] @ anchor_std + b_draws["pick"])  # (n_draws,)
    anchor_mean = float(anchor_draws.mean())

    print("\n=== Coefficients (moyenne postérieure ± écart-type; par écart-type standardisé) ===")
    for fam in FAMILIES:
        print(f"\n-- {fam} (biais={b_mean[fam]:+.3f} ± {b_sd[fam]:.3f}) --")
        rows = sorted(zip(FAMILY_FEATURES[fam], w_mean[fam], w_sd[fam]), key=lambda kv: -abs(kv[1]))
        for name, mean, sd in rows:
            shared_tag = " [pooled]" if name in SHARED_FEATURES else ""
            print(f"  {name:22s} w={mean:+.3f} ± {sd:.3f}  facteur/écart-type={math.exp(mean):.3f}x{shared_tag}")

    print("\n-- hyper-priors (features partagées skater/goalie, pooling partiel) --")
    mu_shared_draws = idata_full.posterior["mu_shared"].values.reshape(-1, len(SHARED_FEATURES))
    sigma_shared_draws = idata_full.posterior["sigma_shared"].values.reshape(-1, len(SHARED_FEATURES))
    for j, f in enumerate(SHARED_FEATURES):
        print(f"  {f:22s} mu={mu_shared_draws[:, j].mean():+.3f}  sigma={sigma_shared_draws[:, j].mean():.3f}")

    print(f"\nAncre de normalisation: pick de fin de 1re ronde, ~28e au total, 1 an avant repêchage, non conditionnel = 1.0 unité")
    print(f"(valeur brute moyenne de l'ancre: {anchor_mean:.4f})")

    write_values_output(trades, w_draws, b_draws, anchor_draws, stand, args.ci)

    model_out = {
        "families": {
            fam: {
                "features": FAMILY_FEATURES[fam],
                "mean": stand[fam]["mean"].tolist(),
                "std": stand[fam]["std"].tolist(),
                "weight_mean": w_mean[fam].tolist(),
                "weight_sd": w_sd[fam].tolist(),
                "bias_mean": b_mean[fam],
                "bias_sd": b_sd[fam],
            }
            for fam in FAMILIES
        },
        "shared_features": {
            f: {
                "mu_mean": float(mu_shared_draws[:, j].mean()),
                "mu_sd": float(mu_shared_draws[:, j].std()),
                "sigma_mean": float(sigma_shared_draws[:, j].mean()),
            }
            for j, f in enumerate(SHARED_FEATURES)
        },
        "age_center": AGE_CENTER,
        "anchor": {
            "description": "pick de fin de 1re ronde, ~28e au total, 1 an avant le repêchage, non conditionnel",
            "features": anchor_feats,
            "raw_value_mean": anchor_mean,
            "raw_value_sd": float(anchor_draws.std()),
        },
        "mcmc": {
            "draws": args.draws, "tune": args.tune, "chains": args.chains, "seed": args.seed,
        },
        "diagnostics": {
            **diag,
            "n_trades_train": len(train_trades),
            "n_trades_holdout": len(holdout_trades),
            "train_rmse": train_rmse,
            "holdout_rmse": holdout_rmse,
            "full_fit_rmse": full_rmse,
            "mcmc_train": diag_stats,
            "mcmc_full": diag_stats_full,
            "sigma_obs_mean": float(idata_full.posterior["sigma_obs"].values.mean()),
        },
    }
    MODEL_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MODEL_OUT_PATH.open("w") as f:
        json.dump(model_out, f, ensure_ascii=False, indent=2)
    log.info("modèle écrit -> %s", MODEL_OUT_PATH)


def write_values_output(
    trades: list[dict], w_draws: dict, b_draws: dict, anchor_draws: np.ndarray, stand: dict, ci: float,
) -> None:
    """Réassocie chaque élément featurisé à ses clés (trade_id/receives_key/element_index)
    et son type d'origine pour écrire values.jsonl. Chaque tirage postérieur (même
    indexation que anchor_draws, pour que la normalisation préserve la corrélation
    tirage-par-tirage plutôt que de diviser par une moyenne) donne une valeur brute ;
    on rapporte moyenne, écart-type et intervalle de crédibilité de la valeur normalisée."""
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

    lo_q, hi_q = (1 - ci) / 2, 1 - (1 - ci) / 2
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
            raw_draws = np.exp(w_draws[family] @ x_std + b_draws[family])  # (n_draws,)
            normalized_draws = raw_draws / anchor_draws
            rows.append({
                "trade_id": trade_id,
                "receives_key": e["receives_key"],
                "element_index": e["element_index"],
                "type_classified": t,
                "family": family,
                "raw_value_mean": float(raw_draws.mean()),
                "normalized_value_mean": float(normalized_draws.mean()),
                "normalized_value_sd": float(normalized_draws.std()),
                "normalized_value_ci_low": float(np.quantile(normalized_draws, lo_q)),
                "normalized_value_ci_high": float(np.quantile(normalized_draws, hi_q)),
                "ci_width": ci,
            })

    VALUES_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with VALUES_OUT_PATH.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    log.info("valeurs écrites -> %s (%d éléments)", VALUES_OUT_PATH, len(rows))


if __name__ == "__main__":
    main()
