"""
Notifications push de l'application (PWA) de l'espace client.

Le client installe l'espace client sur son téléphone (« Ajouter à l'écran d'accueil ») et active les
notifications dans ses Paramètres : son navigateur crée un abonnement (adresse d'envoi + clés) que
l'on garde ici. Quand un fichier est prêt, que l'atelier écrit ou demande une précision, une
notification part vers chaque appareil abonné du compte.

Standards utilisés (aucun service tiers à payer) :
  - VAPID (RFC 8292) : l'atelier signe ses envois avec une paire de clés P-256, créée au premier
    usage et gardée dans portal_config.json (clé "push", fichier hors git) ;
  - chiffrement du contenu aes128gcm (RFC 8291 / RFC 8188) : seul l'appareil peut lire le message.

Dépendance : le paquet « cryptography » (requirements.txt). Sans lui, les notifications sont
simplement désactivées : le reste du fileservice fonctionne.
"""
import base64
import datetime as dt
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from comptes import connect

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    DISPONIBLE = True
except BaseException as e:   # pragma: no cover - dépend de l'installation
    # ImportError si absent ; une installation cassée lève parfois une erreur Rust (PanicException) qui
    # ne dérive pas d'Exception : dans les deux cas, pas de notifications, mais le portail démarre.
    if isinstance(e, (KeyboardInterrupt, SystemExit)):
        raise
    DISPONIBLE = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS abonnements_push (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    utilisateur_id INTEGER,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    appareil TEXT NOT NULL DEFAULT '',
    cree_le TEXT NOT NULL,
    dernier_envoi TEXT
);
CREATE INDEX IF NOT EXISTS abonnements_push_client ON abonnements_push(client_id);
"""
MAX_PAR_COMPTE = 20
# Seuls les services push des navigateurs sont acceptés : le serveur n'envoie jamais de requête ailleurs
SERVICES = (".googleapis.com", ".push.apple.com", ".push.services.mozilla.com", ".notify.windows.com")
TTL = 24 * 3600   # un téléphone éteint reçoit la notification s'il se rallume dans les 24 h


class ErreurPush(RuntimeError):
    pass


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(txt):
    txt = str(txt or "")
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db(db_path):
    with connect(db_path) as con:
        con.executescript(SCHEMA)


# --- Clés VAPID ------------------------------------------------------------------

def _publique_brute(cle_privee):
    return cle_privee.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def cles(config_path, creer=True):
    """(clé privée, clé publique en base64url) ; créées et enregistrées au premier appel."""
    if not DISPONIBLE:
        return None, None
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
    except (OSError, ValueError):
        cfg = {}
    pem = (cfg.get("push") or {}).get("vapid_prive")
    if pem:
        prive = serialization.load_pem_private_key(pem.encode(), password=None)
        return prive, _b64(_publique_brute(prive))
    if not creer:
        return None, None
    prive = ec.generate_private_key(ec.SECP256R1())
    cfg.setdefault("push", {})["vapid_prive"] = prive.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
    tmp = config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, config_path)
    return prive, _b64(_publique_brute(prive))


def jeton_vapid(cle_privee, endpoint, sujet, maintenant=None):
    """En-tête Authorization « vapid t=…, k=… » (JWT ES256, valable 12 h) pour ce service push."""
    u = urllib.parse.urlsplit(endpoint)
    entete = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    corps = _b64(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int((maintenant or time.time()) + 12 * 3600),
                             "sub": sujet}, separators=(",", ":")).encode())
    signe = f"{entete}.{corps}".encode()
    r, s = decode_dss_signature(cle_privee.sign(signe, ec.ECDSA(hashes.SHA256())))
    jwt = f"{entete}.{corps}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={_b64(_publique_brute(cle_privee))}"


# --- Chiffrement RFC 8291 (aes128gcm) --------------------------------------------

def _hkdf(sel, cle, info, longueur):
    return HKDF(algorithm=hashes.SHA256(), length=longueur, salt=sel, info=info).derive(cle)


def chiffrer(message, p256dh, auth, _prive_serveur=None, _sel=None):
    """Chiffre `message` (octets) pour l'appareil (clés de son abonnement). Renvoie le corps à envoyer.
    _prive_serveur et _sel ne servent qu'aux tests (vecteurs de la RFC 8291)."""
    ua_publique = _unb64(p256dh)
    secret = _unb64(auth)
    serveur = _prive_serveur or ec.generate_private_key(ec.SECP256R1())
    serveur_publique = _publique_brute(serveur)
    partage = serveur.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_publique))
    ikm = _hkdf(secret, partage, b"WebPush: info\x00" + ua_publique + serveur_publique, 32)
    sel = _sel or os.urandom(16)
    cek = _hkdf(sel, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(sel, ikm, b"Content-Encoding: nonce\x00", 12)
    chiffre = AESGCM(cek).encrypt(nonce, message + b"\x02", None)   # 0x02 : dernier (et seul) bloc
    return sel + struct.pack(">IB", 4096, len(serveur_publique)) + serveur_publique + chiffre


# --- Abonnements -----------------------------------------------------------------

def abonner(db_path, client_id, abonnement, utilisateur_id=None, appareil=""):
    """Enregistre l'abonnement d'un appareil (objet PushSubscription du navigateur, en JSON)."""
    if not isinstance(abonnement, dict):
        raise ErreurPush("Abonnement invalide.")
    endpoint = str(abonnement.get("endpoint") or "")
    cles_ab = abonnement.get("keys") or {}
    hote = (urllib.parse.urlsplit(endpoint).hostname or "") if endpoint.startswith("https://") else ""
    if not hote or len(endpoint) > 1000 or not any(hote.endswith(sfx) for sfx in SERVICES):
        raise ErreurPush("Adresse d'envoi invalide.")
    try:
        if len(_unb64(cles_ab.get("p256dh"))) != 65 or len(_unb64(cles_ab.get("auth"))) != 16:
            raise ValueError
    except (ValueError, TypeError):
        raise ErreurPush("Clés d'abonnement invalides.")
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM abonnements_push WHERE endpoint = ?", (endpoint,))   # appareil réattribué
        n = con.execute("SELECT COUNT(*) n FROM abonnements_push WHERE client_id = ?", (client_id,)).fetchone()["n"]
        if n >= MAX_PAR_COMPTE:   # on retire le plus ancien plutôt que de refuser le nouvel appareil
            con.execute("DELETE FROM abonnements_push WHERE id = (SELECT MIN(id) FROM abonnements_push WHERE client_id = ?)",
                        (client_id,))
        con.execute("INSERT INTO abonnements_push (client_id, utilisateur_id, endpoint, p256dh, auth, appareil, cree_le)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (client_id, utilisateur_id, endpoint, str(cles_ab["p256dh"]), str(cles_ab["auth"]),
                     str(appareil or "")[:120], _now()))


