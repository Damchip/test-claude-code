# Carto Matcher

Outil **local** qui, à partir d'un fichier client (lecture ECU / `.bin`), cherche
dans **ta base de solutions** s'il existe déjà une solution fonctionnelle similaire.

- Le moteur de matching tourne **100% sur ta machine** : extraction d'identifiants,
  empreinte, similarité binaire, recherche SQLite. **Ta base ne va jamais en ligne.**
- L'assistant conversationnel (optionnel) passe par l'**API Anthropic** mais ne reçoit
  qu'un **résumé déjà calculé** (libellés, type de solution, score, statut) — **jamais**
  un octet de fichier ni le contenu de la base. Sans clé API, l'outil fonctionne en
  mode hors-ligne (synthèse générée localement).

## Installation

Il faut Python 3.9+.

```bash
pip install -r requirements.txt
```

## Mise à jour depuis 1.32 / 1.33

1. Dézippe **à côté** de l’ancienne copie (ne pas écraser `data/`).
2. Copie `data/solutions.db` (et `data/config.json` si tu as une clé / un verrou)
   dans le nouveau dossier.
3. Lance **Lancer Carto Matcher.bat** — la base est migrée toute seule
   (`minhash_ver`, WAL).
4. Clique **Empreintes v2** (onglet Solutions) : recalcule MinHash sans padding.
5. Relance un import de ta bibliothèque : les types FAP / EGR / E85 ignorés
   comme « doublons » en 1.32 vont enfin entrer, et original + solution sont
   copiés dans `data/files/<id>/`.

**Déjà une sauvegarde `.db` ?** Tableau de bord → *Importer une base .db*
(pas besoin de coller le fichier à la main). La 1.35.1 migre le schéma toute
seule. Les chemins des BIN (OneDrive) restent ceux de la machine d'origine.

Ensuite **Solutions → Relire les fiches** : remplit les types / plateformes
vides et corrige les libellés (Claas Jaguar, `dMe`…) depuis les chemins.

Si Auto-patch dit « fichier introuvable » : **Tableau de bord → Pointer le
dossier CARTOS** (le dossier OneDrive de *cette* machine), Tester, Appliquer.
Puis **File atelier** (archive + empreintes v2 + versions ECU) et
**Nettoyer** (suspects, doublons, solutions orphelines).

Le matching liste **toutes les prestas** d'un même stock (Stage 1 et FAP).
Le portail client les affiche en devis séparés.

**v1.47** : Auto-patch nomme les zones (FAP / EGR / SCR / Stage / E85) et
refuse de toucher une signature RSA. Cartes 2D : facteur manuel + export CSV.

**v1.48** : onglet Dossiers = fiche véhicule (immat / VIN / client / dump).

**v1.49** : combiner Stage 1 + FAP + E85 (même stock) en un seul auto-patch.

**v1.50** : pack de sortie (ZIP BIN + rapport) nommé à l'immat, rangé dans le dossier client.

Ne lance **pas** `seed_demo.py` sur ta vraie base (il n’écrit que `data/demo.db`).


## Essai immédiat (données de démo)

```bash
python seed_demo.py     # crée data/demo.db (5 fiches) + data/demo_client.bin
set CARTO_DB=data\demo.db
python app.py           # lance le serveur local
```

Ouvre **http://127.0.0.1:5000**, puis dépose `data/demo_client.bin`.
Il doit ressortir l'Audi A3 **deux fois** (Stage 1 et DPF/FAP off) — même stock,
deux prestations. L'onglet **Cartes 2D** sur la fiche Audi (vue Original +
Solution) montre la table 16×12 plantée, cellules Stage 1 en orange. L'onglet
Auto-patch peut ensuite appliquer le Stage 1 : le fichier de démo a un original
+ une solution archivés dans `data/files/`.

> `seed_demo.py` écrit dans **`data/demo.db`** (jamais dans ta vraie base). Pour
> l'essai : `set CARTO_DB=data\demo.db` avant de lancer, ou
> `set CARTO_DB=data\demo.db` puis `python app.py`.

## Activer l'assistant en ligne (optionnel)

