# Changelog — Carto Matcher

La version est affichée dans l'en-tête de l'interface (à côté de « local · hors-ligne »)
et dans la fenêtre noire au démarrage.

## v1.55.0
Productivité de l'atelier et développement commercial.

**Atelier**
- **Livraison en un clic** (« ⚡ Livrer en un clic » sur chaque demande) : le fichier est préparé depuis
  la bibliothèque et livré quand chaque prestation a une fiche de même stock, qu'aucune fiche n'ajoute
  de prestation non commandée, que le patch est « propre » et les checksums prêts (pas de RSA).
  Sinon, la raison est affichée et la demande se traite à la main. Option **livraison automatique
  dès la réception** (désactivée par défaut).
- **Alertes en direct** : son, notification Windows, compteurs d'onglets et titre de page à chaque
  nouveau fichier, message client ou inscription (vérification toutes les 20 s).
- **Comptes atelier** : un identifiant par technicien (administrateur / technicien), signature
  automatique des messages, actions sensibles (crédits, factures, suppressions, réglages) réservées
  aux administrateurs, **journal des actions** consultable. Sans compte créé, l'outil fonctionne
  comme avant.

**Commercial**
- **Remises par niveau de client** (Standard, Partenaire, VIP… réglables) appliquées côté serveur sur
  les prestations, affichées au client.
- **Relances automatiques** : solde bas, fichier prêt non téléchargé (une fois par événement).
- **API revendeurs** (`/api/v1`) : clés générées par le client, catalogue, devis, envoi de fichier,
  suivi, téléchargement, messages ; activée par l'atelier, documentée dans l'espace client.
- **Espace client en anglais** : sélecteur FR / EN, langue mémorisée, e-mails envoyés dans la langue
  du client. Les pages légales restent dans la langue saisie par l'atelier.

## v1.54.1
- **L'espace client est à la racine du portail** : `https://portail.ton-domaine.fr/` ouvre directement
  la connexion / le tableau de bord (fini le `/espace`). Les anciens liens `/espace/…` (e-mails déjà
  envoyés, favoris) redirigent automatiquement ; l'ancienne adresse du webhook Stripe reste acceptée.
- L'ancienne page de vérification anonyme d'un fichier reste disponible sur `/verifier`.
- Le verrou optionnel du portail ne bloque plus l'espace client (qui a ses propres comptes).
- Outil interne : le lien « voir la page » des pages légales ouvre bien le portail.

## v1.54.0
Mise en conformité et exploitation du fileservice.

- **Pages légales** : CGV, mentions légales, politique de confidentialité, publiques sur
  `/espace/legal/…`, liées depuis l'inscription et le menu. Modèles pré-remplis avec l'identité
  légale, modifiables dans Fileservice → Réglages (à faire relire par un juriste).
- **Sauvegardes** de `data/fileservice.db` au démarrage du portail puis chaque jour (copie à chaud
  SQLite, 30 gardées dans `data/backups/`), bouton « Sauvegarder maintenant ».
- **Comptabilité** : synthèse mensuelle (factures, CA HT/TVA/TTC, crédits vendus et consommés,
  crédits en circulation) et **export CSV des factures** pour le comptable (Excel : « ; » et virgule).
- **RGPD** : le client télécharge ses données (Paramètres) ; l'atelier peut supprimer un compte
  (données personnelles et fichiers effacés, factures conservées 10 ans).
- **Sécurité** : l'outil interne refuse les écritures venant d'un autre site (une page piégée ne peut
  plus livrer un fichier ou créditer un client) ; en-têtes d'e-mail nettoyés.

## v1.53.0
Fileservice complet : espace client, traitement atelier, paiement et factures.

**Espace client** (`/espace` sur le portail, port 5001), aux couleurs E85-FRANCE (thème sombre / clair, mobile) :
- **Comptes pro** : inscription (SIRET vérifié, TVA, adresse), validation par l'atelier, connexion,
  « rester connecté », mot de passe oublié par e-mail, paramètres (coordonnées, mot de passe).
- **Envoi de fichier** : type de véhicule, véhicule, lecture, prestations du catalogue avec **tarif pack
  automatique**, ouverture au siège + retour du boîtier, Garantie Sérénité. Le prix est recalculé par le
  serveur ; crédits débités à l'envoi, dans la même transaction que la demande.
