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


ECHECS = "mails_echecs.jsonl"   # envois ratés alors que le SMTP est configuré (alerte dans l'outil atelier)


def _noter_echec(journal_dir, a, sujet, texte, erreur):
    if not journal_dir:
        return
    try:
        os.makedirs(journal_dir, exist_ok=True)
        now = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"
        with open(os.path.join(journal_dir, "mails_non_envoyes.log"), "a", encoding="utf-8") as fh:
            fh.write(f"--- {now} · à {a} · {sujet} · ÉCHEC : {erreur}\n{texte}\n\n")
        with open(os.path.join(journal_dir, ECHECS), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"date": now, "a": a, "sujet": sujet, "erreur": erreur}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def echecs(journal_dir, limite=50):
    """Envois ratés pas encore vus par l'atelier (les plus récents d'abord)."""
    try:
        with open(os.path.join(journal_dir, ECHECS), encoding="utf-8") as fh:
            lignes = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return []
    return lignes[::-1][:limite]


def acquitter_echecs(journal_dir):
    try:
        os.remove(os.path.join(journal_dir, ECHECS))
    except OSError:
        pass


def envoyer(cfg, a, sujet, texte, nom_expediteur="E85-FRANCE", journal_dir=None, pieces=None):
    """Envoie un e-mail texte, avec d'éventuelles pièces jointes [(nom, octets, type/soustype)].
    Renvoie (ok, message_erreur)."""
    # Pas de retour à la ligne dans les en-têtes (le sujet peut contenir un nom saisi par un client)
    a = " ".join(str(a or "").split())
    sujet = " ".join(str(sujet or "").split())[:200]
    nom_expediteur = " ".join(str(nom_expediteur or "").split())
    if not a or "," in a or ";" in a:
        return False, "Destinataire invalide."
    msg = EmailMessage()
    expediteur = cfg.get("from") or cfg.get("user") or "noreply@localhost"
    msg["From"] = formataddr((nom_expediteur, expediteur))
    msg["To"] = a
    msg["Subject"] = sujet
    msg["Message-ID"] = make_msgid(domain=expediteur.split("@")[-1])
    msg.set_content(texte)
    for nom, contenu, mime in pieces or []:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(contenu, maintype=maintype, subtype=subtype or "octet-stream", filename=nom)

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
        erreur = f"Envoi impossible : {e.__class__.__name__}: {e}"
        _noter_echec(journal_dir, a, sujet, texte, erreur)
        return False, erreur