Copie `.env.example` en `.env` et renseigne ta clé, **ou** exporte les variables :

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python app.py
```

## Avant le premier vrai test (recommandé)

Le détecteur est réglé sur des fichiers d'exemple : valide-le sur les tiens.

1. **Inspecte 2-3 vrais fichiers** pour voir ce qui est repéré :
   ```bash
   py inspect_file.py "D:\un_vrai_fichier.bin"
   ```
   Il affiche la plateforme, la version ECU détectée, et la liste des chaînes
   ASCII (avec offset) pour repérer la vraie clé à l'œil. Si la bonne version
   ECU apparaît dans la liste mais pas dans les candidats, envoie-la-moi :
   j'ajoute le motif correspondant.
2. **Importe un petit sous-dossier en simulation** (`--dry-run`) et vérifie les
   véhicules/types déduits.
3. **Teste le matching** avec un cas connu : prends un fichier déjà en base,
   modifie quelques octets, et vérifie qu'il ressort bien.

## Sauvegarde & sécurité des données

La base vit dans un seul fichier : `data/solutions.db` (à côté du programme).
À l'import / enregistrement, original et solution sont **copiés** dans
`data/files/<id>/` — les liens ne cassent plus si OneDrive déplace le dossier
source. Les fichiers d'origine sur le disque ne sont jamais déplacés ni
modifiés.

- À chaque lancement, le programme affiche le **chemin exact** de la base et le
  **nombre de fiches**, et fait une **sauvegarde horodatée** dans `data/backups/`
  (les 15 plus récentes sont conservées).
- Le bouton **« Sauvegarder »** (onglet Solutions) crée une copie à la demande —
  pratique juste après un gros import.
- **« Empreintes v2 »** recalcule MinHash sans padding pour les anciennes fiches
  (les nouvelles sont déjà en v2).
- Pour restaurer : copie le fichier voulu de `data/backups/` vers
  `data/solutions.db` et relance.
- Si « base vide » s'affiche alors que tu attends des fiches, vérifie au démarrage
  le chemin indiqué : le programme lit peut-être un autre `solutions.db` (dossier
  dupliqué, copie OneDrive). Ta vraie base est le fichier plus volumineux.

## Onglet Solutions

La liste de toutes les solutions, avec recherche instantanée. Le sélecteur
**« Classer par »** (Type / Fabricant / Plateforme / Statut / Étiquettes) affiche
des **pastilles cliquables** avec le nombre de fiches par catégorie : un clic
filtre la liste. Le sélecteur **« puis par »** ajoute un **second niveau** : la
liste filtrée est alors regroupée en sections (ex. classer par Fabricant, puis
regrouper par Type). Une solution à type combiné (ex. « AdBlue/SCR off + DTC off »)
compte dans chacune de ses catégories. Recherche texte et pastilles se combinent.

Chaque fiche peut recevoir des **étiquettes personnalisées** (champ « Étiquettes »
dans Modifier, séparées par des virgules — ex. « client VIP, urgent »). Elles
s'affichent sur la fiche et deviennent une dimension de classement à part entière.

## Visualiseur 2D (carte d'octets)

L'onglet **« Visualiseur »** offre deux affichages d'un fichier :

- **Carte 2D** : les valeurs tracées en ligne (Y = valeur, X = adresse), comme la
  vue 2D d'ECM Titanium ou de WinOLS. Réglages **8/16/32 bits** et
  **Intel (LE) / Motorola (BE)**, fenêtre réglable, navigation par barre, boutons
  ◀ ▶ ou saisie d'un offset. C'est l'affichage par défaut.
- **Aperçu mémoire (octets)** : chaque octet devient un pixel (gris ou couleur).
  Pratique pour repérer d'un coup d'œil zones de code, cartographies et padding.

Sources communes : un **fichier du disque**, plusieurs fichiers à **comparer**,
ou une **solution de la base**. En vue « Original + Solution », les deux courbes
sont **superposées** (original en rouge, solution en vert) — exactement la
comparaison ORI/MOD ; en carte mémoire, les octets modifiés sont surlignés en
orange. Les boutons **◀ Δ / Δ ▶** sautent directement d'une zone de différence
à la suivante.

> Anciennes fiches : si « original introuvable » apparaît en comparaison, va dans
> l'onglet **Solutions → « Réparer les liens »**. Ça reconstruit le chemin de
> l'original (dans le même dossier que la solution) pour les fiches importées
> avant cette fonction, sans tout réimporter.

Le rendu se fait dans le navigateur ; rien n'est envoyé en ligne.

## Livrer la solution sur un match

Quand un fichier client correspond à une solution, le résultat affiche un bouton
**« Télécharger la solution »** (le `.bin` à renvoyer au client), aussi disponible
dans l'onglet Solutions. Un indicateur précise la fiabilité :

- **stock identique** (sha256) → la solution est livrable telle quelle ;
- **base similaire** → c'est la solution d'une base proche mais pas identique :
  à vérifier (et à réajuster checksums compris) avant de flasher.

> Étapes suivantes prévues : visualisation/export du *delta* entre original et
> solution, puis auto-patch d'un fichier client (avec garde-fou « stock identique »
> et vérification des checksums dans ton outil avant flash).

## Construire ta base de solutions

Deux façons :

**A — Une par une (interface).** Analyse un fichier puis clique sur « Enregistrer
comme solution ». Le formulaire est prérempli (véhicule, version ECU, plateforme,
type) à partir du binaire et du nom de fichier ; ajuste, choisis le statut, valide.

> L'onglet **« Solutions »** en haut affiche toute la base : recherche instantanée
> (véhicule, plateforme, ECU, type…), **modification** et **suppression** de chaque
> fiche. Supprimer une fiche n'efface jamais le fichier d'origine sur ton disque
> (seulement la copie d'archive dans `data/files/<id>/` si elle existe).

**B — Tout un dossier d'un coup (`import_folder.py`).** Raisonne **par dossier**
(un dossier = un véhicule / un original). Dans chaque dossier il choisit :

- l'**original** (clé de reconnaissance) = le fichier marqué `ORI` dans son nom
  (ou l'extension `.ori`), sinon le fichier le plus ancien — c'est son empreinte
  qui est stockée, car c'est ce qu'un client envoie (une lecture d'origine) ;
- les **solutions** (livrables) = **un fichier par type** détecté dans le nom
  (Stage 1, FAP off, EGR off, E85…). Si plusieurs fichiers ont le même type,
  la révision **SAV** la plus élevée gagne (SAV3 > SAV2).

> La sélection se base sur les **marqueurs dans les noms** (ORI / SAV), pas sur
> les dates : c'est volontaire, car OneDrive (et les copies de dossiers) réécrit
> souvent la date de modification, ce qui rendrait un choix par date peu fiable.
> La date n'est utilisée qu'en dernier recours.

Une fiche est créée **par type de prestation** dans le dossier : empreinte de
l'original + lien vers le fichier solution de ce type. Un même ORI peut donc
porter Stage 1, FAP off, EGR off, E85… chacun comme fiche séparée. Les doublons
(même empreinte **et** même type) sont ignorés, donc tu peux relancer l'import
sans risque après avoir ajouté de nouveaux fichiers.

Disponible de deux façons, **avec le même résultat** :

- **Dans l'interface, onglet « Importer »** : colle le chemin du dossier, clique
  « Prévisualiser » pour voir ce qui sera ajouté (sans rien écrire), puis
  « Importer maintenant ». C'est le plus simple.
- **En ligne de commande** :
  ```bash
  py import_folder.py "D:\Mes solutions"
  ```

Options utiles :

- `--dry-run` : simule l'import et affiche ce qui serait détecté, sans rien écrire
  (recommandé pour une première passe de vérification).
- `--status testee` : impose un statut à tous les fichiers (défaut `a_confirmer`).
- `--type "Stage 1"` : impose un type de solution.
- `--ext .bin,.ori,.mod` : restreint les extensions importées (défaut : pas de `.dam`).
- `--min-ko` / `--max-mo` : bornes de taille (défaut 16 Ko à 32 Mo).

Les doublons (même empreinte **et** même type de prestation) sont ignorés
automatiquement, donc tu peux relancer l'import sans risque après avoir ajouté
de nouveaux fichiers. Un second type (ex. FAP off) sur un original déjà en base
**n'est pas** un doublon : il est ajouté.

> Conseil : enregistre de préférence tes fichiers d'**origine (stock)**. Quand un
> client enverra une lecture d'origine, la correspondance sera exacte. Les fichiers
> déjà modifiés fonctionnent aussi (la similarité binaire reste élevée).

Chaque solution stocke : version ECU, plateforme, fabricant, libellé véhicule,
type (Stage 1, DPF off…), statut de test, empreinte du fichier (sha256), signature
de similarité, et le chemin du fichier source.

## Nom de fichier et dossier : une aide précieuse

Le nom du fichier et des dossiers parents (ex. `Audi A3 2.0 TDI/EDC17CP14/A3_Stage1_EGRoff.bin`)
est exploité en plus du binaire (`matcher/metadata.py`). Il en extrait :

- la **marque** et le **véhicule**,
- la **plateforme ECU** (et donc le fabricant),
- le **type de solution** (Stage 1/2, DPF off, EGR off, AdBlue/SCR off, **E85 / Flexfuel**, DTC off…).

Ces informations sont des **indices à fort poids**, recoupés avec le binaire :

- si nom et binaire donnent la même plateforme → confiance renforcée
  (« Plateforme confirmée nom+binaire ») ;
- si le binaire est muet, le nom prend le relais ;
- le libellé véhicule du nom sert aussi de signal de correspondance ;
- à l'enregistrement, marque/véhicule/type/plateforme sont **préremplis** automatiquement.

Comment fournir le chemin :

- un **champ optionnel** dans l'interface où coller le dossier/chemin ;
- sinon le **nom du fichier** déposé est utilisé automatiquement ;
- en glissant un **dossier**, le chemin relatif est capté.

Les noms n'étant pas normalisés selon les sources, ils restent des indices et ne
remplacent jamais la vérification binaire.

## Détection des identifiants — multi-familles

L'extracteur (`matcher/extract.py`) couvre les principales familles d'ECU sans
configuration. Il combine trois stratégies :

- **Motifs typés par fabricant** : numéros Bosch (HW + logiciel), références VAG,
  Continental/Siemens, Marelli, Denso, Mercedes…
- **Détection de plateforme** : EDC15/16/17, MED9/17/40, MEVD17, ME7/9, MG1/MD1,
  SIMOS, PCR2.1, SID, EMS, SIM2K, DCM, MJD, IAW, MSV/MSD, DDE/DME, CRD/CR6,
  Trionic… La plateforme donne aussi le **fabricant** automatiquement.
- **Filet générique** : références alphanumériques isolées par des octets nuls,
  pour les ECU non répertoriées.

Chaque candidat est **typé, daté d'une famille et noté** (confiance 0–1, avec
bonus si la chaîne est isolée par des `\x00` — signature d'un vrai identifiant).
La plateforme sert aussi de signal de matching supplémentaire entre solutions.

Aucune liste de motifs ne couvre 100% des ECU du marché : pour une famille
particulière, ajoute un motif dans `PART_PATTERNS` / `PLATFORM_REGEX`, ou un
offset connu via `fixed_offsets()`. **Un fichier exemple par famille permet de
verrouiller l'extraction.**

## Comment ça marche (résumé)

Pour un fichier entrant :

1. Header programmeur retiré si présent (KESS, Autotuner…) — matching sur le *corps*.
2. Empreinte : `sha256` (fichier + corps) + taille + signature **MinHash v2**
   (padding 00/FF ignoré). Les fiches v1 restent comparables.
3. **Match exact fichier** : sha256 du dump ou du corps identique à un stock → 1.0.
4. **Calibration exacte** : un identifiant du fichier == `ecu_version` en base
   (égalité, pas sous-chaîne ; identifiant ≥ 8 caractères) → 0.97.
5. **Similarité binaire** : Jaccard(MinHash) ≥ seuil **et** tailles quasi identiques
   (±2 %, header connu toléré) → même base, modifs localisées.
6. **Plateforme / libellé** : indices faibles (score < 0,45) — utiles en interne,
   **insuffisants** pour un verdict client.

Côté **portail client**, le verdict n'utilise plus `score ≥ 0,6` :
- *compatible* : sha256 identique, ou calibration exacte + même taille ;
- *à vérifier* : calibration exacte (taille différente) **ou** MinHash ≥ 85 % +
  même taille + même plateforme ;
- *non trouvé* : le reste (même plateforme toute seule, libellé proche, etc.).

Chaque solution candidate reçoit un score (0–1) et la liste des raisons.

## Limites / pistes

- L'extracteur d'identifiants doit être calibré sur de vrais fichiers (cf. ci-dessus).
- MinHash en Python pur suffit jusqu'à plusieurs milliers de solutions ; au-delà,
  on peut accélérer avec `numpy` ou indexer les signatures.
- Possible ensuite : stocker la modif en *delta* vs stock, détection stock/déjà-modifié,
  export d'un rapport par dossier.

## Fichiers

```
app.py              serveur local (127.0.0.1)
inspect_file.py     diagnostic : ce que le détecteur voit dans un fichier
import_folder.py    import en masse d'un dossier de solutions
seed_demo.py        données de démonstration
matcher/
  extract.py        détection multi-familles (Bosch, VAG, Conti, Marelli…)
  metadata.py       lecture du nom de fichier / dossier (marque, véhicule, stage)
  importer.py       logique d'import par dossier (partagée CLI + interface)
  fingerprint.py    sha256 + MinHash v2 (padding ignoré)
  headers.py        détection / retrait des headers programmeur
  checksum.py       correcteur additif / CRC16 (honnête : jamais RSA)
  maps.py           détection Kennfeld 1D / 2D + CSV / facteur
  patch.py          auto-patch serveur (alignement, zones, checksums)
  zones.py          nommage FAP/EGR/SCR/Stage + refus RSA
  dossiers.py       dossiers client / véhicule (immat, VIN, dump)
  db.py             base SQLite + archive data/files/<id>/
  engine.py         moteur de matching
  ai.py             assistant via API (résumé uniquement)