- **Suivi** : statuts (reçu, en traitement, info requise, prêt, refusé), étapes datées, versions livrées,
  **conversation avec pièces jointes**, révision gratuite 30 jours, remboursement automatique en cas de refus.
- **Crédits** : packs à 2,50 € HT/crédit avec bonus, prix **HT et TTC**, historique des mouvements.
- **Paiement en ligne Stripe** (optionnel) : Checkout, webhook signé, crédit idempotent, montant vérifié.
- **Factures** numérotées FA-AAAA-NNNN, identité vendeur/client figée, TVA 20 % ou **autoliquidation UE**,
  mentions légales, imprimables en PDF.
- **Support** : FAQ, contact, horaires d'ouverture et état ouvert / fermé.

**Outil interne** :
- Onglet **Fileservice** : file des demandes, détail, calculateur détecté et verdict bibliothèque,
  **Analyser**, **Auto-patch** direct, messages (avec « info requise »), **livraison** du fichier modifié,
  refus avec remboursement ; réglages (identité légale, horaires, clés Stripe).
- Onglet **Clients** : validation, blocage, niveau, crédits, **facture pour paiement hors ligne**
  (virement, chèque…), réglages SMTP O2switch avec test.
- E-mails automatiques : inscription, activation, info requise, fichier prêt, refus, facture ;
  notifications atelier (nouvelle inscription, nouveau fichier, message client, révision).
- Correctifs : la vue Recherche restait affichée sous les autres onglets ; la barre d'onglets passe
  à la ligne au lieu de déborder.

Sécurité : CSRF sur tous les formulaires, blocage après 5 échecs de connexion, aucun client ne peut
voir les fichiers d'un autre, fichiers rangés hors de la racine web avec contrôle des chemins, clés
(SMTP, Stripe) jamais renvoyées au navigateur.

## v1.52.0
File inbox, livré, cartes multi-SOL.

- Demandes : file **À packer** + bouton **Packer** / **Packer le suivant** (combo + ZIP + traité).
- Après un pack : proposer **Livré** sur le dossier et ouvrir le dossier de livraison.
- Cartes 2D multi-SOL : original + chaque presta sur la même grille.

## v1.51.0
Portail commande + garde-fou calibre + historique stock.

- Portail : cases à cocher sur les prestas, bouton **Commander**. La demande inbox porte les prestas voulues.
- Inbox : **Valider le combo** ouvre l'auto-patch déjà filtré sur cette commande.
- Combiner deux stocks / deux calibres → refusé (`stocks_melanges`), sauf `force_mix`.
- Recherche : bandeau **Déjà livré sur ce stock** (jobs / packs précédents).

## v1.50.0
Pack de sortie atelier.

- Bouton **Pack de sortie (ZIP)** : BIN + rapport TXT + rapport HTML.
- Nommage `AA-123-BB_E85_Stage1_FAP.bin` (immat, sinon 8 derniers du VIN, sinon nom du dump).
- Si un dossier client est rattaché : fichiers dans `data/dossiers/000123/livraisons/AAAAMMJJ-HHMMSS/`.
- Sinon : `data/livraisons/…`.
- Job atelier noté avec le chemin du pack. « Prêt à flasher » seulement si checksum clair, pas de RSA, pas de conflit.

## v1.49.0
Bloc E — auto-patch multi-prestas (le « 1-2-3 » NuoVolta).

- **Combiner** plusieurs fiches du même stock (Stage 1 + FAP + E85…) en un seul BIN.
- Cases à cocher sur les résultats de recherche + bouton **Combiner et auto-patch**.
- Conflit = même octet, deux valeurs différentes → octet **non écrit**, le reste passe.
- Recouvrement identique (même valeur) = pas un conflit.
- Routes `/patch/analyze_multi` et `/patch/apply_multi` (`ids=1,2,3`).
- RSA / « prêt à flasher » inchangés.

## v1.48.0
Bloc D (début) — dossier client.

- **Dossier véhicule** : client, téléphone, email, garage partenaire,
  immat (SIV `AA-123-BB` / FNI `1234 AB 31`), VIN, calculateur, prestations
  (puces Stage / FAP / EGR / SCR / E85…), notes, statut (ouvert → en cours
  → livré → archivé).
- **Dump archivé** dans le dossier (copie locale). Analyse (Recherche) et
  Auto-patch depuis la fiche, sans re-déposer le fichier.
