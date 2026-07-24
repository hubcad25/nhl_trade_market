# Structure cible — Skater Prospect (champs concrets)

Objectif: lister les champs explicites qu'on veut dans un objet prospect patineur impliqué dans un trade.

Principe: rester facts-first. On stocke ce qui est observable/collectable, plus un résumé textuel provenant de sources existantes.

## 1) Identité et contexte du trade

- `trade_id`
- `trade_date`
- `player_name`
- `from_team`
- `to_team`
- `is_prospect` (true)

## 2) Profil de base du prospect

- `position` (C/LW/RW/D, si dispo)
- `shoots` (L/R, si dispo)
- `age_at_trade`
- `birth_year`
- `nationality` (si dispo)
- `height` (si dispo)
- `weight` (si dispo)

## 3) Statut de développement

- `development_stage` (junior/college/europe/pro/ahl/unknown)
- `current_team_at_trade`
- `current_league_at_trade`
- `drafted` (bool)
- `draft_year` (si drafté)
- `draft_round` (si drafté)
- `draft_overall` (si drafté)
- `nhl_rights_holder_at_trade` (si applicable)

## 4) Production de base (saison en cours à la date du trade)

- `games_played_season_to_trade`
- `goals`
- `assists`
- `points`
- `points_per_game`
- `penalty_minutes` (si dispo)

## 5) Fenêtre récente (optionnelle)

Rester simple avec stats de base sur un segment court avant le trade.

- `recent_window_games` (ex: 10)
- `recent_goals`
- `recent_assists`
- `recent_points`
- `recent_points_per_game`

## 6) Signaux de valeur prospect (observables)

- `ranking_source` (nom de la source, si dispo)
- `ranking_value` (si dispo)
- `ranking_date` (si dispo)
- `tier_label` (A/B/C ou équivalent, seulement si explicitement présent dans la source)

Note: on n'invente pas de ranking. Si absent, champ nul.

## 7) Parcours et disponibilité

- `seasons_in_current_league` (si dispo)
- `injury_status_at_trade` (healthy/day_to_day/IR/unknown)
- `games_missed_recent` (si dispo)

## 8) Qualitatif textuel (seulement observé)

- `qualitative_summary` (résumé neutre court)
- `qualitative_signals` (liste de signaux trouvés dans article/recherche web)
- `qualitative_source_type` (tsn_article/rss/web)
- `qualitative_source_date`
- `qualitative_source_url` (si dispo)
- `qualitative_extraction_method` (rule_based/llm_distilled)

Important: ce bloc est extrait de texte existant, pas inventé.

## 9) Contexte marché minimal

- `market_window` (deadline/offseason/other)
- `seller_team_context` (seller/buyer/neutral/unknown)
- `seller_team_context_confidence` (high/medium/low)

## 10) Champs dérivés (hors objet brut)

Les champs dérivés pour simplifier les prompts (buckets, score interne, etc.) ne font pas partie de l'objet brut prospect.
Ils seront calculés dans une couche séparée de prompt building.

## Version compacte attendue pour training prompt

Minimum utile:

- Âge + position + profil physique (si dispo)
- Statut de développement (ligue/équipe, draft info)
- GP/G/A/PTS/PTS-GP
- Ranking explicite si présent
- Bloc qualitatif court provenant d'une source texte
- Contexte marché (deadline/offseason, seller/buyer)

---

Ce document définit le contenu cible skater prospect.
Le format technique final (types stricts, champs obligatoires, nullables) sera défini dans les schemas formels.