templates/ static/  interface web
```

## Onglet Cartes 2D

Détecte les **Kennfeld / Kennlinie** dans un dump (axes 16 bits LE collés à une
table Z), comme un WinOLS allégé — **sans Damos**, donc **sans facteur ni unité**.

- Source : fichier disque, comparaison de 2 dumps, ou fiche de la base
  (original + solution).
- Liste à gauche, grille colorée à droite. Les cellules qui diffèrent
  (Stage 1, FAP…) sont orange, ancienne valeur barrée.
- Si la détection se trompe : offset + lignes × colonnes + inversion X/Y.
- **Facteur Z / offset / unité** : affichage physique (ex. × 0.01 mg) — saisi
  à la main, **pas de Damos**. Les axes restent bruts.
- **CSV** : la carte courante, ou toutes les cartes Δ (original / solution /
  delta). Séparateur `;` (Excel FR).
- Depuis Auto-patch, le bouton **2D r×c** d'une zone ouvre la table correspondante.

La détection est volontairement stricte (axes quasi linéaires, table plus lisse
que du bruit). Une carte 4×4 isolée dans du padding ne sort pas.


## Onglet Auto-patch

Applique automatiquement les modifications d'une solution de la base (le delta
**original → solution**) à un **fichier client**, et génère un fichier patché.
**Tout le calcul est serveur** : plus de téléchargement des deux BIN dans le
navigateur.

Fonctionnement et garde-fous :

- Choisis le fichier client et la solution de la base qui porte le patch, puis
  **« Analyser la compatibilité »**.
- Les **headers programmeur** (KESS +0x400, Autotuner…) sont retirés pour
  comparer les corps, puis recollés sur le fichier client.
- Le patch n'est applicable que si les **zones modifiées sont identiques** (stock)
  dans le client. L'outil affiche chaque zone (offset, taille, état) et un verdict :
  - vert : patch propre (client identique au stock connu hors modifications) ;
  - orange : zones compatibles mais le client diffère ailleurs → **checksums à
    recalculer** ;
  - rouge : des zones ne correspondent pas → application risquée (corruption).
- **« Générer le fichier patché »** crée un nouveau fichier `<nom>_PATCHED.<ext>`
  (le fichier source n'est jamais modifié). En cas de zones incompatibles, une
  option « Forcer » permet de n'appliquer que les zones compatibles, à tes risques.
- **Checksums** : blocs additifs Bosch (fin ou tête, 256 o → 32 Ko) et CRC-16
  (CCITT / IBM) si le schéma de blocs est clair (≥ 70 % des blocs utiles).
  Sinon statut *inconnu* / *non applicable* — l'UI n'affiche **jamais**
  « prêt à flasher ». Les signatures RSA/CSA (PKCS#1) sont détectées,
  **jamais patchées**, et bloquent le verdict flashable : passe par WinOLS
  / un correcteur dédié. MD1/MG1 : l'additif peut être juste, la RSA non.


> Même après correction additive, vérifie le fichier patché avant flash. Un
> EDC17 avec CSA/RSA propriétaire restera *inconnu*.

### Détection / étiquetage des zones

Après l'analyse de compatibilité, chaque zone modifiée reçoit un **type estimé** :

- **Étiquetage local (hors-ligne, toujours actif)** : déduit de la forme de la zone
  dans l'original (axe croissant, table lisse, scalaire/drapeau, données) et du
  motif de changement (mise à 0, 0xFF, valeur forcée), avec le contexte du type de
  solution (FAP, AdBlue/SCR, EGR, Stage…). Rien ne quitte la machine.
- **Étiquetage IA (interrupteur)** : si activé, envoie en ligne un **résumé
  structuré** des zones (offsets, tailles, formes, plages, plateforme, type de
  solution) — **jamais les octets ni la base** — pour obtenir une fonction probable
  par zone (axe régime, table de couple, limiteur, désactivation FAP…). Nécessite
  une clé `ANTHROPIC_API_KEY` ; sinon l'étiquetage local suffit.

Ces étiquettes sont des **estimations** d'aide à la lecture, pas une vérité absolue.

## Démarrage rapide (Windows)

Double-clique sur **`Lancer Carto Matcher.bat`** : il vérifie Python, installe Flask
si besoin, lance le serveur local et ouvre le navigateur sur http://127.0.0.1:5000.
(Si Python n'est pas installé, le lanceur te l'indique — installe-le depuis
python.org en cochant « Add Python to PATH ».)

Pour l'**étiquetage IA**, clique sur ⚙ (en haut à droite) et saisis ta clé API
Anthropic : elle est enregistrée localement dans `data/config.json` (en clair, sur
ta machine). Le bouton « Tester » vérifie qu'elle est valide. Sans clé, tout
fonctionne hors-ligne (étiquetage local).

## Onglet Tableau de bord

Vue d'ensemble de la base : **statistiques** par type, fabricant, plateforme et
statut (en barres), et compteurs de **fiches incomplètes** (sans libellé, sans
version ECU, sans lien original, sans fichier solution).

- **Exporter en CSV** : télécharge toute la base (métadonnées) en CSV ouvrable dans
  Excel — sauvegarde lisible et partageable.
- **Doublons** : repère les fiches au binaire identique (même empreinte) et permet
  d'en supprimer.
- **Sauvegardes** : liste les sauvegardes (date, nombre de fiches) et permet d'en
  **restaurer** une en un clic (la base courante est sauvegardée avant remplacement).

Dans l'onglet **Solutions**, les cases à cocher permettent une **édition en masse** :
ajouter une étiquette ou changer le statut de plusieurs fiches d'un coup.

Dans **Auto-patch**, le bouton **Rapport (PDF)** ouvre un rapport imprimable
(fichier client, solution, verdict, zones et étiquettes) à enregistrer en PDF.

## Onglet Dossiers

Un **dossier client** = un véhicule. Immat (plaque FR SIV / FNI), VIN,
coordonnées, garage partenaire, prestations (Stage 1, FAP, E85…) et le dump
client archivé à côté de la base — pas dans OneDrive.

- **Nouveau dossier** ou depuis Recherche / une **demande portail**.
- Statuts : ouvert → en cours (dès un patch) → livré → archivé.
- **Analyser** relance la Recherche sur le dump archivé ; **Auto-patch**
  pré-sélectionne ce dossier pour y rattacher le fichier généré.
- Les patchs générés avant la 1.48 restent en « non rattachés » (tu peux
  les accrocher à un dossier).

Le devis PDF (1.49) partira de ces prestations.

## Onglet Inspecteur

Équivalent web de `inspect_file.py` : choisis un fichier, et l'outil affiche la
plateforme et les identifiants détectés, puis la liste des chaînes ASCII avec leur
offset (et `[Z]` quand la chaîne est isolée par des octets nuls). Le bouton
« Copier le rapport » met tout le diagnostic dans le presse-papier — pratique pour
le partager et affiner la détection d'une famille ECU.

## Accès depuis plusieurs postes de l'atelier (réseau local)

Lance « **Lancer Carto Matcher (reseau atelier).bat** » sur le poste qui héberge la
base (ton poste habituel). Il affiche l'adresse à utiliser, par exemple
`http://192.168.1.50:5000`. Sur les autres postes, ouvre simplement cette adresse
dans le navigateur — aucune installation nécessaire sur les autres PC.

