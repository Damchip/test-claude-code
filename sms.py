"""
Alertes SMS aux clients (fichier prêt, information demandée), en option.

Réglages atelier (clé "sms" de portal_config.json, fichier hors git) :
    fournisseur : "brevo" | "twilio"
    cle         : Brevo : clé API (xkeysib-…) · Twilio : Auth Token
    compte      : Twilio seulement : Account SID (AC…)
    expediteur  : Brevo : nom affiché, 3 à 11 lettres/chiffres (ex. E85FRANCE)
                  Twilio : numéro d'envoi (+33…) ou nom alphanumérique
    actif       : true pour envoyer

Chaque client choisit dans Paramètres s'il veut les SMS et sur quel mobile.
Le SMS est court et ne contient que le numéro de demande et le lien : le détail reste
dans l'espace client. Envoi par HTTP (urllib) : aucune dépendance à installer.
"""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

FOURNISSEURS = {"brevo": "Brevo", "twilio": "Twilio"}
LONGUEUR_MAX = 300   # 2 SMS au plus


class ErreurSms(RuntimeError):
    pass


def reglages(cfg, masquer=True):
    r = {"fournisseur": "brevo", "cle": "", "compte": "", "expediteur": "", "actif": False}
    r.update({k: v for k, v in (cfg.get("sms") or {}).items() if k in r})
    if masquer:
        r["cle_set"] = bool(r.pop("cle"))
    return r


def configure(cfg):
    r = reglages(cfg, masquer=False)
    if not r["actif"] or r["fournisseur"] not in FOURNISSEURS or not r["cle"] or not r["expediteur"]:
        return False
    return r["fournisseur"] != "twilio" or bool(r["compte"])


def verifier_expediteur(fournisseur, expediteur):
    if fournisseur == "brevo" and not re.fullmatch(r"[A-Za-z0-9]{3,11}", expediteur or ""):
        return "Expéditeur Brevo : 3 à 11 lettres ou chiffres, sans espace (ex. E85FRANCE)."
    if fournisseur == "twilio" and not re.fullmatch(r"\+[1-9]\d{7,14}|[A-Za-z0-9 ]{3,11}", expediteur or ""):
        return "Expéditeur Twilio : numéro au format +33… ou nom de 3 à 11 caractères."
    return ""


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}")
            detail = detail.get("message") or detail.get("code") or e.reason
        except ValueError:
            detail = e.reason
        raise ErreurSms(f"{e.code} : {detail}")
    except (urllib.error.URLError, OSError) as e:
        raise ErreurSms(f"Fournisseur SMS injoignable : {e}")


def envoyer(cfg, numero, texte):
    """Envoie un SMS. Renvoie (ok, message). Ne lève jamais (un SMS raté ne bloque pas l'atelier)."""
    if not configure(cfg):
        return False, "SMS non configurés."
    r = reglages(cfg, masquer=False)
    texte = " ".join(str(texte or "").split())[:LONGUEUR_MAX]
    try:
        if r["fournisseur"] == "brevo":
            _post("https://api.brevo.com/v3/transactionalSMS/sms",
                  json.dumps({"sender": r["expediteur"], "recipient": numero.lstrip("+"), "content": texte,
                              "type": "transactional"}).encode(),
                  {"api-key": r["cle"], "Content-Type": "application/json", "Accept": "application/json"})
        else:
            auth = base64.b64encode(f"{r['compte']}:{r['cle']}".encode()).decode()
            _post(f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(r['compte'])}/Messages.json",
                  urllib.parse.urlencode({"To": numero, "From": r["expediteur"], "Body": texte}).encode(),
                  {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
        return True, ""
    except ErreurSms as e:
        return False, f"SMS non envoyé : {e}"