- **Portail** : nom, immat, VIN en plus du véhicule / email / tél. Bouton
  **Dossier** sur une demande → fiche préremplie + dump copié.
- **Recherche / Auto-patch** : « Ouvrir un dossier client ». Un patch généré
  se rattache au dossier choisi (presta ajoutée, statut *en cours*).
- Les anciens patchs sans dossier restent listés en « non rattachés ».

## v1.47.0
Bloc C (1.45–1.47) — zones nommées, cartes 2D physiques, checksums honnêtes.

- **Zones nommées** (Auto-patch) : FAP/DPF, EGR, AdBlue/SCR, DTC, Stage
  (couple / limiteur), E85. Une fiche `Stage 1 + FAP` étiquette la table
  mise à 0 comme FAP, pas comme Stage. Puces couleur dans le rapport.
- **RSA / CSA** : un bloc PKCS#1 (`00 01 FF…FF 00`) est détecté, **jamais
  patché**, et le fichier n'est **jamais** dit « prêt à flasher » — même si
  les checksums additifs sont justes. Passe par WinOLS.
- **Cartes 2D** : facteur Z + offset + unité (affichage physique, pas de
  Damos). Export **CSV** de la carte courante, ou de toutes les cartes Δ
  (original / solution / delta, séparateur `;` Excel FR).
- **Checksums étendus** (seulement si le schéma est *clair*, ≥ 70 % des
  blocs) : additif Bosch en fin **ou en tête** de bloc (256 o → 32 Ko),
  CRC-16 CCITT et IBM. MD1/MG1 restent additifs ; la RSA bloque le flash.

## v1.44.0
Bloc B (1.41–1.44) — matching métier.

- **Recherche par presta** : les types composés (`Stage 1 + FAP + SCR`) sont
  des atomes. Un dump `…FAP_off.bin` fait remonter le FAP avant le Stage 1.
  Puces de filtre au-dessus des résultats.
- **Même stock / même calibration** : toutes les cartos de ce véhicule
  ressortent (Stage 1 **et** FAP **et** E85), plus seulement la première.
- **Portail** : devis multi-lignes (Stage 1, FAP, AdBlue séparés), tarif
  + délai. Alias `DPF off` = `DPF/FAP off`. « Origine » n'est plus une offre
  s'il y a une vraie presta.
- Familles chemin / binaire : **CPEGD (Kefico)**, **BEM (Hitachi)**,
  **E6T / 8GM (Marelli)**, ACDelco, Cummins.

## v1.40.0
Bloc A (1.38–1.40) — la base devient utilisable une fois CARTOS pointé.

- **File atelier** (Tableau de bord) : lots de 12 fiches, on reprend si ça
  s'arrête. Pour chaque BIN trouvé : copie dans `data/files/`, empreinte
  MinHash v2, version ECU / plateforme / fabricant lus dans le fichier
  (Bosch 10 chiffres, Denso `xxxxx-xxxxx`… — pas un numéro générique).
  Les champs déjà remplis ne sont pas écrasés.
- **Nettoyer** : aperçu puis application. Supprime les `.cache` / merdasse
  (suspects), fusionne les vrais doublons (même binaire + même type, on
  garde la fiche la plus complète), rattache une solution orpheline si le
  dossier de l'original contient un autre BIN.
- Après **Appliquer** sur le dossier CARTOS, propose de lancer la file.

## v1.37.0
- **E85 / Flexfuel** : un fichier `E85France-…` ou `23%` dans le *nom* est une
  vraie presta (dossier REFERENCE, voitures). Le tampon `E85France` sur un
  FAP/EGR/SCR (poids lourds) n'ajoute plus Flexfuel. Le dossier OneDrive
  « E85 » reste ignoré.
- **Pointer le dossier CARTOS** (Tableau de bord) : réécrit
  `D:\OneDrive E85\OneDrive\CARTOS\…` vers le dossier de *cette* machine.
  Tester d'abord (compte les BIN trouvés), puis Appliquer — la base est
  sauvegardée. Auto-patch et Cartes 2D retrouvent alors les fichiers.
- Plateformes chemin : CM2150 / CM2350 (Cummins), A6E11, BEM, CPEGD.

## v1.36.0
- **Relire les fiches** (onglet Solutions) : type, plateforme, fabricant et
  libellé sont relus depuis les chemins (dossier OneDrive + nom du fichier
  solution). Les champs déjà remplis ne sont pas écrasés.