Points importants :
- La base de solutions reste **uniquement sur le poste serveur**. Rien n'est exposé
  sur internet : c'est ton réseau local (LAN) uniquement.
- Au premier lancement, Windows peut demander l'autorisation du pare-feu pour le
  port 5000 : réponds Oui (ou lance le .bat en administrateur une fois).
- Garde la fenêtre du serveur ouverte sur le poste hôte tant que les autres postes
  travaillent.
- À n'utiliser que sur un réseau de confiance (pas un wifi invité partagé).

Le lancement local classique (« Lancer Carto Matcher.bat ») reste disponible et
n'expose rien sur le réseau.

## Portail client (optionnel, application séparée)

Le portail laisse un client déposer son fichier et obtenir un verdict —
**compatible / à vérifier / non trouvé** — avec les prestations proposées et un
tarif. Il **ne révèle jamais** la bibliothèque : aucun nom de véhicule, fichier de
solution, score ou calibration ne sort. Seul le *type de prestation* (ex. « DPF
off ») est affiché, ce que le client cherche de toute façon.

Lancement : « **Lancer le portail client.bat** » (port 5001). Il peut tourner
**en même temps** que l'outil interne (port 5000). Le portail lit la même base en
lecture seule pour faire le matching.

Capture de lead : chaque dépôt est rangé dans `data/portal_inbox/` (le fichier +
un journal `demandes.log` avec les coordonnées éventuelles et le calculateur
détecté). L'atelier ouvre ensuite ces fichiers dans l'outil interne pour produire
la solution.

