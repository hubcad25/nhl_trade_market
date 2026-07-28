# Plan de travail — étapes 5 à 7

Les étapes 1 à 4 (scrape TSN, normalisation, résolution d'IDs, classification) sont
faites et décrites dans le README. Ce document couvre la partie non construite :
la recherche par joueur, l'extraction du profil, et l'enrichissement déterministe.

L'ancien plan — une chaîne de sources par type d'élément (MoneyPuck, PuckPedia,
EliteProspects, DobberProspects) — est sur `archive/tavily-pipeline-2026-07-28`.

---

## Étape 5 — Agent de recherche

Script : `pipelines/research_player.py`

Une exécution d'agent par paire (joueur, date de trade). L'agent cherche lui-même
sur le web et produit un portrait en prose du joueur tel qu'il était perçu à la date
du trade, avec ses sources et leur date de publication.

### Cache

Par `(trade_id, element_index)`, sur disque, dans `data/raw/briefs/`. Même logique
que le cache Tavily précédent : une exécution interrompue ne coûte rien à reprendre.
C'est ce qui rend une passe complète sur 727 éléments réexécutable sans risque.

### Contexte fourni à l'agent

- nom du joueur, date du trade
- équipes impliquées
- âge, position, matchs NHL avant le trade (depuis `classified_elements.jsonl`)

L'agent a besoin du contexte du trade pour trouver les bons articles. On le lui donne
et on lui interdit de le restituer. Le matériel contaminé reste confiné dans `raw/`.

### Prompt (à itérer)

> Recherche sur le web et produis un portrait de **{joueur}** tel qu'il était perçu
> au **{date}**, au moment où il a été échangé.
>
> Écris comme si tu rédigeais la veille de l'échange, sans aucune connaissance de ce
> qui s'est passé depuis. N'évalue pas l'échange, ne mentionne pas ce qu'il a rapporté
> ni qui est allé dans l'autre sens, et n'utilise jamais la carrière ultérieure du
> joueur — ni explicitement, ni pour choisir quoi mettre en avant.
>
> Couvre : statut et âge, rang de repêchage, niveau de jeu et production récente,
> forces reconnues, réserves des recruteurs, projection consensuelle, situation
> contractuelle, santé.
>
> Cite tes sources avec leur date de publication. Si l'information est mince, dis-le
> explicitement plutôt que de combler.

Deux clauses portent le poids : « ni pour choisir quoi mettre en avant » contre le
rétro-cadrage implicite, et « dis-le explicitement plutôt que de combler » pour
distinguer un joueur sans couverture d'une hallucination de remplissage.

### Pilote — à faire avant toute passe complète

5 à 10 éléments couvrant les trois stades de carrière. Mesurer le nombre de
recherches et les tokens réels par élément, donc le coût total à multiplier par 727.

Cas de test déjà validés manuellement :

| Joueur | Date | Ce que le cas teste |
|---|---|---|
| Kyle Criscuolo | 2023-01-18 | vétéran AHL anonyme, échange 1-contre-1 sans couverture |
| Nils Juntorp | 2025-01-26 | pièce accessoire d'un blockbuster, couverture entièrement sur Rantanen |
| Graham Sward | 2024-03-07 | cas intermédiaire, défenseur WHL dans un échange de second plan |

Reste à couvrir dans le pilote : un joueur NHL établi, et un prospect d'élite pré-pro.

---

## Étape 6 — Extraction du profil

Script : `pipelines/extract_profile.py`

Un appel API simple par élément, sans boucle d'agent. Entrée : le brief. Sortie :
le profil destiné au dataset d'entraînement.

Responsabilités :

- appliquer les règles anti-fuite (retour, contrepartie, évaluation de l'échange)
- retirer le nom du joueur — nom complet, prénom seul, nom de famille seul, surnoms
- produire une sortie structurée validée contre un schéma

Le retrait du nom se fait **après** génération, de façon déterministe et vérifiable.
Demander à l'agent d'écrire sans jamais nommer le joueur dégrade la prose.

La version précédente de ce script, écrite contre du texte d'article Tavily, est sur
la branche d'archive :

```bash
git show archive/tavily-pipeline-2026-07-28:pipelines/extract_qualitative.py
```

Son schéma JSON, son system prompt et sa fonction `validate()` sont réutilisables tels
quels. Seule l'entrée change.

**Note sur `validate()`** : sa règle `PICK_VOCABULARY` marque comme fuite des phrases
légitimes — « he was a first-round talent » décrit le pedigree du joueur, pas ce qu'il
a rapporté. À restreindre aux verbes de transaction (`returned`, `in exchange for`,
`sent`) plutôt qu'au vocabulaire de repêchage seul.

---

## Étape 7 — Enrichissement déterministe

Trois choses qui ne doivent jamais venir de l'agent.

### 7a — Stats coupées à la date du trade

Depuis le game log NHL. La logique existe déjà, inlinée dans `classify_elements.py`
(`/player/{id}/game-log/{season}/2`) — à extraire dans un module réutilisable.

Double usage : ce sont les stats du prompt, **et** un détecteur d'hallucination.
Quand l'agent avance des chiffres dans son brief, on les compare aux vrais. L'écart
mesure la fiabilité de l'agent sur l'ensemble du dataset, gratuitement.

### 7b — Tiers de picks

456 éléments, 37 % du dataset, aucun code à ce jour. Requiert le classement NHL à la
date du trade pour l'équipe d'origine du choix.

Tiers : lottery (top ~10) / mid-1st (11-20) / late-1st (21-32) / 2e ronde / 3e ronde+

### 7c — Le JSON de sortie

La cible, construite depuis `data/normalized/trades.jsonl`. Jamais générée.

---

## Ordre de travail

```
5 (agent + pilote)  ──┐
                      ├──> 8 (assemblage du dataset)
7b (tiers de picks) ──┤
7a (stats)          ──┘
        │
        └──> 6 (extraction) dépend de 5
```

Le pilote de l'étape 5 est bloquant : son coût décide si l'architecture tient à
727 éléments. Les tiers de picks (7b) n'en dépendent pas et peuvent avancer en
parallèle.

Hors chemin critique mais sensible au temps : la demande de quota GPU Azure. La
réponse prend des jours et peut être négative, et les crédits expirent en octobre 2026.

---

## Questions ouvertes

- **Financement de l'agent.** Les crédits Azure paient le fine-tuning. Ils n'atteignent
  l'agent que via Claude sur Microsoft Foundry, facturé par le Marketplace Azure — que
  beaucoup de programmes de crédits excluent. À vérifier dans les termes de la subvention.
- **Les 11 prospects sans `nhl_id`.** Sans identifiant, pas de stats déterministes.
  L'agent peut-il produire un profil utilisable malgré tout, ou faut-il les résoudre
  à la main dans `name_overrides.json` ?
- **Les 3 éléments `unresolved`** de la classification.
- **Sort des caches Tavily** (`data/raw/search/`, `data/raw/articles/`) une fois
  l'approche agent validée.
