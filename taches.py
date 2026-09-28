"""
Tâches planifiées du fileservice, lancées toutes les heures :
  - par le portail lui-même (python portal.py : fil en arrière-plan) ;
  - ou par une tâche cron chez l'hébergeur (O2switch : python outils_prod.py taches).

Chaque tâche note sa dernière exécution dans data/taches.json : lancer les deux à la fois
ne fait rien en double.
"""
import datetime as dt
import json
import os
import time

import comptes
import mailer
import mise_a_jour
import sante
import sauvegarde_externe

ETAT = "taches.json"
JOUR = 23 * 3600        # « chaque jour », avec une marge pour une tâche horaire
RAPPEL_ALERTE = 24 * 3600


def _lire(data_dir):
    try:
        with open(os.path.join(data_dir, ETAT), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _ecrire(data_dir, etat):
    try:
        with open(os.path.join(data_dir, ETAT), "w", encoding="utf-8") as fh:
            json.dump(etat, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def executer(app, base_url="", maintenant=None, journal=print):
    """Lance ce qui est dû. `app` : l'application du portail (sa config donne les chemins)."""
    import fileservice   # import tardif : fileservice importe Flask et les modèles
    now = maintenant or time.time()
    c = app.config
    data_dir, db_path = c["FS_DATA_DIR"], c["FS_DB"]
    cfg = mailer.lire_config(c["FS_CONFIG"])
    nom = cfg.get("shop_name") or "E85-FRANCE"
    etat = _lire(data_dir)
    fait = []

    if now - etat.get("sauvegarde", 0) >= JOUR:
        try:
            comptes.sauvegarder(db_path)
            etat["sauvegarde"] = now
            fait.append("sauvegarde")
        except Exception as e:
            journal(f"  ⚠ Sauvegarde du fileservice impossible : {e}")

    if sauvegarde_externe.reglages(cfg)["mode"] and now - etat.get("externe", 0) >= JOUR:
        r = sauvegarde_externe.executer(cfg, db_path, c["FS_FILES"], data_dir, nom)
        etat["externe"] = now   # même en échec : on ne réessaie pas toutes les heures, l'alerte part
        fait.append("externe " + ("OK" if r["ok"] else "ÉCHEC"))

    try:
        n = fileservice.executer_relances(app, base_url)
        if n:
            fait.append(f"{len(n)} relance(s)")
    except Exception as e:
        journal(f"  ⚠ Relances automatiques impossibles : {e}")

    # mise à jour du logiciel : vérifiée une fois par jour, installée la nuit si l'option est active
    atelier_mail = (cfg.get("smtp") or {}).get("atelier")

    def prevenir_atelier(sujet, texte):
        if atelier_mail:
            mailer.envoyer(cfg.get("smtp") or {}, atelier_mail, f"{nom} · {sujet}", texte, nom_expediteur=nom,
                           journal_dir=data_dir)
    r = mise_a_jour.automatique(cfg, data_dir, envoyer=prevenir_atelier)
    if r:
        fait.append("mise à jour : " + r)

    # alerte e-mail à l'atelier : quand la liste des problèmes change, puis rappel une fois par jour
    s = sante.verifier(db_path, data_dir, cfg)
    signature = " | ".join(s["problemes"])
    atelier = (cfg.get("smtp") or {}).get("atelier")
    if s["problemes"] and atelier and (signature != etat.get("alerte_signature")
                                       or now - etat.get("alerte_date", 0) >= RAPPEL_ALERTE):
        ok, _ = mailer.envoyer(cfg.get("smtp") or {}, atelier, f"⚠ Fileservice {nom} : {len(s['problemes'])} problème(s)",
                               "Contrôle automatique du fileservice :\n\n" + "\n".join("- " + p for p in s["problemes"])
                               + "\n\nDétail dans l'outil atelier, onglet Fileservice → État du service.",
                               nom_expediteur=nom)   # pas de journal_dir : une alerte ratée ne s'alerte pas elle-même
        if ok:
            etat["alerte_signature"], etat["alerte_date"] = signature, now
            fait.append("alerte envoyée")
    elif not s["problemes"]:
        etat.pop("alerte_signature", None)
    etat["derniere_execution"] = f"{dt.datetime.fromtimestamp(now):%Y-%m-%d %H:%M:%S}"
    _ecrire(data_dir, etat)
    return {"fait": fait, "sante": s}


def derniere_execution(data_dir):
    return _lire(data_dir).get("derniere_execution")
