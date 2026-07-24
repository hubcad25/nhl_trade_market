# Structure cible — NHL Skater (champs concrets)

Objectif: lister les **champs explicites** qu'on veut dans l'objet NHL skater.

Principe: on stocke ce qui est observable/collectable. On évite les catégories abstraites (ex: "type de joueur", "intangibles") sauf si c'est du texte extrait d'article/recherche web.

## 1) Identité et contexte du trade

- `trade_id`
- `trade_date`
- `player_name`
- `current_team`
- `from_team`
- `to_team`
- `position` (C/LW/RW/D)
- `shoots` (L/R)
- `age_at_trade`

## 2) Statut contractuel

- `cap_hit`
- `salary_remaining_at_trade` (si dispo)
- `contract_years_remaining`
- `contract_expiry_season`
- `contract_status_at_trade` (UFA/RFA/signed_term)
- `retained_salary_pct` (0 si aucune rétention)
- `clauses` (NMC/NTC/etc. si dispo)

## 3) Usage et rôle mesurable

- `games_played_season_to_trade`
- `avg_toi`
- `avg_toi_even_strength`
- `avg_toi_power_play`
- `avg_toi_penalty_kill`
- `special_teams_usage` (PP1/PP2/PK yes/no si dispo)
- `faceoff_pct` (si C et dispo)

## 4) Production de base (saison en cours jusqu'au trade)

- `goals`
- `assists`
- `points`
- `points_per_game`
- `shots`
- `shooting_pct`
- `plus_minus` (si on décide de le garder)

### Fenêtre récente (optionnelle mais utile)

Idée: rester simple et factuel avec les stats de base sur une courte fenêtre avant le trade.

- `recent_window_games` (ex: 10 ou 20)
- `recent_goals`
- `recent_assists`
- `recent_points`
- `recent_points_per_game`
- `recent_avg_toi`

## 5) Indicateurs avancés (si disponibles)

- `ixg` ou équivalent xG individuel
- `on_ice_xgf_pct` (ou métrique possession/impact équivalente)
- `cf_pct` (ou équivalent)
- `high_danger_chances_for_pct` (si dispo)
- `penalties_drawn`
- `penalties_taken`

Note: le set exact de stats avancées peut varier selon la saison/source. L'objet doit accepter des champs manquants.

## 6) Physique et robustesse

- `height`
- `weight`
- `hits`
- `blocked_shots`

## 7) Santé / disponibilité

- `injury_status_at_trade` (healthy/day_to_day/IR/LTIR/unknown)
- `games_missed_recent` (si dispo)

## 8) Qualitatif textuel (seulement observé)

- `qualitative_summary` (résumé neutre en 2-5 lignes)
- `qualitative_signals` (liste courte de signaux trouvés dans article/recherche web)
- `qualitative_source_type` (tsn_article/rss/web)
- `qualitative_source_date`
- `qualitative_source_url` (si dispo)
- `qualitative_extraction_method` (rule_based/llm_distilled)

Important: ce bloc est **extrait de texte existant**, pas inventé.

## 9) Contexte marché minimal

- `market_window` (deadline/offseason/other)
- `seller_team_context` (seller/buyer/neutral/unknown)
- `seller_team_context_confidence` (high/medium/low)

## 10) Champs dérivés (hors objet brut)

Les champs dérivés pour simplifier les prompts (ex: buckets) ne font pas partie de l'objet brut NHL skater.
Ils seront calculés au moment de construire le prompt training/inference, dans une couche séparée.

## Version compacte attendue pour training prompt

Minimum utile:

- Position + âge
- Statut contractuel + cap hit + rétention
- GP/G/A/PTS/PTS-GP + TOI
- 2-4 stats d'impact (si dispo)
- 1 bloc qualitatif court provenant de source texte
- Contexte marché (deadline/offseason, seller/buyer)

---

Ce document définit le **contenu cible** NHL skater.
Le format technique final (types stricts, champs obligatoires, nullables) sera défini dans les schemas formels.
