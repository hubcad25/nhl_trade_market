Tu contribues à un corpus d'entraînement pour un modèle qui prédit la valeur de retour
d'un échange de la LNH (NHL trade). Ta tâche : produire, pour CHAQUE joueur listé
ci-dessous (tous impliqués dans le(s) même(s) trade(s)), un "brief" de recherche —
un portrait factuel et sourcé du joueur tel qu'il était perçu à la date de son trade,
en suivant EXACTEMENT le protocole ci-dessous. C'est un travail de recherche web réel
via tes outils WebSearch/WebFetch — pas une réponse depuis ta mémoire.

## Le protocole (identique pour chaque joueur)

Pour chaque joueur, fais une recherche web distincte pour CHACUNE des 9 rubriques
suivantes avant de rédiger. Si une recherche ne donne rien d'exploitable, reformule-la
au moins une fois avant d'abandonner la rubrique.

Rubriques : statut et âge, rang de repêchage, niveau de jeu et production récente,
forces reconnues, réserves des recruteurs/analystes, projection consensuelle,
situation contractuelle, santé, climat avec l'organisation.

La rubrique « climat avec l'organisation » couvre ce qui explique la disponibilité du
joueur au-delà de sa valeur sportive : conflit avec la direction/l'entraîneur,
capitanat retiré, demande d'échange publique, dossier disciplinaire. S'il n'y a rien
de tel, dis-le simplement — l'absence de conflit est une information utile.

**Écris comme si tu rédigeais la veille de l'échange**, sans aucune connaissance de ce
qui s'est passé depuis (y compris sa carrière ultérieure, même pour choisir quoi
mettre en avant). N'évalue jamais l'échange lui-même, ne mentionne jamais ce qu'il a
rapporté ni qui est allé dans l'autre sens.

**N'utilise et ne cite JAMAIS un article qui annonce, commente ou récapitule ce
trade** — reconnaissable à son titre/URL ("trade", "traded", "acquire", "acquires",
"receives", "roster transaction", "in exchange for") ou à une date de publication le
jour même ou après la date du trade. Cherche l'info ailleurs, dans une source
antérieure et indépendante. Si tu ne la trouves nulle part ailleurs, "non documenté".

### Règles de sourçage, sans exception

- Chaque affirmation factuelle est suivie de sa source et sa date de publication.
- Une affirmation que tu ne peux pas rattacher à une page réellement consultée ne
  s'écrit pas — écris "non documenté" à la place. Vaut d'abord pour la signalétique
  (taille, poids, main de tir, rang de repêchage, termes de contrat) : jamais de
  mémoire.
- Si deux sources se contredisent, donne les deux et dis laquelle tu retiens.
- Une durée relative ("il reste un an sur son contrat") est vraie à la date de
  PUBLICATION de la source, pas forcément à la date du trade. Recalcule-la toi-même
  depuis une date fixe (signature, blessure), ou marque "non documenté" si tu ne
  peux pas.

Un portrait court et entièrement sourcé vaut mieux qu'un portrait complet à moitié
deviné. Une rubrique vide est une information utile — ne la comble pas.

### Forme

Le "brief" est un texte en français, une rubrique par section titrée. Pas de
préambule sur ta méthode, pas de conclusion, pas d'offre de reformuler.

## Ce que tu dois écrire pour CHAQUE joueur

Un fichier JSON à l'emplacement exact indiqué pour ce joueur (dans "output_path"),
avec ce schéma :

```json
{
  "trade_id": <int>,
  "trade_date": "<YYYY-MM-DD>",
  "receives_key": "<team_one_receives|team_two_receives>",
  "element_index": <int>,
  "player_name": "<nom du joueur>",
  "nhl_id": <int>,
  "type_classified": "<nhl_skater|nhl_goalie|skater_prospect|goalie_prospect>",
  "model": "<TON_MODELE_GEMINI>",  // ex: gemini-2.5-pro
  "prompt_version": "v6",
  "brief": "<le texte du portrait complet, en francais, rubriques titrees>",
  "sources": [{"url": "<url>", "title": "<titre ou null>"}, ...],
  "queries": ["<requete 1>", "<requete 2>", ...],
  "n_searches": <int>,
  "status": "completed"
}
```