Tarifs : modifiables dans `data/portal_config.json` (créé au premier lancement).
Par défaut « sur devis » ; tu peux activer des prix fixes (`show_prices`, `prices`,
`default_price`), définir le nom de l'atelier, l'intro et un contact.

### Si un jour tu exposes le portail sur internet
Le serveur Flask intégré est un serveur de **développement**, pas fait pour une
exposition publique directe. Avant toute mise en ligne, il faut au minimum :
- un **reverse proxy** (nginx/Caddy) avec **HTTPS**,
- une **limitation de débit** (anti-abus / anti-spam de dépôts),
- éventuellement un serveur WSGI de production (gunicorn/waitress).
La taille des dépôts est déjà limitée (64 Mo). Commence en local/LAN ; on durcit
seulement si tu décides d'ouvrir au public.

## Verrou d'accès (mot de passe)

**Outil interne** : dans Réglages → « Verrou d'accès », définis un mot de passe
pour protéger l'outil sur le réseau atelier. Sans mot de passe, l'accès reste
libre (comme avant). Une fois activé, chaque poste doit se connecter ; une icône
⎋ dans l'en-tête permet de se déconnecter. Le mot de passe est stocké haché,
jamais en clair.

**Portail client** : dans Réglages → « Portail client », un verrou du même type
est disponible mais **désactivé par défaut**. ⚠ L'activer bloque aussi tes vrais
clients (ils devront connaître le mot de passe pour déposer leur fichier) — à
réserver à un usage restreint (tests, lien privé partagé à des clients choisis).
Pour un portail public normal, laisse ce champ vide.

