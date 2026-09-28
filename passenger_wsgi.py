"""
Point d'entrée de l'ESPACE CLIENT pour un hébergeur Python (O2switch : cPanel → « Setup Python App »,
fichier de démarrage passenger_wsgi.py, point d'entrée « application »). Guide : DEPLOIEMENT_O2SWITCH.md
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CARTO_PROD", "1")

import portal  # noqa: E402
from matcher import db  # noqa: E402

db.init_db(portal.DB_PATH)
application = portal.app