- **Claas Jaguar ≠ Jaguar voiture** : une marque agri/PL dans le chemin gagne.
- **Plateformes-bruit** (`dMe`, `me0m`, `EDC17CV41-EP-`, `EDC16.A000`) nettoyées.
  ADEM4 / ACM / MCM / MD1 / EDC17CVxx lus dans le nom du dossier.
- **Type** : `SCR_OFF`, `ST1`, `ORI` ; « Origine (stock) » est retiré si une
  vraie prestation (FAP / EGR / SCR / Stage) est aussi dans le nom.
  `9.3 CAT` (Caterpillar) n'est plus pris pour un Decat.
- Les dossiers `.cache` ne sont plus importés. Les fiches `.cache` déjà en
  base sont étiquetées `suspect`.

## v1.35.1
- **Importer une base `.db`** (Tableau de bord → Sauvegardes) : on dépose une
  sauvegarde (même d'une ancienne version, même d'un autre PC). La base
  actuelle est d'abord copiée dans `data/backups/`, le schéma est migré
  (`minhash_ver`, index…). Plus besoin de coller le fichier à la main.
- Les chemins des BIN (OneDrive, `D:\…`) ne sont pas réécrits : le matching
  (sha256 / MinHash / calibration) fonctionne sans les fichiers ; Auto-patch
  et Cartes 2D les attendent sur le disque.

## v1.35.0
- **Cartes 2D (Kennfeld)** : l'onglet Visualiseur devient **Cartes 2D**.
  Détection locale des tables 1D / 2D (axes 16 bits LE, Bosch-like : X collé à Y
  collé à Z). Grille colorée façon WinOLS, axes en en-tête, valeurs brutes.
- **Comparaison original / solution** : les cellules modifiées sont surlignées
  (ancienne valeur barrée + nouvelle). Liste à gauche, les cartes Δ en premier.
- **Réglage manuel** : offset, lignes × colonnes, bits, endian, signé, inversion
  X/Y — si la détection se trompe, tu cadres comme dans WinOLS.
- **Auto-patch → 2D** : si une zone de diff *est* une table (axes retrouvés
  juste avant), bouton **2D r×c** dans le rapport.
- Headers programmeur retirés avant le scan (offsets du dump complet).
- Pas de Damos : pas de facteur / unité. Valeurs brutes, volontairement.

