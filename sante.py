"""
État de santé du fileservice : ce qui doit alerter l'atelier.

  - /sante (portail, public)   : 200 si le portail et sa base répondent, 503 sinon —
                                 à surveiller par un service externe (UptimeRobot…) ;
  - /fs/sante (outil atelier)  : le détail des problèmes ;
  - taches.py                  : envoie un e-mail à l'atelier quand un problème apparaît.
"""
import datetime as dt
import os
import shutil

import mailer
import sauvegarde_externe
from comptes import connect, dernieres_sauvegardes

DISQUE_MIN = 500 * 1024 * 1024
SAUVEGARDE_MAX_H = 26


def base_ok(db_path):
    try:
        with connect(db_path) as con:
            con.execute("SELECT COUNT(*) FROM clients").fetchone()
        return True
    except Exception:
        return False


def _age_h(date_txt, maintenant):
    try:
        return (maintenant - dt.datetime.strptime(date_txt[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def verifier(db_path, data_dir, cfg, maintenant=None):
    """Renvoie {ok, problemes: [texte], avertissements: [texte]}."""
    maintenant = maintenant or dt.datetime.now()
    problemes, avert = [], []
    if not base_ok(db_path):
        problemes.append("La base du fileservice ne répond pas.")
    try:
        libre = shutil.disk_usage(data_dir).free
        if libre < DISQUE_MIN:
            problemes.append(f"Espace disque faible : {libre // (1024 * 1024)} Mo libres.")
    except OSError:
        pass
    sauv = dernieres_sauvegardes(db_path, 1)
    if os.path.isfile(db_path):
        if not sauv:
            problemes.append("Aucune sauvegarde de la base.")
        else:
            n = sauv[0]["nom"]   # fileservice-AAAAMMJJ-HHMMSS-…
            age = _age_h(f"{n[12:16]}-{n[16:18]}-{n[18:20]} {n[21:23]}:{n[23:25]}:{n[25:27]}", maintenant)
            if age is not None and age > SAUVEGARDE_MAX_H:
                problemes.append(f"Dernière sauvegarde il y a {round(age)} h (tâche planifiée arrêtée ?).")
    ext = sauvegarde_externe.reglages(cfg)
    if ext["mode"]:
        der = sauvegarde_externe.derniere(data_dir)
        if not der:
            avert.append("Sauvegarde externe configurée mais jamais exécutée.")
        elif not der.get("ok"):
            problemes.append(f"Sauvegarde externe en échec : {der.get('message')}")
        elif (_age_h(der.get("date"), maintenant) or 0) > SAUVEGARDE_MAX_H:
            problemes.append("Sauvegarde externe pas faite depuis plus d'un jour.")
    else:
        avert.append("Pas de sauvegarde externe : une panne du serveur ferait tout perdre.")
    ech = mailer.echecs(data_dir)
    if ech:
        problemes.append(f"{len(ech)} e-mail(s) non envoyé(s) — dernier : {ech[0]['erreur']}")
    if not mailer.configure(cfg.get("smtp") or {}):
        avert.append("Envoi d'e-mails non configuré (onglet Clients → E-mails).")
    return {"ok": not problemes, "problemes": problemes, "avertissements": avert}