def desabonner(db_path, client_id, endpoint):
    with connect(db_path) as con:
        con.execute("DELETE FROM abonnements_push WHERE client_id = ? AND endpoint = ?", (client_id, str(endpoint or "")))


def abonnements(db_path, client_id):
    with connect(db_path) as con:
        # un utilisateur rattaché désactivé ne reçoit plus rien (ses abonnements restent s'il est réactivé)
        return [dict(r) for r in con.execute(
            "SELECT a.id, a.utilisateur_id, a.endpoint, a.p256dh, a.auth, a.appareil, a.cree_le, a.dernier_envoi"
            " FROM abonnements_push a LEFT JOIN utilisateurs u ON u.id = a.utilisateur_id"
            " WHERE a.client_id = ? AND (a.utilisateur_id IS NULL OR u.actif = 1) ORDER BY a.id", (client_id,))]


def nombre(db_path, client_id):
    try:
        with connect(db_path) as con:
            return con.execute("SELECT COUNT(*) n FROM abonnements_push a LEFT JOIN utilisateurs u ON u.id = a.utilisateur_id"
                               " WHERE a.client_id = ? AND (a.utilisateur_id IS NULL OR u.actif = 1)", (client_id,)).fetchone()["n"]
    except Exception:
        return 0


# --- Envoi -----------------------------------------------------------------------

def _poster(endpoint, corps, entetes):
    req = urllib.request.Request(endpoint, data=corps, headers=entetes, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def envoyer(db_path, config_path, client_id, notification, sujet="mailto:contact@localhost"):
    """Envoie `notification` ({titre, texte, url, tag}) à tous les appareils abonnés du compte.
    Supprime les abonnements expirés (404 / 410). Renvoie le nombre d'appareils atteints. Ne lève jamais."""
    if not DISPONIBLE:
        return 0
    try:
        prive, _ = cles(config_path, creer=False)
        if not prive:
            return 0
        message = json.dumps(notification, ensure_ascii=False).encode()[:3000]
        atteints = 0
        for ab in abonnements(db_path, client_id):
            corps = chiffrer(message, ab["p256dh"], ab["auth"])
            statut = _poster(ab["endpoint"], corps, {
                "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL),
                "Urgency": "high", "Authorization": jeton_vapid(prive, ab["endpoint"], sujet)})
            with connect(db_path) as con:
                if statut in (404, 410):     # désinstallée ou notifications retirées sur l'appareil
                    con.execute("DELETE FROM abonnements_push WHERE id = ?", (ab["id"],))
                elif 200 <= statut < 300:
                    atteints += 1
                    con.execute("UPDATE abonnements_push SET dernier_envoi = ? WHERE id = ?", (_now(), ab["id"]))
        return atteints
    except Exception:
        return 0
