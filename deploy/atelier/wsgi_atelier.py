"""
Point d'entrée de l'OUTIL ATELIER pour un hébergeur Python (O2switch : seconde application,
racine = ce dossier deploy/atelier, sur un sous-domaine distinct, ex. atelier.votredomaine.fr).
Fichier de démarrage à indiquer dans cPanel : wsgi_atelier.py (point d'entrée « application »).
Accès protégé par les comptes atelier + double authentification. Guide : DEPLOIEMENT_O2SWITCH.md
"""
import os
import sys

RACINE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, RACINE)
os.chdir(RACINE)
os.environ.setdefault("CARTO_PROD", "1")

import app as outil  # noqa: E402
from matcher import db  # noqa: E402

db.init_db(outil.DB_PATH)
application = outil.app