## Traitement par lot

L'onglet **Traitement par lot** permet de traiter d'un coup un dossier entier de
fichiers clients (et ses sous-dossiers). Pour chaque fichier :

1. Recherche de la meilleure solution connue en base.
2. Calcul des zones de différence entre l'original et la solution de la fiche
   trouvée, puis vérification que le fichier client correspond bien au stock
   connu — exactement la même analyse que l'onglet Auto-patch.
3. **Patch automatique uniquement si le verdict est « propre »** (fichier client
   identique au stock connu en dehors des zones modifiées). Tout le reste
   (checksum à vérifier, zones incompatibles, aucune solution trouvée) est
   signalé dans le rapport — à traiter manuellement dans Auto-patch. Le
   traitement par lot ne patche jamais à l'aveugle.

Les fichiers sources ne sont **jamais** modifiés. Les fichiers patchés sont écrits
dans un sous-dossier `_traite` (structure de dossiers préservée). Chaque patch
généré est automatiquement journalisé dans l'onglet Dossiers.

Le seuil de correspondance est réglable : Standard (recommandé), Large (plus de
candidats mais moins fiable) ou Strict (solutions quasi certaines uniquement).

⚠ Comme pour un patch individuel, vérifie les checksums avant de flasher un
fichier généré par le traitement par lot.

## Demandes clients (boîte de réception du portail)

L'onglet **Demandes** liste tous les fichiers déposés sur le portail client :
date, verdict rendu au client, calculateur détecté, coordonnées éventuelles.
Un badge sur l'onglet indique le nombre de demandes non traitées.

Pour chaque demande : **Analyser** lance la recherche de solutions en base et
affiche les meilleurs résultats sous la demande ; **Télécharger** récupère le
fichier déposé (pour le passer dans Recherche ou Auto-patch) ; **Marquer traité**
classe la demande ; **Supprimer** retire la demande et son fichier.

## Mise en ligne chez un hébergeur (O2switch)

Espace client et outil atelier en HTTPS, chacun sur son sous-domaine, avec comptes atelier +
double authentification, tâches planifiées (cron), sauvegarde externe et surveillance :
**guide pas à pas dans [DEPLOIEMENT_O2SWITCH.md](DEPLOIEMENT_O2SWITCH.md)**.
Commandes serveur : `python outils_prod.py admin | taches | sante | verifier`.

## Mise en ligne du portail depuis un poste de l'atelier

Le portail est prêt techniquement : serveur de production (waitress), taille de
dépôt plafonnée (64 Mo), limitation anti-spam par adresse (10 dépôts/heure par
défaut, réglable via `max_depots_heure` dans `data/portal_config.json`, 0 pour
désactiver). Ce qui reste à choisir, c'est **comment** l'exposer. Deux options :

### Option A — Tunnel Cloudflare (recommandée, la plus simple)
Aucun port à ouvrir sur ta box, HTTPS automatique, gratuit.
1. Crée un compte Cloudflare et ajoute ton domaine (ou utilise un sous-domaine).
2. Installe `cloudflared` sur le poste qui héberge le portail.
3. Lance le portail avec « Lancer le portail client (production).bat ».
4. `cloudflared tunnel --url http://localhost:5001` (test rapide) ou configure un
   tunnel permanent vers `http://localhost:5001` associé à ton sous-domaine
   (ex. `portail.e85france.fr`).
Le trafic passe chiffré par Cloudflare jusqu'à ta machine ; ta box n'expose rien.

### Option B — Redirection de port + reverse proxy
1. Redirige le port 443 de ta box vers le poste du portail.
2. Installe un reverse proxy avec HTTPS automatique (Caddy est le plus simple :
   deux lignes de configuration vers `localhost:5001`).
3. Pointe ton domaine vers ton IP (DynDNS si IP dynamique).
Plus de contrôle, mais plus de maintenance et ta box est exposée.

### Dans les deux cas
- Lance toujours le portail avec le **lanceur production** (waitress), jamais le
  serveur de développement, pour une exposition continue.
- N'expose QUE le portail (port 5001). Depuis un poste de l'atelier, l'outil interne (5000) ne doit
  **jamais** être accessible depuis internet. Pour le mettre en ligne, passe par l'hébergement décrit
  dans DEPLOIEMENT_O2SWITCH.md (comptes atelier obligatoires + double authentification).