Utilise Write (ou Bash avec un heredoc) pour créer chaque fichier — un fichier par
joueur, pas un fichier groupé. Crée le dossier parent s'il n'existe pas déjà (il
existe déjà normalement : data/raw/briefs/<TON_MODELE_GEMINI>/v6/).

Fais chaque joueur en profondeur (vraies recherches, pas de raccourci) même s'il y en
a plusieurs dans ce lot — c'est le même standard de qualité qui a servi à construire
le reste du corpus (727 autres éléments, prompt v6, déjà validé). Rapporte en fin de
tâche, en 2-3 phrases, le nombre de fichiers écrits et tout joueur pour lequel la
couverture web était très mince.


## Joueurs à traiter

### Trade 1009518 — 2013-10-27 — New York Islanders <-> Buffalo Sabres

#### Thomas Vanek (nhl_id=8470598)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2013-10-27
- des Buffalo Sabres vers les New York Islanders
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009518
trade_date: 2013-10-27
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009518-one-0.json

#### Matt Moulson (nhl_id=8470852)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2013-10-27
- des New York Islanders vers les Buffalo Sabres
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009518
trade_date: 2013-10-27
receives_key: team_two_receives
element_index: 2
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009518-two-2.json

### Trade 1009555 — 2014-03-05 — New York Rangers <-> Tampa Bay Lightning

#### Martin St. Louis (nhl_id=8466378)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-03-05
- des Tampa Bay Lightning vers les New York Rangers
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier droit
trade_id: 1009555
trade_date: 2014-03-05
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009555-one-0.json

#### Ryan Callahan (nhl_id=8471339)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-03-05
- des New York Rangers vers les Tampa Bay Lightning
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier droit
trade_id: 1009555
trade_date: 2014-03-05
receives_key: team_two_receives
element_index: 2
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009555-two-2.json

### Trade 1009559 — 2014-03-05 — Montreal Canadiens <-> New York Islanders

#### Thomas Vanek (nhl_id=8470598)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-03-05
- des New York Islanders vers les Montreal Canadiens
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009559
trade_date: 2014-03-05
receives_key: team_one_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009559-one-1.json

### Trade 1009561 — 2014-03-05 — Minnesota Wild <-> Buffalo Sabres

#### Matt Moulson (nhl_id=8470852)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-03-05
- des Buffalo Sabres vers les Minnesota Wild
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009561
trade_date: 2014-03-05
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009561-one-0.json

#### Cody McCormick (nhl_id=8469591)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-03-05
- des Buffalo Sabres vers les Minnesota Wild
- statut au moment de l'échange : joueur établi de la LNH
- position : centre
trade_id: 1009561
trade_date: 2014-03-05
receives_key: team_one_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009561-one-1.json

#### Torrey Mitchell (nhl_id=8471338)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-03-05
- des Minnesota Wild vers les Buffalo Sabres
- statut au moment de l'échange : joueur établi de la LNH
- position : centre
trade_id: 1009561
trade_date: 2014-03-05
receives_key: team_two_receives
element_index: 2
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009561-two-2.json

### Trade 1009583 — 2014-06-27 — Nashville Predators <-> Pittsburgh Penguins

#### James Neal (nhl_id=8471707)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-06-27
- des Pittsburgh Penguins vers les Nashville Predators
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009583
trade_date: 2014-06-27
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009583-one-0.json

#### Patric Hornqvist (nhl_id=8471887)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-06-27
- des Nashville Predators vers les Pittsburgh Penguins
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier droit
trade_id: 1009583
trade_date: 2014-06-27
receives_key: team_two_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009583-two-0.json

#### Nick Spaling (nhl_id=8474096)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2014-06-27
- des Nashville Predators vers les Pittsburgh Penguins
- statut au moment de l'échange : joueur établi de la LNH
- position : centre
trade_id: 1009583
trade_date: 2014-06-27
receives_key: team_two_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009583-two-1.json

