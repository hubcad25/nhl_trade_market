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

### gpt-5.4-mini — 12 $ au lieu de 405 $, mais il invente

Mêmes 5 cas, même prompt, `--model gpt-5.4-mini` (tarifs Gl : 0,75 $/M entrée,
0,075 $/M en cache, 4,50 $/M sortie).

| | gpt-5.5 | gpt-5.4-mini |
|---|---|---|
| tokens entrée / élément | 80 000 | 15 600 |
| tokens sortie / élément | 5 200 | 1 500 |
| recherches / élément | 11,0 | 2,4 |
| durée / élément | 110 s | 15 s |
| coût / élément | 0,56 $ | 0,017 $ |
| **passe de 727** | **~405 $** | **~12 $** |

Le mini écrit des briefs de longueur comparable (3 800 – 5 600 caractères contre
4 900 – 8 200) à partir de **quatre fois moins de recherches**. C'est le problème,
pas l'économie : il produit autant de prose avec deux fois moins de sources.

| caractères de prose par source citée | gpt-5.5 | gpt-5.4-mini |
|---|---|---|
| Kyle Criscuolo | 632 | 753 |
| Kevin Fiala | 908 | 970 |
| Shea Weber | 902 | 1 127 |
| Nils Juntorp | 815 | **2 407** |
| Graham Sward | 822 | **2 475** |

Sur les deux joueurs les moins couverts — exactement ceux pour qui la recherche a de
la valeur — le mini fait une seule recherche et écrit 4 800 caractères. La différence
sort de sa mémoire paramétrique, pas du web.

**Défaillance concrète.** Dans le brief de Juntorp, le mini écrit « 6'1", 196 lb,
**gaucher au tir** » puis, deux paragraphes plus bas, « âge, taille, **tir droitier** » —
les deux attribués à EliteProspects, dans le même brief. gpt-5.5 écrit « il tire de la
droite » et cite une source distincte. Le mini n'a pas seulement halluciné : il s'est
contredit sans le voir, sur une fiche signalétique triviale à vérifier.

**Décision : rester sur gpt-5.5 pour la passe complète.** L'écart de 390 $ est réel,
mais le dataset d'entraînement se construit une fois et l'étape 6 extrait des profils
structurés à partir de ces briefs — une hallucination non détectée à l'étape 5 se
propage jusqu'au modèle fine-tuné. 390 $ contre un corpus contaminé, ce n'est pas
un arbitrage serré.

Les briefs du mini restent dans `data/raw/briefs/gpt-5.4-mini/` : ils serviront de
point de comparaison si le détecteur d'hallucination (n7z) a besoin de cas positifs.

---

## Prompt v2 — protocole de recherche et discipline de sourçage