- Le poste doit rester allumé ; pense aux mises à jour Windows programmées.
- Intégration à ton site WordPress : le plus simple est un lien/bouton vers
  `https://portail.tondomaine.fr` (ou une iframe si tu veux l'intégrer visuellement).

## Fileservice (espace client)

Le portail (port 5001) sert l'espace client à sa racine : `http://127.0.0.1:5001/` (en ligne : `https://portail.ton-domaine.fr/`).
L'ancienne page de vérification anonyme d'un fichier reste disponible sur `/verifier`.
Tout se gère ensuite dans l'outil interne (port 5000), onglets **Fileservice** et **Clients**.
Les données sont dans `data/fileservice.db` (comptes, demandes, factures) et
`data/fileservice_fichiers/` (fichiers déposés et livrés), séparées de la bibliothèque de solutions.

### Mise en service (une fois)
1. **Fileservice → Réglages** : identité légale (raison sociale, adresse, SIRET, RCS, TVA, contact)
   — elle apparaît sur les factures et la page Support —, puis les horaires d'ouverture.
2. **Clients → E-mails (SMTP)** : boîte O2switch (voir plus bas), adresse de notification atelier et
   **adresse publique du portail** (ex. `https://portail.ton-domaine.fr`). Clique *Tester l'envoi*.
3. *Optionnel* — **Fileservice → Réglages → Stripe** pour le paiement en ligne (voir plus bas).
   Sans Stripe, les clients commandent par virement et tu crédites à la main.
4. Mets le portail en ligne (section « Mise en ligne du portail ») avec le lanceur production.

### Au quotidien
- **Nouvelle inscription** → onglet Clients (badge) → *Valider* : le client reçoit l'e-mail d'activation.
- **Créer un compte toi-même** (client au téléphone, au comptoir…) → Clients → *＋ Nouveau client* :
  le compte est actif tout de suite ; le client reçoit un e-mail pour choisir son mot de passe (lien
  7 jours, *Renvoyer l'invitation* si besoin), ou tu fixes le mot de passe et le lui communiques.
- **Paiement reçu par virement** → Clients → *Paiement reçu hors ligne* : choisis le pack, mets la
  référence du virement → crédits ajoutés **et** facture émise. Une même référence ne crédite qu'une fois.
- **Nouveau fichier** → onglet Fileservice (badge, e-mail atelier) → ouvre la demande :
  - *Analyser* cherche en bibliothèque ; *Auto-patch* charge le fichier client et ouvre l'onglet
    Auto-patch avec les fiches même stock ;
  - besoin d'une précision → écris au client en cochant « demander une info » ;
  - fichier prêt → *Livrer* : le client est prévenu et télécharge depuis son espace ;
  - impossible → *Refuser* avec le motif : les crédits sont remboursés automatiquement.
- Le client peut demander une **révision** gratuite pendant 30 jours : la demande repasse « en traitement ».
- Signe tes messages (champ *Signature* en haut de l'onglet Fileservice).

### Pages légales
CGV, mentions légales et politique de confidentialité sont publiées sur `/legal/cgv`,
`/legal/mentions-legales` et `/legal/confidentialite`. Les modèles fournis reprennent
l'identité légale saisie dans Fileservice → Réglages (directeur de la publication et hébergeur
compris) ; un badge signale les informations encore à compléter. **Ce sont des bases de travail :
fais-les relire par ton expert-comptable ou un juriste**, puis adapte le texte dans les réglages.

### Comptabilité
Fileservice → *Chiffre d'affaires & comptabilité* : synthèse par mois et **export CSV** des factures
d'une période (s'ouvre directement dans Excel) à transmettre au comptable.

### Sauvegardes
La base du fileservice est copiée dans `data/backups/fileservice-….db` au démarrage du portail puis
chaque jour (30 copies). **Inclus `data/backups/` et `data/fileservice_fichiers/` dans ta sauvegarde
externe** (OneDrive, disque USB) : une copie sur le même disque ne protège pas d'une panne du PC.
Pour restaurer : arrête le portail et l'outil, remplace `data/fileservice.db` par la copie voulue.

### RGPD
Le client télécharge ses données depuis *Paramètres → Mes données*. Sur demande de suppression :
onglet Clients → *Supprimer le compte* (tape SUPPRIMER) : coordonnées, véhicules, messages et
fichiers sont effacés ; les factures restent (obligation de conservation de 10 ans).

### Livraison en un clic
Sur une demande, **⚡ Livrer en un clic** prépare le fichier à partir de la bibliothèque et le livre
après confirmation. C'est proposé uniquement si chaque prestation commandée a une fiche de **même
stock**, sans prestation en trop, avec un patch « propre » et des checksums prêts ; sinon la raison
s'affiche et tu traites à la main (Auto-patch). Les prestations reliées à la bibliothèque sont
indiquées par le champ `type` dans `catalogue.py` (Stage 1, E85 / Flexfuel, DTC off, Vmax off).
Réglages → *Livraison automatique* : même logique, dès la réception du fichier (désactivée par défaut).

### Comptes atelier et journal
Fileservice → *Équipe de l'atelier* : crée ton compte administrateur, puis ceux des techniciens.
Dès le premier compte, l'outil demande un identifiant ; les messages sont signés automatiquement et
chaque action est tracée dans le *Journal des actions*. Les techniciens ne peuvent pas toucher aux
crédits, factures, suppressions ni réglages.

### Alertes
Bouton **🔔 Alertes** (onglet Fileservice) : autorise les notifications Windows. Son + notification à
chaque nouveau fichier, message client ou inscription. Sur le réseau atelier (adresse IP, sans
HTTPS), le navigateur n'autorise que le son.

### Remises et relances
Réglages → *Remises par niveau* (le niveau d'un client se choisit dans l'onglet Clients) et
*Relances automatiques* (solde bas, fichier non téléchargé), vérifiées toutes les heures.

### API revendeurs
Réglages → *API revendeurs* pour l'activer. Chaque client génère ses clés dans *Paramètres → API
revendeur*, où il trouve aussi la documentation (exemples `curl`). Même règles que le site : prix
recalculé, remise, crédits débités, livraison automatique éventuelle.

### Langue
L'espace client existe en français et en anglais (sélecteur FR / EN en haut de page). La langue est
mémorisée par client et ses e-mails partent dans cette langue. Les textes sont dans `traductions.py`.

### Option express
Fileservice → Réglages → **Option express** : supplément en crédits (réglable). Les demandes express
passent en tête de la file « À traiter » avec un badge ⚡ ; le supplément est remboursé en cas de refus.

### Fichiers complémentaires
Le client peut joindre jusqu'à 4 fichiers en plus de la lecture d'origine (EEPROM, boîte, sauvegarde…),
sur le site comme par l'API (`annexes`). Ils apparaissent en 📎 sur la demande dans l'outil.

### Utilisateurs d'un compte client
Dans ses **Paramètres**, le titulaire invite ses techniciens (lien par e-mail, 7 jours) : même solde,
connexion propre, demandes signées « envoyé par … ». L'achat de crédits et les factures leur sont
fermés sauf autorisation ; réglages du compte, API et export RGPD restent au titulaire. Les e-mails
« fichier prêt » partent au titulaire **et** à la personne qui a envoyé la demande.

### Alertes SMS
Fileservice → Réglages → **Alertes SMS** : compte Brevo (clé API) ou Twilio (Account SID + Auth Token),
expéditeur, bouton de test. Chaque client active les SMS et son mobile dans ses Paramètres ; il reçoit
un SMS quand un fichier est prêt ou qu'une précision est demandée (coût facturé par le fournisseur).

### Statistiques et réponses types
Fileservice → **Statistiques** : chiffre d'affaires HT, demandes et délai de livraison par mois,
prestations les plus demandées, meilleurs clients (6, 12 ou 24 mois, vue tableau).
Fileservice → **Réponses types** : messages préenregistrés (champs `{contact}`, `{numero}`,
`{vehicule}`…) à insérer dans la conversation depuis la liste « Réponse type… », relus avant envoi.

### Double authentification et état du service
Fileservice → **Équipe** : chaque compte atelier peut activer un code à 6 chiffres (Google/Microsoft
Authenticator, 2FAS) avec 8 codes de secours ; un administrateur peut la rendre obligatoire.
Fileservice → **État du service** : sauvegardes, e-mails non partis, espace disque, tâches planifiées.
Réglages → **Sauvegarde externe** : copie quotidienne par FTPS ou e-mail. Récapitulatif PDF de chaque
demande (bouton « Récapitulatif » côté atelier et client).

### Application mobile (PWA) et notifications
En HTTPS, l'espace client s'installe comme une application (Android, iPhone iOS 16.4+, ordinateur) : icône,
plein écran, page d'attente hors connexion. Dans **Paramètres → Application et notifications**, chaque client
(et chaque utilisateur rattaché) active les notifications sur son appareil : fichier prêt, message ou précision
demandée, refus. Envoi gratuit par le standard Web Push (paquet `cryptography`), en plus des e-mails et SMS.
Dans l'outil, 📲 sur une demande indique que le client a l'application.

### Passerelle PC atelier
La bibliothèque et ses fichiers .bin peuvent rester sur le PC (OneDrive) : l'onglet **En ligne** du Carto Matcher
du PC se connecte au fileservice en ligne avec une clé (Fileservice → Passerelle PC atelier), prépare les fichiers
avec la bibliothèque locale et renvoie seulement le fichier modifié. Livraison automatique possible.
Depuis la v1.59, le PC envoie aussi sa **liste de solutions** (base .db seule) à l'outil en ligne, et « ⬇ Solution »
en ligne **demande le fichier au PC** (effacé du serveur après téléchargement). Voir DEPLOIEMENT_O2SWITCH.md, section 11.

### Mise à jour du logiciel
**Fileservice → Mise à jour du logiciel** (administrateurs) : recherche sur GitHub, installation d'un clic
(empreinte SHA-256 vérifiée, dépendances installées, essai de démarrage, sauvegarde du code, redémarrage
automatique chez l'hébergeur), retour à la version précédente, installation depuis un fichier .zip, option
d'installation automatique la nuit. En local, relance ensuite les deux lanceurs .bat.

### Tarifs
Catalogue, packs, garanties et prix des packs de crédits : `catalogue.py` (prix en crédits,
1 crédit = 2,50 € HT, TVA 20 %). Le prix de chaque demande est toujours recalculé par le serveur.
Un client pro d'un autre pays de l'UE avec un numéro de TVA est facturé HT (autoliquidation).

### E-mails (O2switch)
Dans cPanel O2switch → *Comptes de messagerie*, crée une boîte (ex. `fileservice@ton-domaine.fr`),
puis *Connecter les appareils* pour lire le **serveur SMTP**. Renseigne serveur, port **465**,
adresse complète et mot de passe dans **Clients → E-mails (SMTP)**.
Tant que rien n'est réglé, les e-mails sont écrits dans `data/mails_non_envoyes.log`.

### Paiement en ligne (Stripe)
1. Crée un compte Stripe. Commence en **mode test** (clé `sk_test_…`, carte `4242 4242 4242 4242`).
2. Développeurs → *Clés API* : copie la **clé secrète** dans Fileservice → Réglages.
3. Développeurs → *Webhooks* → ajoute `https://<adresse publique>/stripe/webhook`, événements
   `checkout.session.completed` et `checkout.session.async_payment_succeeded` ; copie le
   **secret de signature** (`whsec_…`) dans les réglages.
4. Le client clique *Acheter* sur un pack, paie sur la page Stripe, revient : crédits et facture
   sont ajoutés (une seule fois, même si Stripe renvoie l'événement). Si le montant payé ne
   correspond pas au prix du pack, rien n'est crédité et l'atelier reçoit une alerte.
5. Passe en clé `sk_live_…` quand tout fonctionne.

Clés Stripe et mot de passe SMTP sont stockés dans `data/portal_config.json`, jamais dans git, et
ne sont jamais renvoyés au navigateur.