### Trade 1009671 — 2015-07-10 — Dallas Stars <-> Chicago Blackhawks

#### Patrick Sharp (nhl_id=8469544)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-10
- des Chicago Blackhawks vers les Dallas Stars
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009671
trade_date: 2015-07-10
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009671-one-0.json

#### Stephen Johns (nhl_id=8475730)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-10
- des Chicago Blackhawks vers les Dallas Stars
- statut au moment de l'échange : espoir, sans poste régulier dans la LNH
- position : défenseur
trade_id: 1009671
trade_date: 2015-07-10
receives_key: team_one_receives
element_index: 1
type_classified: skater_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009671-one-1.json

#### Trevor Daley (nhl_id=8470110)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-10
- des Dallas Stars vers les Chicago Blackhawks
- statut au moment de l'échange : joueur établi de la LNH
- position : défenseur
trade_id: 1009671
trade_date: 2015-07-10
receives_key: team_two_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009671-two-0.json

#### Ryan Garbutt (nhl_id=8476116)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-10
- des Dallas Stars vers les Chicago Blackhawks
- statut au moment de l'échange : joueur établi de la LNH
- position : centre
trade_id: 1009671
trade_date: 2015-07-10
receives_key: team_two_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009671-two-1.json

### Trade 1009677 — 2015-07-01 — Pittsburgh Penguins <-> Toronto Maple Leafs

#### Phil Kessel (nhl_id=8473548)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-01
- des Toronto Maple Leafs vers les Pittsburgh Penguins
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier droit
trade_id: 1009677
trade_date: 2015-07-01
receives_key: team_one_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009677-one-1.json

#### Tim Erixon (nhl_id=8475148)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-01
- des Toronto Maple Leafs vers les Pittsburgh Penguins
- statut au moment de l'échange : joueur établi de la LNH
- position : défenseur
trade_id: 1009677
trade_date: 2015-07-01
receives_key: team_one_receives
element_index: 2
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009677-one-2.json

#### Tyler Biggs (nhl_id=8476475)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-01
- des Toronto Maple Leafs vers les Pittsburgh Penguins
- statut au moment de l'échange : espoir, sans poste régulier dans la LNH
- position : ailier droit
trade_id: 1009677
trade_date: 2015-07-01
receives_key: team_one_receives
element_index: 3
type_classified: skater_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009677-one-3.json

#### Kasperi Kapanen (nhl_id=8477953)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-01
- des Pittsburgh Penguins vers les Toronto Maple Leafs
- statut au moment de l'échange : espoir, sans poste régulier dans la LNH
- position : ailier droit
trade_id: 1009677
trade_date: 2015-07-01
receives_key: team_two_receives
element_index: 2
type_classified: skater_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009677-two-2.json

#### Scott Harrington (nhl_id=8476449)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-01
- des Pittsburgh Penguins vers les Toronto Maple Leafs
- statut au moment de l'échange : espoir, sans poste régulier dans la LNH
- position : défenseur
trade_id: 1009677
trade_date: 2015-07-01
receives_key: team_two_receives
element_index: 3
type_classified: skater_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009677-two-3.json

#### Nick Spaling (nhl_id=8474096)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2015-07-01
- des Pittsburgh Penguins vers les Toronto Maple Leafs
- statut au moment de l'échange : joueur établi de la LNH
- position : centre
trade_id: 1009677
trade_date: 2015-07-01
receives_key: team_two_receives
element_index: 4
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009677-two-4.json

### Trade 1009731 — 2016-02-27 — Florida Panthers <-> Calgary Flames

#### Jiri Hudler (nhl_id=8470201)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2016-02-27
- des Calgary Flames vers les Florida Panthers
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier droit
trade_id: 1009731
trade_date: 2016-02-27
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009731-one-0.json

### Trade 1009869 — 2017-06-23 — Columbus Blue Jackets <-> Chicago Blackhawks