## v1.34.0
- **Auto-patch 100 % serveur** : plus de téléchargement des deux BIN (jusqu'à 32 Mo × 2)
  dans le navigateur. Analyse (`/patch/analyze`) et génération (`/patch/apply`) tournent
  côté atelier. La prévisualisation n'écrit pas de dossier.
- **Headers programmeur** (KESS, Autotuner, PCMFlash, Alientech, CMD, MPPS…) : détectés
  et retirés (0x100–0x2000) pour comparer / patcher le *corps* ECU, puis recollés sur
  le fichier client. Un KESS +0x400 et un Autotuner du même ECU se patchent.
- **MinHash v2** : les blocs de padding (≥ 90 % de 00 ou FF) sont ignorés — deux EDC17
  différentes bourrées de 0xFF ne ressortent plus « binaires similaires ». Les fiches
  v1 restent comparables (le moteur calcule les deux versions). Bouton **Empreintes v2**
  (Solutions / Tableau de bord) pour recalculer les anciennes.
- **Archive locale `data/files/<id>/`** : original + solution copiés à l'import /
  enregistrement. Les liens ne cassent plus si OneDrive déplace le dossier source.
  Supprimer une fiche enlève aussi son archive (jamais le fichier d'origine).
- **Checksums Bosch additifs** (blocs 2 / 8 / 16 / 32 Ko, somme 16 bits LE directe ou
  complément) : corrigés automatiquement si le schéma est *clair*. Statuts
  `ok` / `corrige` / `inconnu` / `non_applicable`. **Jamais** « prêt à flasher » si
  inconnu (RSA / CSA non touchés — passe par WinOLS).
- Matching : sha256 du *corps* (sans header) reconnu comme fichier identique ;
  tailles compatibles à un header connu près.

## v1.33.0
- **Matching plus strict, portail plus honnête** :
  - plus de match par *sous-chaîne* d'identifiant (`"103"` dans `"1037551299"`) ;
  - similarité binaire uniquement si les tailles sont quasi identiques (±2 %), plus ±25 % ;
  - plateforme / libellé véhicule restent des indices **faibles** (score < 0,45) — ils ne
    déclenchent plus un « à vérifier » côté client ;
  - le portail décide avec des règles dédiées, pas avec `score ≥ 0,6` : *compatible*
    = sha256 identique **ou** calibration exacte + même taille ; *à vérifier* =
    calibration exacte (taille différente, ex. header) **ou** MinHash ≥ 85 % +
    même taille + même plateforme. Le reste = *non trouvé*.
- **Plusieurs prestations par original** : l'import crée une fiche par *type*
  détecté dans le dossier (Stage 1, FAP off, EGR off, E85…). Un doublon n'est
  plus « même sha256 » mais « même sha256 + même type ». Stage 1 + FAP off sur
  le même ORI cohabitent.
- **LAN multi-postes** : la dernière analyse (Enregistrer / Assistant) est
  liée à la *session* du navigateur, plus à une variable globale partagée.
- **`seed_demo.py` ne touche plus `data/solutions.db`** : écrit dans
  `data/demo.db` et refuse d'écraser une base qui contient déjà des fiches.
- **SQLite WAL + timeout 8 s** : import + portail en parallèle ne se marchent
  plus dessus (`database is locked`).
- **Sécurité** : plafond 64 Mo aussi sur l'outil interne, redirection `next`
  bornée, XSS échappée (résultats + portail), `X-Forwarded-For` ignoré sauf
  `CARTO_TRUST_PROXY=1`, cookie de session HttpOnly/SameSite.
- **Métier E85 / agricole / PL** : mots-clés E85 / flexfuel / éthanol, marques
  Claas, Fendt, Massey, Deutz, Kubota, Scania, MAN, John Deere, Komatsu…,
  plateformes SIM266 / SIM27x / Keihin. Les Damos (`.dam`) ne sont plus
  importés comme des dumps flash.

## v1.32.0
- **Badge « Demandes » en temps réel** : le compteur de demandes clients non
  traitées se rafraîchit automatiquement (toutes les 30 s) — un dépôt sur le
  portail apparaît sans avoir à recharger la page.
- **Portail prêt pour la mise en ligne** :
  - serveur de production **waitress** + lanceur dédié « Lancer le portail
    client (production).bat » (pare-feu inclus) ;
  - **limitation anti-spam** : 10 dépôts/heure par adresse (réglable via
    `max_depots_heure` dans portal_config.json, 0 = désactivé), compatible
    reverse proxy/tunnel (X-Forwarded-For) ;
  - guide complet « Mise en ligne du portail » dans le README (tunnel
    Cloudflare recommandé, ou reverse proxy), avec les précautions.
- Correctif : le numéro de version du portail était resté bloqué à 1.28.0
  dans sa bannière de démarrage.


## v1.31.0
- **Onglet « Demandes clients »** : les fichiers déposés sur le portail apparaissent
  maintenant directement dans l'outil interne — date, verdict (compatible / à
  vérifier / non trouvé), calculateur détecté, coordonnées laissées par le client.
  Pour chaque demande : **Analyser** (recherche immédiate des solutions en base,
  résultat affiché sous la demande), **Télécharger** le fichier déposé,
  **Marquer traité** / Rouvrir, **Supprimer**. Un badge sur l'onglet indique le
  nombre de demandes non traitées. La boucle portail → atelier est fermée :
  plus aucun dépôt ne peut passer inaperçu.

## v1.30.1
- **Correctif d'affichage** : la fenêtre Réglages ne défilait plus depuis l'ajout
  du Verrou d'accès et de la personnalisation du Portail client (contenu trop
  long, pas de scroll). Ajout d'une hauteur maximale avec défilement interne.

## v1.30.0
- **Correctif d'étiquette Mercedes/MCM** : sur un module MCM confirmé (ex. Claas
  Jaguar / OM471), le numéro à 10 chiffres autrefois étiqueté « Numéro Bosch (HW) »
  est maintenant reconnu comme « Numéro Mercedes (pièce) » — Detroit/MCM n'utilise
  jamais de calculateur Bosch, ce numéro est bien une référence Mercedes
  (00xxxxxxxx, la forme sans le préfixe A des références A0xxxxxxxx). N'affecte
  que les fichiers déjà identifiés comme Mercedes/Detroit ; aucun changement sur
  les autres familles (Bosch, Bosch/Siemens, Denso, Hitachi, Continental).

