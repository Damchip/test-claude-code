"""
Paiement des packs de crédits par Stripe Checkout — appels HTTP directs, sans dépendance.

Réglages dans data/portal_config.json, clé "stripe" (modifiables depuis l'outil
interne, onglet Fileservice → Réglages) :
    secret_key      : clé secrète (sk_live_… ou sk_test_… pour essayer)
    webhook_secret  : secret de signature du webhook (whsec_…)

Webhook à déclarer dans le tableau de bord Stripe :
    https://<adresse publique du portail>/stripe/webhook
    événements : checkout.session.completed, checkout.session.async_payment_succeeded
Le retour navigateur (/credits/merci) vérifie aussi la session : les
crédits arrivent même si le webhook est en retard, et jamais deux fois.
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.stripe.com/v1"
TOLERANCE = 300  # secondes d'écart admises sur la signature du webhook


class ErreurStripe(Exception):
    pass


def configure(cfg):
    return str(cfg.get("secret_key") or "").startswith(("sk_live_", "sk_test_", "rk_live_", "rk_test_"))


def _appel(cfg, methode, chemin, params=None):
    if not configure(cfg):
        raise ErreurStripe("Clé Stripe absente.")
    data = urllib.parse.urlencode(params or {}).encode() if methode == "POST" else None
    req = urllib.request.Request(API + chemin, data=data, method=methode)
    jeton = base64.b64encode((cfg["secret_key"] + ":").encode()).decode()
    req.add_header("Authorization", "Basic " + jeton)
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise ErreurStripe(f"HTTP {e.code} {msg}".strip())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ErreurStripe(str(e))


def creer_session(cfg, *, montant_centimes, libelle, email, reference, succes, annulation):
    """Crée une session Checkout et renvoie l'URL de paiement."""
    sess = _appel(cfg, "POST", "/checkout/sessions", {
        "mode": "payment",
        "success_url": succes,
        "cancel_url": annulation,
        "customer_email": email,
        "client_reference_id": reference,
        "metadata[reference]": reference,
        "locale": "fr",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(int(montant_centimes)),
        "line_items[0][price_data][product_data][name]": libelle,
    })
    if not sess.get("url"):
        raise ErreurStripe("Réponse Stripe sans URL de paiement.")
    return sess["url"]


def lire_session(cfg, session_id):
    return _appel(cfg, "GET", "/checkout/sessions/" + urllib.parse.quote(session_id, safe=""))


def signer(secret, payload, horodatage):
    """Signature attendue d'un webhook (exposée pour les tests)."""
    return hmac.new(secret.encode(), f"{horodatage}.".encode() + payload, hashlib.sha256).hexdigest()


def verifier_webhook(cfg, payload, entete, maintenant=None):
    secret = cfg.get("webhook_secret") or ""
    if not secret:
        raise ErreurStripe("Secret du webhook non configuré.")
    morceaux = [p.split("=", 1) for p in (entete or "").split(",") if "=" in p]
    t = next((v for k, v in morceaux if k.strip() == "t"), "")
    signatures = [v for k, v in morceaux if k.strip() == "v1"]
    if not t.isdigit() or not signatures:
        raise ErreurStripe("En-tête de signature invalide.")
    if abs((maintenant or time.time()) - int(t)) > TOLERANCE:
        raise ErreurStripe("Signature trop ancienne.")
    attendue = signer(secret, payload, t)
    if not any(hmac.compare_digest(attendue, s) for s in signatures):
        raise ErreurStripe("Signature incorrecte.")
    return json.loads(payload.decode())