#### Artemi Panarin (nhl_id=8478550)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2017-06-23
- des Chicago Blackhawks vers les Columbus Blue Jackets
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009869
trade_date: 2017-06-23
receives_key: team_one_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009869-one-1.json

#### Tyler Motte (nhl_id=8477353)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2017-06-23
- des Chicago Blackhawks vers les Columbus Blue Jackets
- statut au moment de l'échange : joueur établi de la LNH
- position : centre
trade_id: 1009869
trade_date: 2017-06-23
receives_key: team_one_receives
element_index: 2
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009869-one-2.json

#### Brandon Saad (nhl_id=8476438)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2017-06-23
- des Columbus Blue Jackets vers les Chicago Blackhawks
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1009869
trade_date: 2017-06-23
receives_key: team_two_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009869-two-1.json

#### Anton Forsberg (nhl_id=8476341)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2017-06-23
- des Columbus Blue Jackets vers les Chicago Blackhawks
- statut au moment de l'échange : gardien espoir, sans poste régulier dans la LNH
- position : gardien
trade_id: 1009869
trade_date: 2017-06-23
receives_key: team_two_receives
element_index: 2
type_classified: goalie_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1009869-two-2.json

### Trade 1010031 — 2019-02-25 — Vegas Golden Knights <-> Ottawa Senators

#### Mark Stone (nhl_id=8475913)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2019-02-25
- des Ottawa Senators vers les Vegas Golden Knights
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier droit
trade_id: 1010031
trade_date: 2019-02-25
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1010031-one-0.json

#### Erik Brannstrom (nhl_id=8480073)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2019-02-25
- des Vegas Golden Knights vers les Ottawa Senators
- statut au moment de l'échange : espoir, sans poste régulier dans la LNH
- position : défenseur
trade_id: 1010031
trade_date: 2019-02-25
receives_key: team_two_receives
element_index: 1
type_classified: skater_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1010031-two-1.json

#### Oscar Lindberg (nhl_id=8475715)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2019-02-25
- des Vegas Golden Knights vers les Ottawa Senators
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier gauche
trade_id: 1010031
trade_date: 2019-02-25
receives_key: team_two_receives
element_index: 2
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1010031-two-2.json

### Trade 1010061 — 2019-06-29 — Pittsburgh Penguins <-> Arizona Coyotes

#### Alex Galchenyuk (nhl_id=8476851)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2019-06-29
- des Arizona Coyotes vers les Pittsburgh Penguins
- statut au moment de l'échange : joueur établi de la LNH
- position : centre
trade_id: 1010061
trade_date: 2019-06-29
receives_key: team_one_receives
element_index: 0
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1010061-one-0.json

#### Pierre-Olivier Joseph (nhl_id=8480058)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2019-06-29
- des Arizona Coyotes vers les Pittsburgh Penguins
- statut au moment de l'échange : espoir, sans poste régulier dans la LNH
- position : défenseur
trade_id: 1010061
trade_date: 2019-06-29
receives_key: team_one_receives
element_index: 1
type_classified: skater_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1010061-one-1.json

#### Phil Kessel (nhl_id=8473548)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2019-06-29
- des Pittsburgh Penguins vers les Arizona Coyotes
- statut au moment de l'échange : joueur établi de la LNH
- position : ailier droit
trade_id: 1010061
trade_date: 2019-06-29
receives_key: team_two_receives
element_index: 1
type_classified: nhl_skater
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1010061-two-1.json

#### Dane Birks (nhl_id=8477397)
Contexte (pour t'aider a trouver les bons articles -- ne le restitue pas tel quel) :
- échangé le 2019-06-29
- des Pittsburgh Penguins vers les Arizona Coyotes
- statut au moment de l'échange : espoir, sans poste régulier dans la LNH
- position : défenseur
trade_id: 1010061
trade_date: 2019-06-29
receives_key: team_two_receives
element_index: 2
type_classified: skater_prospect
output_path: /home/hubcad25/code/hockey/nhl_trade_market/data/raw/briefs/<TON_MODELE_GEMINI>/v6/1010061-two-2.json
