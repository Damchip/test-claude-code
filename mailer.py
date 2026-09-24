"""
Envoi d'e-mails par SMTP (hébergeur O2switch ou autre).

Réglages dans data/portal_config.json, clé "smtp" (fichier hors git), modifiables
depuis l'outil interne, onglet Clients :
    host      : serveur SMTP (O2switch : le nom du serveur indiqué dans cPanel,
                ou mail.votredomaine.fr)
    port      : 465 (SSL, recommandé) ou 587 (STARTTLS)
    user      : adresse complète de la boîte, ex. fileservice@e85-france.fr
    password  : mot de passe de cette boîte
    from      : expéditeur affiché (par défaut = user)
    atelier   : adresse qui reçoit les notifications (nouvelle inscription…)

Sans réglage, les e-mails sont écrits dans data/mails_non_envoyes.log au lieu
d'être envoyés : pratique en test, et rien n'est perdu.
"""
import datetime as dt
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


def lire_config(config_path):
    try:
        with open(config_path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def config_smtp(config_path):
    return lire_config(config_path).get("smtp") or {}


def configure(cfg):
    return bool(cfg.get("host") and cfg.get("user") and cfg.get("password"))


def envoyer(cfg, a, sujet, texte, nom_expediteur="E85-FRANCE", journal_dir=None):
    """Envoie un e-mail texte. Renvoie (ok, message_erreur)."""
    if not a:
        return False, "Destinataire vide."
    msg = EmailMessage()
    expediteur = cfg.get("from") or cfg.get("user") or "noreply@localhost"
    msg["From"] = formataddr((nom_expediteur, expediteur))
    msg["To"] = a
    msg["Subject"] = sujet
    msg["Message-ID"] = make_msgid(domain=expediteur.split("@")[-1])
    msg.set_content(texte)

    if not configure(cfg):
        if journal_dir:
            os.makedirs(journal_dir, exist_ok=True)
            with open(os.path.join(journal_dir, "mails_non_envoyes.log"), "a", encoding="utf-8") as fh:
                fh.write(f"--- {dt.datetime.now():%Y-%m-%d %H:%M:%S} · à {a} · {sujet}\n{texte}\n\n")
        return False, "SMTP non configuré : e-mail écrit dans data/mails_non_envoyes.log."

    port = int(cfg.get("port") or 465)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, context=ctx, timeout=15) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=15) as s:
                s.starttls(context=ctx)
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        return True, ""
    except (smtplib.SMTPException, OSError) as e:
        return False, f"Envoi impossible : {e.__class__.__name__}: {e}"
