# Mise en ligne sur O2switch

Ce guide publie **l'espace client** et **l'outil atelier** sur votre hébergement O2switch, en HTTPS, chacun sur son sous-domaine :

| Application | Adresse (exemple) | Point d'entrée |
|---|---|---|
| Espace client (portail) | `https://fichiers.e85-france.fr` | `wsgi_portail.py` |
| Outil atelier | `https://atelier.e85-france.fr` | `deploy/atelier/wsgi_atelier.py` |

Les deux applications partagent le même dossier `data/` (comptes, demandes, factures, bibliothèque de solutions).

> **Durée** : environ 1 h la première fois. Gardez ce guide ouvert à côté de cPanel.

---

## 1. Préparer les sous-domaines

1. cPanel → **Domaines** → créez `fichiers.e85-france.fr` et `atelier.e85-france.fr`.
2. cPanel → **Lets Encrypt™ SSL** (ou AutoSSL) → générez un certificat pour les deux sous-domaines.

## 2. Déposer le logiciel

Le dossier du logiciel doit être **hors de `public_html`** (les données ne doivent jamais être accessibles directement).

- **Par Git (recommandé)** : cPanel → **Git™ Version Control** → *Create* → clonez le dépôt GitHub dans `/home/VOTRE_COMPTE/carto_matcher`.
- **Ou par archive** : cPanel → **Gestionnaire de fichiers** → créez `carto_matcher` dans votre dossier personnel, envoyez le zip de la release, puis *Extraire*.

Envoyez ensuite votre bibliothèque existante (`solutions.db` et le dossier des fichiers de solutions) dans `carto_matcher/data/`.

## 3. Créer l'application « Espace client »

cPanel → **Setup Python App** → *Create Application* :

| Champ | Valeur |
|---|---|
| Python version | la plus récente proposée (3.11 ou plus) |
| Application root | `carto_matcher` |
| Application URL | `fichiers.e85-france.fr` |
| Application startup file | `wsgi_portail.py` |
| Application Entry point | `application` |

Variables d'environnement (*Add Variable*) :

| Nom | Valeur |
|---|---|
| `CARTO_PROD` | `1` |
| `CARTO_PUBLIC_URL` | `https://fichiers.e85-france.fr` |

*Create*, puis en bas de la page : **Configuration files** → `requirements.txt` → *Run Pip Install*. Enfin *Restart*.

## 4. Créer l'application « Outil atelier »

Même écran, seconde application :

| Champ | Valeur |
|---|---|
| Application root | `carto_matcher/deploy/atelier` |
| Application URL | `atelier.e85-france.fr` |
| Application startup file | `wsgi_atelier.py` |
| Application Entry point | `application` |
| Variable `CARTO_PROD` | `1` |

Installez Flask dans cet environnement : en haut de la page de l'application, copiez la commande *« Enter to the virtual environment »*, collez-la dans cPanel → **Terminal**, puis :

```
pip install -r ~/carto_matcher/requirements.txt
```

*Restart*.

## 5. Créer le premier compte administrateur

En ligne, l'outil atelier n'a **jamais** d'accès libre : tant qu'aucun compte n'existe, il affiche une page d'installation. Dans **Terminal**, entrez dans l'environnement de l'application « Espace client » (commande copiée en haut de sa page), puis :

```
cd ~/carto_matcher
python outils_prod.py admin
python outils_prod.py verifier
```

Connectez-vous ensuite sur `https://atelier.e85-france.fr`.

## 6. Sécuriser l'outil atelier

Onglet **Fileservice → Équipe de l'atelier** :

1. **Double authentification** → *Activer sur mon compte* → scannez le QR code avec Google Authenticator, Microsoft Authenticator ou 2FAS → saisissez le code → **notez les 8 codes de secours**.
2. Cochez **« Obliger tous les comptes de l'atelier à l'activer »**.
3. Créez les comptes des techniciens : ils activeront leur double authentification à la première connexion.

Téléphone perdu : un administrateur clique sur **« Retirer la 2FA »** du compte concerné.

## 7. Régler le fileservice

Dans l'outil atelier :

- **Clients → E-mails** : serveur `mail.e85-france.fr` (ou celui indiqué dans cPanel → Comptes de messagerie → *Connect Devices*), port **465**, adresse et mot de passe de la boîte, adresse de l'atelier pour les notifications, **adresse publique** `https://fichiers.e85-france.fr`. Bouton *Tester*.
- **Fileservice → Réglages** : identité légale, horaires, pages légales, **Stripe** (webhook : `https://fichiers.e85-france.fr/stripe/webhook`).
- **Fileservice → Réglages → Sauvegarde externe** : FTP (un NAS, un autre hébergement, un stockage en ligne) ou e-mail. Bouton *Enregistrer et tester maintenant*.

## 8. Tâches planifiées (obligatoire)

Chez un hébergeur, le portail ne tourne pas en continu : ce sont des **tâches cron** qui font les sauvegardes, les relances et les alertes.

cPanel → **Tâches Cron** → *Ajouter une tâche* → réglage commun **« Une fois par heure »** → commande (remplacez `VOTRE_COMPTE` et la version de Python par ceux affichés dans *Setup Python App*) :

```
cd /home/VOTRE_COMPTE/carto_matcher && /home/VOTRE_COMPTE/virtualenv/carto_matcher/3.11/bin/python outils_prod.py taches > /dev/null 2>&1
```

