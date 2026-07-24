# Plan — Pipeline d'acquisition de données (Étapes 1-3)

## Principes généraux

- Scripts Python simples + CLI, un script par étape
- Toutes les saisons disponibles dans le TSN Trade Tracker (le plus loin possible)
- Snapshot temporel : stats au moment du trade (saison en cours à la date du trade)
- Les vieux échanges auront moins d'info — c'est acceptable, le LLM gère l'hétérogénéité
- Résolution des IDs : par source, uniquement pour les sources nécessaires au type de l'élément

---

## Étape A — Scrape TSN Trade Tracker

### A1 — Recherche : schema TSN API (à faire en premier)
- Faire des requêtes manuelles sur plusieurs saisons
- Documenter le schema complet : structure d'un trade, champs joueur, pick, équipes, date, article URL
- Identifier les variations de schema selon les saisons
- Output : documentation du schema dans `docs/tsn_schema.md`

### A2 — Scraper TSN + stocker raw JSON
- Script : `pipelines/scrape_tsn.py`
- Scraper toutes les saisons disponibles
- Output : `data/raw/tsn/{season}.json`
- Gestion pagination et retry

---

## Étape B — Normalisation & Classification des éléments de trade

### B1 — Normaliser les trades
- Script : `pipelines/normalize_trades.py`
- Parser le raw TSN JSON
- Extraire chaque élément de trade dans un format canonique avec type inféré
- Output : `data/normalized/trades.jsonl`

### B2 — Classifier les éléments par type
- Logique de classification intégrée dans B1 ou module dédié
- NHL Skater vs. NHL Goalie : via position (NHL API)
- NHL player vs. Prospect : via présence d'un NHL ID + critères âge/ligue
- Pick : trivial depuis TSN
- Heuristiques à affiner après A1 (schema discovery)

---

## Étape C — Résolution des IDs par source

Chaque joueur est mappé aux IDs des sources nécessaires SELON SON TYPE uniquement.

### C1 — Résolution NHL API ID (NHL skaters et goalies)
- Investiguer l'API NHL pour le lookup de joueurs par nom
- Fuzzy matching sur le nom
- Fallback : LLM local avec contexte (équipe, saison, position)
- Output : mapping `tsn_name → nhl_api_id`

### C2 — Résolution MoneyPuck (NHL skaters et goalies)
- Déterminer comment MoneyPuck identifie les joueurs (nom? nhl_api_id?)
- Construire le mapping nécessaire (probablement direct après C1)

### C3 — Recherche : source de données de contrats
- Options à investiguer : PuckPedia (scraping web), CapFriendly archive, NHL API, autre
- Critères : historique des contrats au moment du trade, cap hit, statut UFA/RFA, NMC/NTC
- Output : décision documentée sur la source choisie

### C4 — Recherche : source de données pour les prospects
- Options à investiguer : EliteProspects (API/scraping), recherche web RSS, autre
- Critères : stats par saison dans leur ligue, ranking, scouting report
- Pour les anciens trades : données les plus proches de la date du trade
- Output : décision documentée sur la source choisie

### C5 — Table de mapping globale
- Script : construit et maintient `data/resolved/player_id_map.json`
- Pour chaque joueur : ses IDs par source selon son trade element type
- Dépend de C1 et C2 (et C3/C4 une fois les sources décidées)

---

## Étape F — Schemas JSON des trade elements

### F1 — Définir les schemas formels
- À faire après A1 (on connaît la structure TSN) et en parallèle de C3/C4
- Un schema par type d'élément : NHL Skater, NHL Goalie, Skater Prospect, Goalie Prospect, Pick
- Les schemas définissent les champs enrichis attendus (pas le raw TSN)
- Output : `docs/schemas/` ou dans le code comme dataclasses/Pydantic models

---

## Dépendances entre étapes

```
A1 (schema discovery)
├── A2 (scraper)
│   └── B1 (normalizer)
│       └── B2 (classifier)
│           ├── C1 (nhl api id resolver)
│           │   └── C2 (moneypuck resolver)
│           │       └── C5 (id mapping table)
│           └── [C3 + C4 débloquent C5 aussi]
├── F1 (schemas JSON)
C3 (contract source research)  ← parallèle à A/B
C4 (prospect source research)  ← parallèle à A/B
```

**A1 est le premier travail à faire — débloque tout.**  
**C3 et C4 peuvent démarrer en parallèle dès maintenant.**

---

## Structure de répertoires cible

```
pipelines/
  scrape_tsn.py
  normalize_trades.py
  sources/
    nhl_api.py
    moneypuck.py
    contracts.py       (source TBD après C3)
    prospects.py       (source TBD après C4)
    rss.py
  enrich_skater_nhl.py
  enrich_skater_prospect.py
  enrich_goalie_nhl.py
  enrich_goalie_prospect.py
  enrich_pick.py
data/
  raw/tsn/             (raw JSON par saison)
  normalized/          (trades.jsonl)
  resolved/            (player_id_map.json)
  enriched/            (éléments enrichis par trade)
  training/            (paires prompt/output finales)
docs/
  tsn_schema.md        (output de A1)
  schemas/             (schemas JSON par type — output de F1)
```
