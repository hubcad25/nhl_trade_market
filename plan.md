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

### Mécanisme — API Responses Azure avec recherche web hébergée

**Validé le 2026-07-28** : réponse 200 avec les URLs sources en annotations.

```
POST https://info-4552-resource.cognitiveservices.azure.com/openai/v1/responses?api-version=preview
header  api-key: <clé du compte info-4552-resource>
body    {"model": "gpt-5.5", "input": <prompt>, "tools": [{"type": "web_search"}]}
```

Le modèle **boucle côté serveur**. Les blocs de sortie sont `reasoning`,
`web_search_call`, `message` ; les URLs des sources sont dans les `annotations`
du bloc `message`. Il n'y a donc **ni boucle `while tool_calls` à écrire, ni outil
de recherche à fournir, ni récupération de pages** — Microsoft exécute tout.

Facturé comme usage Azure OpenAI, donc payé par les crédits Opubliq.

Ce que ça remplace : Tavily, Serper, un module de recherche maison et son cache de
requêtes. Aucun n'est nécessaire.

**Pourquoi pas Grounding with Bing.** C'est le chemin « officiel » côté Foundry
Agent Service, et il est **bloqué** sur cet abonnement : `SkuNotEligible` sur le SKU
G1, parce que le `quotaId` est `Sponsored_2016-01-01` — les ressources Bing sont
interdites sur les abonnements sponsorisés. L'outil `web_search` de l'API Responses
passe par Azure OpenAI, un chemin distinct qui, lui, fonctionne.

**Pourquoi pas le Foundry Agent Service non plus.** Il apporte une boucle hébergée,
des threads et de la persistance. La boucle est déjà côté serveur ici, et la
persistance c'est notre cache disque. Il ne resterait qu'une ressource Azure de plus
à gérer.

Réserve : `api-version=preview` est une surface instable. D'où la décision de faire
la passe complète en une fois plutôt que de l'étaler.

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

Reste à couvrir dans le pilote : un prospect d'élite pré-pro. Shea Weber et Kevin
Fiala couvrent le joueur NHL établi.

### Résultats du pilote — 2026-07-28

5 éléments passés, `status: completed` partout, briefs de 4 900 à 5 800 caractères
avec sources datées. Par élément :

| | moyenne | étendue |
|---|---|---|
| recherches web | 9 | 6 – 14 |
| tokens entrée | ~70 000 | 53k – 106k |
| tokens sortie | ~4 500 | |
| durée | ~110 s | 20 s – 130 s |

Extrapolé aux 727 éléments : **~58 M tokens d'entrée, ~4 M de sortie, ~8 000
recherches web**. Le volume d'entrée vient de la boucle serveur, qui réinjecte le
contenu des pages à chaque tour — ce n'est pas notre prompt.

### Coût — ~400 $ US pour la passe complète

Tarifs relevés le 2026-07-28 sur `prices.azure.com`, compteurs
`5.5 ShortCo … Gl 1M Tokens` (Gl parce que le déploiement `gpt-5.5` est en
GlobalStandard, vérifié avec `az cognitiveservices account deployment list`) :

| | $US / M tokens |
|---|---|
| entrée | 5,00 |
| entrée en cache | 0,50 |
| sortie | 30,00 |

**0,56 $ par élément en moyenne** (0,45 $ – 0,65 $), soit **~405 $ pour les 727**,
fourchette 325 $ – 476 $ selon que les éléments ressemblent au moins ou au plus
coûteux du pilote.

Deux points qui pèsent sur ce chiffre :

- **Aucune remise de cache.** `cached_input_tokens` vaut 0 sur tous les appels
  mesurés. Chaque élément est une conversation neuve, et la boucle serveur ne
  réutilise rien d'un tour à l'autre. Le tarif plein s'applique aux 58 M tokens.
- **`web_search` n'a aucun compteur publié.** Sur les 29 394 tarifs du service
  Foundry Models, le seul compteur d'appels d'outil est `file-search`
  (2,50 $/1000 appels). La recherche web semble donc facturée uniquement par les
  tokens qu'elle réinjecte — ce qui expliquerait les 80 000 tokens d'entrée par
  élément. À confirmer sur la facture : l'absence de compteur public n'est pas une
  preuve de gratuité. Si un compteur existait au tarif de `file-search`, les ~8 000
  recherches ajouteraient ~20 $, ce qui ne change pas l'ordre de grandeur.

Le même élément relancé trois fois a consommé 55k, 63k et 82k tokens d'entrée pour
6, 7 et 12 recherches : la variance vient de l'agent, pas du joueur. Une passe
complète ne sera donc pas reproductible au dollar près.

Reste à faire tourner le pilote sur `gpt-5.4-mini`, déjà déployé (issue o80). C'est
le seul levier qui ferait vraiment bouger les 400 $.

Deux enseignements du pilote :

1. **L'agent recopie le contexte qu'on lui donne.** La première version du contexte
   décrivait le statut comme « espoir (moins de 25 matchs dans la LNH) » ; le brief
   de Criscuolo est ressorti avec « il demeure sous le seuil des 25 matchs dans la
   LNH ». Un artefact de notre classification, présenté comme un fait sur le joueur.
   Les libellés de stade ne portent plus de seuil numérique.

2. **Les URLs sources fuient, même quand la prose ne fuit pas.** Les briefs ne
   nomment jamais le retour de l'échange, mais les sources incluent des articles
   d'annonce du type `detroit-trades-kyle-criscuolo-receives-jasper-weatherby`.
   Toléré dans `raw/`, mais **l'extraction (E6) ne doit pas donner les URLs au
   modèle** — seulement leur domaine et leur date.

### Contexte manquant

`classified_elements.jsonl` ne porte ni l'âge ni les matchs NHL avant le trade,
contrairement à ce que supposait la section « Contexte fourni à l'agent ». Ces
champs viennent du module stats NHL (étape 7a). En attendant, le contexte se limite
à nom, date, équipes, position et stade de carrière — suffisant pour que l'agent
trouve les bons articles dans les 5 cas du pilote.

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