Chaque heure : sauvegarde du jour (30 gardées), sauvegarde externe quotidienne, relances clients, et **e-mail à l'atelier** dès qu'un problème apparaît (sauvegarde en échec, e-mails qui ne partent plus, disque presque plein…). L'état est visible dans **Fileservice → État du service**.

## 9. Surveillance externe (5 minutes, gratuit)

Si le serveur tombe, il ne peut pas vous prévenir lui-même. Créez un compte gratuit sur [UptimeRobot](https://uptimerobot.com) → *Add New Monitor* → type **HTTP(s)** → adresse `https://fichiers.e85-france.fr/sante` → toutes les 5 minutes → alerte par e-mail (ou SMS / application mobile).

La page `/sante` répond 200 quand tout fonctionne et 503 si la base ne répond plus ; elle ne donne aucun détail.

## 10. Application mobile et notifications (clients)

Rien à installer côté serveur : dès que le site est en HTTPS, l'espace client est une **application installable**.
Le client ouvre `https://fichiers.e85-france.fr` sur son téléphone, puis :

- **Android / ordinateur** (Chrome, Edge) : bouton « Installer l'application » dans *Paramètres*, ou menu du navigateur → *Installer* ;
- **iPhone** (iOS 16.4 ou plus) : Safari → *Partager* → *Sur l'écran d'accueil*, puis ouvrir l'application depuis l'icône.

Dans *Paramètres → Application et notifications*, il active les notifications : fichier prêt, message ou précision
demandée par l'atelier, refus. C'est gratuit (standard Web Push). Les clés d'envoi sont créées automatiquement
au premier usage et gardées dans `data/portal_config.json`.

## 11. Mettre à jour le logiciel

À partir de la v1.57, tout se fait depuis l'outil atelier (administrateur) : **Fileservice → Mise à jour du logiciel**.

- **Rechercher une mise à jour** interroge GitHub ; **Installer** télécharge la release, vérifie son empreinte SHA-256,
  installe les nouvelles dépendances (dans les environnements Python des **deux** applications), démarre la nouvelle
  version à l'essai, sauvegarde le code actuel, puis remplace les fichiers et redémarre les applications.
  Si une étape échoue, rien n'est modifié.
- **Revenir à la version précédente** : un clic (les 3 dernières versions sont gardées dans `data/mises_a_jour/`).
- **Installer depuis un fichier .zip** : la même chose avec une release téléchargée à la main.
- **Réglages** : dépôt GitHub, jeton d'accès si le dépôt est privé (lecture seule), et option **installation automatique
  la nuit** (sinon l'atelier reçoit un e-mail quand une version sort et installe d'un clic).
- Installé avec **Git** (cPanel → Git™ Version Control) : la mise à jour passe sur l'étiquette de la version
  (`git fetch` + `checkout`). N'utilisez plus alors le bouton *Pull* de cPanel.

Pour passer d'une version **antérieure à la 1.57** : une dernière fois à la main (*Pull* Git, ou zip extrait par-dessus,
le dossier `data/` n'est jamais dans l'archive), puis *Restart* des deux applications et, dans chacune,
*Run Pip Install* (la 1.57 ajoute le paquet `cryptography` pour les notifications).

## Bon à savoir

- **Bibliothèque de solutions** : sur le serveur, l'import « choisir un dossier » ne peut pas ouvrir votre PC. Envoyez le dossier par FTP dans `carto_matcher/data/…` puis importez-le en indiquant ce chemin, ou continuez d'enrichir la bibliothèque sur le PC de l'atelier et envoyez `solutions.db` mise à jour.
- **Taille des fichiers** : 64 Mo par envoi, comme en local.
- **Ne jamais publier** `data/` (comptes, factures, mots de passe SMTP/Stripe) : il est déjà exclu de Git et de l'archive de release.
- En local, rien ne change : `python portal.py` et `python app.py` fonctionnent comme avant (le portail lance lui-même les tâches toutes les heures).

## Dépannage

**« We're sorry, but something went wrong » et, en Terminal, `RecursionError` avec `imp.load_source('wsgi', 'passenger_wsgi.py')`** :
cPanel a remplacé `passenger_wsgi.py` par son modèle, qui charge le « fichier de démarrage »… c'est-à-dire lui-même.
Dans *Setup Python App* → *Edit*, mettez **Application startup file** = `wsgi_portail.py` (espace client) ou
`wsgi_atelier.py` (outil atelier), enregistrez puis **Restart**. Le passenger_wsgi.py de cPanel chargera alors le bon fichier.

**« Passenger error #2 … Passengerfile.json … Permission denied (errno=13) »** : Apache n'a pas le droit de lire le
dossier du logiciel (dossier créé ou décompressé avec des droits trop stricts). Dans cPanel → **Terminal** :

```
cd ~/carto_matcher          # ou le nom de votre dossier (ex. carto-matcher)
python3 outils_prod.py droits
```

(ou, sans ce script : `chmod 755 ~/carto_matcher`, puis dossiers en 755 et fichiers en 644 ; `data/` peut rester en 700).
Puis *Setup Python App* → **Restart** sur les deux applications.

**Le dossier ne s'appelle pas `carto_matcher`** : remplacez le nom partout dans ce guide, y compris dans le chemin
de l'environnement Python de la tâche cron (`~/virtualenv/<nom du dossier>/<version>/bin/python`).
