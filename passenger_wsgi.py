"""
Compatibilité : si cPanel utilise passenger_wsgi.py comme fichier de démarrage, on charge le vrai point
d'entrée. (Réglage conseillé : fichier de démarrage « wsgi_portail.py » — voir DEPLOIEMENT_O2SWITCH.md.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wsgi_portail import application  # noqa: E402,F401