Le prompt v1 ne disait rien sur *combien* chercher. C'est précisément ce qui manquait
au mini, qui faisait 2,4 recherches là où gpt-5.5 en faisait 11. v2 ajoute trois
choses : un protocole (une recherche par rubrique, reformuler avant d'abandonner),
l'obligation de rattacher chaque fait à une page consultée avec « non documenté »
comme repli imposé, et l'interdiction explicite d'écrire la signalétique de mémoire —
taille, poids, main de tir, repêchage, contrat — qui est exactement là où le mini
avait halluciné.

Le cache est désormais indexé par `{modèle}/{version de prompt}/` : changer le prompt
rend les lots incomparables, et on veut mesurer une révision sans perdre le précédent.

Mesures sur les 5 mêmes cas, par élément :

| | rech. | sources | car. | car./source | « non doc. » | artefacts |
|---|---|---|---|---|---|---|
| mini v1 | 2,4 | 3,8 | 4 803 | 1 264 | 0 | 4/5 |
| mini v2 | 3,2 | 4,8 | 3 837 | **799** | 5 | 5/5 |
| 5.5 v1 | 11,0 | 7,6 | 6 124 | 806 | 0 | 0/5 |
| 5.5 v2 | 9,8 | **10,8** | 7 055 | **653** | 14 | 0/5 |

**v2 améliore les deux modèles, et gpt-5.5 davantage** : il passe de 7,6 à 10,8
sources par brief et de 806 à 653 caractères par source. La discipline de sourçage
profite au modèle qui cherche déjà beaucoup.

**Le mini devient défendable sans devenir équivalent.** Sa contradiction sur la main
de tir de Juntorp a disparu, et son ratio caractères/source rejoint celui de gpt-5.5
v1. Mais il y arrive en écrivant moins, pas en cherchant plus : 4,8 sources contre
10,8. Le matériel par joueur reste deux fois plus mince. Pour un corpus
d'entraînement, ça donne des profils plus creux — pas nécessairement faux.

**Défaut restant, propre au mini : 5 briefs sur 5** contiennent un préambule méta
(« Voici un portrait strictement ancré dans des sources consultées ») et une relance
conversationnelle finale (« Si tu veux, je peux aussi te le reformater en portrait
journalistique de 120-150 mots »). gpt-5.5 : 0 sur 5, dans les deux versions.
Corrigeable au prompt ou en post-traitement, mais c'est du bruit à retirer avant
l'étape 6.

### Décision de coût — à trancher

| | coût des 727 |
|---|---|
| gpt-5.5 v2 | ~414 $ |
| gpt-5.4-mini v2 | ~14 $ |

Trois options :

1. **Tout en gpt-5.5** — ~414 $, le matériel le plus riche, aucune surprise.
2. **Tout en mini** — ~14 $, profils deux fois plus minces, et il faut d'abord relire
   à la main assez de briefs pour croire à sa fiabilité factuelle (issue c4z).
3. **Hybride, par escalade** — passer les 727 au mini (14 $), puis relancer en 5.5
   seulement les éléments dont le brief mini est maigre (peu de sources, beaucoup de
   « non documenté »). Le cache par modèle rend ça trivial et l'étape 6 lit ce qu'on
   lui donne. À 30 % d'escalade : ~140 $.

L'option 3 a un attrait particulier : le critère d'escalade est mesurable sur le
brief lui-même, sans jugement humain, et c'est le même signal dont n7z a besoin.

---

## Prompt v4 — interdire la catégorie de source, pas seulement son contenu

v1-v3 interdisaient de *mentionner* le retour de l'échange, mais rien n'interdisait
de *se servir* d'une page qui l'annonce. En relisant les briefs v3 en détail (pas
seulement leurs métriques agrégées), le brief de Shea Weber tirait 16 de ses 18
citations d'un communiqué de transaction ; celui de Criscuolo, plusieurs aussi. Les
métriques de forme (nombre de sources, ratio caractères/source) ne voyaient rien
d'anormal — c'est en lisant le contenu que le problème est apparu. Un communiqué
d'acquisition est écrit après coup et encadre le joueur par ce qu'il a rapporté,
même quand le brief n'en reprend pas le texte : c'est la fuite temporelle que le
prompt était censé bloquer, par la porte de derrière.

v4 ajoute une clause qui interdit la catégorie de source (titre/URL contenant
« trade », « acquire », « receives »… ou publiée le jour même ou après la date du
trade), avec repli en « non documenté » si l'info n'existe nulle part ailleurs.

Mesure (URLs de sources correspondant à un motif de transaction, faux positif
`-for-` corrigé) :

| | sources d'annonce d'échange |
|---|---|
| mini v3 | 29 % |
| **mini v4** | **11 %** |
| 5.5 v3 | 11 % |

Le mini rejoint le niveau de gpt-5.5 sur le même prompt — la fuite n'était donc pas
propre au mini, v1-v3 la permettaient pour les deux modèles, gpt-5.5 s'en tenait
juste plus loin par tempérament. La clause **réduit** la fuite, elle ne l'élimine
pas : 2 citations sur 19 dans le pilote mini v4 proviennent encore d'un communiqué
de transaction (le nombre d'années de contrat restantes de Weber, le statut
non-repêché de Criscuolo). Ce résidu n'a pas été creusé plus loin.

**Leçon de méthode** : le ratio caractères/source, le nombre de « non documenté »,
la présence d'artefacts conversationnels — tout ça mesure la forme du brief, pas
son contenu. Le seul test qui a trouvé la fuite temporelle a été de lire des briefs
en entier et de vérifier chaque URL citée. Avant toute décision de coût qui repose
sur une comparaison de modèles ou de prompts, relire du texte, pas seulement des
métriques agrégées.

---

## Escalade abandonnée comme stratégie de qualité — v4→v6 recalibrés sur relecture manuelle

`escalate_briefs.py` a d'abord automatisé l'escalade vers gpt-5.5 sur trois critères :
moins de 5 sources, plus de 2 « non documenté », une citation d'annonce de
transaction. Appliqué au pilote (10 cas), ça faisait escalader **9 briefs sur 10** —
en apparence un signal que le mini est globalement peu fiable.

En relisant les 10 briefs en entier plutôt que ces trois métriques, aucune ne
prédisait le vrai risque :

- **Peu de sources** (Fiala, Sward, 4 chacun) : contenu bon, joueurs simplement
  moins couverts. Le seuil de 5 était arbitraire, pas calé sur ce que le mini
  produit normalement.
- **« Non documenté »** (Korpisalo, 3 occurrences) : c'est le comportement voulu
  du prompt — deux jeux de stats contradictoires signalés comme tels plutôt que
  tranchés au hasard. Compter les occurrences pénalise la prudence.
- **Citation d'annonce de transaction** (5 des 10 cas) : dans 4 cas sur 5, la
  source citée annonce le trade **recherché**, pour un fait neutre et daté
  (contrat, statut de repêchage) — bas risque, le fait ne change pas selon qui le
  rapporte. Un seul cas (Charlie Coyle) citait l'annonce d'un trade **ultérieur et
  différent** du même joueur ; vérifié après coup (recherche web), le fait cité —
  un an restant sur son contrat de 6 ans/31,5 M$ signé en 2019 — s'est avéré exact
  pour la date recherchée. La catégorie de source est un mauvais proxy pour le
  risque réel.

**Décision : `escalate_briefs.py` supprimé.** Remplacé par `--retry-failed` dans
`research_player.py` — reprend uniquement les éléments sans cache ou avec un cache
en échec véritable (`status != completed`, brief vide), dans le même modèle. Pas de
second modèle, pas de jugement de qualité automatisé. La qualité se juge en lisant
(issue c4z), pas en comptant.

### v5 — durées relatives à recalculer

Le cas Coyle a révélé un risque réel même si, cette fois, il ne s'est pas
matérialisé : une durée relative (« il reste un an sur son contrat », « agent
libre la saison prochaine ») est vraie à la date de **publication** de la source,
pas nécessairement à la date du trade recherché. v5 demande au modèle de la
recalculer depuis une date fixe (signature, date de blessure) plutôt que de la
recopier telle quelle.

### v6 — la rubrique manquante, trouvée par un cas hors-échantillon

Test final avant la passe complète : **Jack Eichel, Buffalo → Vegas, 2021-11-04**
— hors du dataset (qui commence en 2022-06), construit à la main pour stress-tester
un cas où le joueur est disponible pour une raison autre que sportive. Sur v5, le
brief capturait le désaccord médical (sous « santé » et « réserves ») mais
manquait les deux faits les plus significatifs du cas réel : le retrait du
capitanat (23 septembre 2021) et la demande d'échange rendue publique. Pas un
accident de recherche — **aucune des 8 rubriques ne leur correspondait**.

v6 ajoute une 9e rubrique, « climat avec l'organisation » : conflit avec la
direction ou l'entraîneur, capitanat retiré ou refusé, demande d'échange publique,
dossier disciplinaire — avec instruction explicite de dire « rien trouvé » plutôt
que d'en inventer.

Revalidé sur Eichel (mini) : capitanat retiré, désaccord public depuis mai 2021,
demandes répétées de l'agent en juillet, demande d'échange publique — tout daté
avant le trade, 11 sources, aucune fuite sur la suite de carrière. Revalidé sur 3
cas sans controverse (Weber, Fiala, Faber) : la rubrique répond « aucune trace
documentée » plutôt que d'inventer une tension — pas de bruit ajouté sur le cas
normal. Un seul résidu mineur : Weber cite une lettre de remerciement publiée
après le trade pour appuyer l'absence de conflit — hors-instruction sur la
catégorie de source, mais le contenu ne fuit rien.

**v6 est la version retenue pour la passe complète.**
