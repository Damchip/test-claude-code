"""
Comptes de l'atelier (outil interne) et journal des actions.

Tant qu'aucun compte n'existe, l'outil garde son fonctionnement d'origine (accès
libre, ou mot de passe unique des Réglages). Dès le premier compte créé, chaque
technicien se connecte avec son identifiant.

Rôles :
  admin      : tout, y compris crédits, factures, suppression de compte client,
               réglages et gestion des comptes de l'atelier ;
  technicien : traitement des demandes (messages, livraison, refus), validation
               des inscriptions, bibliothèque.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import sqlite3
import struct
import time
from urllib.parse import quote

from werkzeug.security import check_password_hash, generate_password_hash

from comptes import ErreurCompte, MDP_MIN, connect

ROLES = ("admin", "technicien")

SCHEMA = """
CREATE TABLE IF NOT EXISTS techniciens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identifiant TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nom TEXT NOT NULL,
    mdp_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'technicien',
    actif INTEGER NOT NULL DEFAULT 1,
    cree_le TEXT NOT NULL,
    derniere_connexion TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    technicien TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    cible TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS journal_date ON journal(id DESC);
"""

# Double authentification (TOTP, RFC 6238 : Google Authenticator, Microsoft Authenticator, 2FAS…)
COLONNES_AJOUTEES = [
    ("totp_secret", "TEXT NOT NULL DEFAULT ''"),
    ("totp_dernier", "INTEGER NOT NULL DEFAULT 0"),   # dernier pas de temps accepté (anti-rejeu)
    ("codes_secours", "TEXT NOT NULL DEFAULT '[]'"),  # empreintes SHA-256 des codes de secours
]
CODES_SECOURS_N = 8


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db(db_path):
    with connect(db_path) as con:
        con.executescript(SCHEMA)
        existantes = {r["name"] for r in con.execute("PRAGMA table_info(techniciens)")}
        for nom, decl in COLONNES_AJOUTEES:
            if nom not in existantes:
                con.execute(f"ALTER TABLE techniciens ADD COLUMN {nom} {decl}")


def existe(db_path):
    with connect(db_path) as con:
        return con.execute("SELECT 1 FROM techniciens WHERE actif = 1 LIMIT 1").fetchone() is not None


def lister(db_path):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT id, identifiant, nom, role, actif, cree_le, derniere_connexion,"
            " totp_secret != '' AS double_auth FROM techniciens ORDER BY nom")]


def get(db_path, tech_id):
    with connect(db_path) as con:
        r = con.execute("SELECT id, identifiant, nom, role, actif, totp_secret != '' AS double_auth FROM techniciens"
                        " WHERE id = ?", (tech_id,)).fetchone()
    return dict(r) if r else None


def _verifier(identifiant, nom, role, mdp, creation):
    identifiant = (identifiant or "").strip().lower()[:40]
    nom = (nom or "").strip()[:60]
    if creation and not identifiant.replace(".", "").replace("-", "").replace("_", "").isalnum():
        raise ErreurCompte("Identifiant : lettres, chiffres, point ou tiret, sans espace.")
    if not nom:
        raise ErreurCompte("Indique le nom affiché (signature des messages).")
    if role not in ROLES:
        raise ErreurCompte("Rôle inconnu.")
    if mdp is not None and len(mdp) < MDP_MIN:
        raise ErreurCompte(f"Mot de passe : {MDP_MIN} caractères minimum.")
    return identifiant, nom


def creer(db_path, identifiant, nom, role, mdp):
    identifiant, nom = _verifier(identifiant, nom, role, mdp, True)
    try:
        with connect(db_path) as con:
            return con.execute("INSERT INTO techniciens (identifiant, nom, mdp_hash, role, cree_le) VALUES (?, ?, ?, ?, ?)",
                               (identifiant, nom, generate_password_hash(mdp), role, _now())).lastrowid
    except sqlite3.IntegrityError:
        raise ErreurCompte("Cet identifiant existe déjà.")


def modifier(db_path, tech_id, *, nom, role, actif, mdp=None, retirer_double_auth=False):
    _, nom = _verifier("x", nom, role, mdp or None, False)
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        # garde-fou : il doit toujours rester au moins un administrateur actif
        if role != "admin" or not actif:
            autres = con.execute("SELECT COUNT(*) n FROM techniciens WHERE role = 'admin' AND actif = 1 AND id != ?",
                                 (tech_id,)).fetchone()["n"]
            if not autres:
                raise ErreurCompte("Il doit rester au moins un administrateur actif.")
        con.execute("UPDATE techniciens SET nom = ?, role = ?, actif = ? WHERE id = ?", (nom, role, int(bool(actif)), tech_id))
        if mdp:
            con.execute("UPDATE techniciens SET mdp_hash = ? WHERE id = ?", (generate_password_hash(mdp), tech_id))
        if retirer_double_auth:   # téléphone perdu : l'administrateur la retire, le technicien la réactive
            con.execute("UPDATE techniciens SET totp_secret = '', totp_dernier = 0, codes_secours = '[]' WHERE id = ?",
                        (tech_id,))


_HASH_FACTICE = generate_password_hash(secrets.token_hex(16))


def authentifier(db_path, identifiant, mdp):
    """Vérifie identifiant + mot de passe. `double_auth` indique qu'un code est encore demandé :
    la connexion n'est notée qu'une fois le code vérifié (voir connexion_reussie)."""
    with connect(db_path) as con:
        r = con.execute("SELECT * FROM techniciens WHERE identifiant = ? AND actif = 1",
                        ((identifiant or "").strip(),)).fetchone()
    if not r:
        check_password_hash(_HASH_FACTICE, mdp or "")   # même temps de réponse qu'un identifiant existant
        return None
    if not check_password_hash(r["mdp_hash"], mdp or ""):
        return None
    t = {"id": r["id"], "identifiant": r["identifiant"], "nom": r["nom"], "role": r["role"],
         "double_auth": bool(r["totp_secret"])}
    if not t["double_auth"]:
        connexion_reussie(db_path, t["id"])
    return t


def connexion_reussie(db_path, tech_id):
    with connect(db_path) as con:
        con.execute("UPDATE techniciens SET derniere_connexion = ? WHERE id = ?", (_now(), tech_id))


def verifier_mdp(db_path, tech_id, mdp):
    with connect(db_path) as con:
        r = con.execute("SELECT mdp_hash FROM techniciens WHERE id = ? AND actif = 1", (tech_id,)).fetchone()
    return bool(r) and check_password_hash(r["mdp_hash"], mdp or "")


# --- Double authentification (TOTP) ------------------------------------------

def nouveau_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, pas):
    cle = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(cle, struct.pack(">Q", pas), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def code_totp(secret, instant=None):
    return _code(secret, int((time.time() if instant is None else instant) // 30))


def pas_valide(secret, code, instant=None, fenetre=1):
    """Pas de temps (entier) correspondant au code, en tolérant ±30 s de décalage d'horloge ; sinon None."""
    code = "".join(c for c in str(code or "") if c.isdigit())
    if len(code) != 6 or not secret:
        return None
    pas = int((time.time() if instant is None else instant) // 30)
    for p in range(pas - fenetre, pas + fenetre + 1):
        if hmac.compare_digest(_code(secret, p), code):
            return p
    return None


def uri_otpauth(secret, identifiant, emetteur="E85-FRANCE atelier"):
    """Lien à scanner (QR code) dans l'application d'authentification."""
    return (f"otpauth://totp/{quote(emetteur)}:{quote(identifiant)}?secret={secret}"
            f"&issuer={quote(emetteur)}&algorithm=SHA1&digits=6&period=30")


def _empreinte(code):
    return hashlib.sha256("".join(c for c in code.upper() if c.isalnum()).encode()).hexdigest()


def activer_double_auth(db_path, tech_id, secret, code):
    """Active la double authentification si le code prouve que l'application est bien configurée.
    Renvoie les codes de secours (affichés une seule fois)."""
    pas = pas_valide(secret, code)
    if pas is None:
        raise ErreurCompte("Code incorrect : vérifie l'heure du téléphone et saisis le code affiché.")
    codes = ["-".join(secrets.token_hex(2).upper() for _ in range(2)) for _ in range(CODES_SECOURS_N)]
    with connect(db_path) as con:
        con.execute("UPDATE techniciens SET totp_secret = ?, totp_dernier = ?, codes_secours = ? WHERE id = ?",
                    (secret, pas, json.dumps([_empreinte(c) for c in codes]), tech_id))
    return codes


def desactiver_double_auth(db_path, tech_id):
    with connect(db_path) as con:
        con.execute("UPDATE techniciens SET totp_secret = '', totp_dernier = 0, codes_secours = '[]' WHERE id = ?",
                    (tech_id,))


def verifier_second_facteur(db_path, tech_id, code):
    """Code de l'application (usage unique : un code déjà accepté est refusé) ou code de secours
    (supprimé après usage). Renvoie 'totp', 'secours' ou None."""
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        r = con.execute("SELECT totp_secret, totp_dernier, codes_secours FROM techniciens WHERE id = ? AND actif = 1",
                        (tech_id,)).fetchone()
        if not r or not r["totp_secret"]:
            return None
        pas = pas_valide(r["totp_secret"], code)
        if pas is not None:
            if pas <= r["totp_dernier"]:
                return None
            con.execute("UPDATE techniciens SET totp_dernier = ?, derniere_connexion = ? WHERE id = ?",
                        (pas, _now(), tech_id))
            return "totp"
        codes = json.loads(r["codes_secours"] or "[]")
        e = _empreinte(str(code or ""))
        if len("".join(c for c in str(code or "") if c.isalnum())) == 8 and e in codes:
            codes.remove(e)
            con.execute("UPDATE techniciens SET codes_secours = ?, derniere_connexion = ? WHERE id = ?",
                        (json.dumps(codes), _now(), tech_id))
            return "secours"
    return None


def codes_secours_restants(db_path, tech_id):
    with connect(db_path) as con:
        r = con.execute("SELECT codes_secours FROM techniciens WHERE id = ?", (tech_id,)).fetchone()
    return len(json.loads(r["codes_secours"] or "[]")) if r else 0


def noter(db_path, technicien, action, cible="", detail=""):
    """Ajoute une ligne au journal. Ne lève jamais (le journal ne doit pas bloquer le travail)."""
    try:
        with connect(db_path) as con:
            con.execute("INSERT INTO journal (date, technicien, action, cible, detail) VALUES (?, ?, ?, ?, ?)",
                        (_now(), (technicien or "")[:60], action[:60], str(cible or "")[:120], str(detail or "")[:300]))
    except Exception:
        pass


def journal(db_path, limite=200, recherche=""):
    sql, args = "SELECT * FROM journal", []
    if recherche:
        sql += " WHERE technicien LIKE ? OR action LIKE ? OR cible LIKE ? OR detail LIKE ?"
        args = [f"%{recherche}%"] * 4
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limite])]
