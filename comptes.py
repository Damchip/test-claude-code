"""
Comptes clients du fileservice — SQLite local (data/fileservice.db).

Base SÉPARÉE de la bibliothèque de solutions : le portail (exposé sur internet)
écrit ici, jamais dans solutions.db.

  clients     : un compte pro par société (statut en_attente -> actif / bloque)
  mouvements  : journal des crédits (achat, débit, remboursement, ajustement)
  jetons      : liens « mot de passe oublié » (seule l'empreinte SHA-256 est stockée)
  utilisateurs: personnes rattachées à un compte client (même solde, connexion propre)
"""
import datetime as dt
import hashlib
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_DB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "data", "fileservice.db"))

STATUTS = ("en_attente", "actif", "bloque")
MDP_MIN = 10
JETON_DUREE = 3600               # 1 h (mot de passe oublié)
JETON_INVITATION = 7 * 24 * 3600  # 7 jours (compte créé par l'atelier)

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    societe TEXT NOT NULL,
    siret TEXT NOT NULL,
    tva TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    tel TEXT NOT NULL DEFAULT '',
    mdp_hash TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'en_attente',
    niveau TEXT NOT NULL DEFAULT 'Standard',
    credits INTEGER NOT NULL DEFAULT 0,
    cree_le TEXT NOT NULL,
    valide_le TEXT,
    derniere_connexion TEXT
);
CREATE TABLE IF NOT EXISTS mouvements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    date TEXT NOT NULL,
    libelle TEXT NOT NULL,
    montant INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mouvements_client ON mouvements(client_id, id);
CREATE TABLE IF NOT EXISTS jetons (
    empreinte TEXT PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    expire INTEGER NOT NULL,
    utilise INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS utilisateurs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    nom TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    mdp_hash TEXT NOT NULL,
    peut_acheter INTEGER NOT NULL DEFAULT 0,
    actif INTEGER NOT NULL DEFAULT 1,
    langue TEXT NOT NULL DEFAULT 'fr',
    cree_le TEXT NOT NULL,
    derniere_connexion TEXT
);
CREATE INDEX IF NOT EXISTS utilisateurs_client ON utilisateurs(client_id);
"""
UTILISATEURS_MAX = 10


class ErreurCompte(ValueError):
    """Erreur affichable telle quelle au client."""


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect(db_path=DEFAULT_DB):
    """Connexion courte : validée en sortie, annulée sur erreur, toujours fermée."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path, timeout=8)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


# Colonnes ajoutées après la première version (migration douce des bases existantes)
COLONNES_AJOUTEES = [
    ("adresse", "TEXT NOT NULL DEFAULT ''"),
    ("code_postal", "TEXT NOT NULL DEFAULT ''"),
    ("ville", "TEXT NOT NULL DEFAULT ''"),
    ("pays", "TEXT NOT NULL DEFAULT 'France'"),
    ("langue", "TEXT NOT NULL DEFAULT 'fr'"),
    ("sms_mobile", "TEXT NOT NULL DEFAULT ''"),     # alertes SMS (numéro au format international)
    ("sms_actif", "INTEGER NOT NULL DEFAULT 0"),
]


def init_db(db_path=DEFAULT_DB):
    with connect(db_path) as con:
        con.executescript(SCHEMA)
        existantes = {r["name"] for r in con.execute("PRAGMA table_info(clients)")}
        for nom, decl in COLONNES_AJOUTEES:
            if nom not in existantes:
                con.execute(f"ALTER TABLE clients ADD COLUMN {nom} {decl}")
        if "utilisateur_id" not in {r["name"] for r in con.execute("PRAGMA table_info(jetons)")}:
            con.execute("ALTER TABLE jetons ADD COLUMN utilisateur_id INTEGER")


def sauvegarder(db_path=DEFAULT_DB, garder=30):
    """Copie cohérente de la base (API de sauvegarde SQLite, sûre pendant l'écriture) dans
    data/backups/fileservice-AAAAMMJJ-HHMMSS.db. Garde les `garder` plus récentes.
    Renvoie le chemin de la copie, ou None si la base n'existe pas encore."""
    if not os.path.isfile(db_path):
        return None
    bdir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    os.makedirs(bdir, exist_ok=True)
    dest = os.path.join(bdir, f"fileservice-{dt.datetime.now():%Y%m%d-%H%M%S-%f}.db")
    src = sqlite3.connect(db_path, timeout=8)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    anciennes = sorted((os.path.join(bdir, f) for f in os.listdir(bdir)
                        if f.startswith("fileservice-") and f.endswith(".db")), reverse=True)
    for old in anciennes[garder:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dest


def dernieres_sauvegardes(db_path=DEFAULT_DB, n=5):
    bdir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    if not os.path.isdir(bdir):
        return []
    noms = sorted((f for f in os.listdir(bdir) if f.startswith("fileservice-") and f.endswith(".db")), reverse=True)
    return [{"nom": f, "taille": os.path.getsize(os.path.join(bdir, f))} for f in noms[:n]]


# --- Validation --------------------------------------------------------------

def normaliser_siret(raw):
    return re.sub(r"\s+", "", raw or "")


def siret_valide(siret):
    """14 chiffres + clé de Luhn (exception La Poste : somme des chiffres multiple de 5)."""
    if not re.fullmatch(r"\d{14}", siret):
        return False
    if siret.startswith("356000000"):
        return sum(int(c) for c in siret) % 5 == 0
    total = 0
    for i, c in enumerate(reversed(siret)):
        d = int(c)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def normaliser_tva(raw):
    return re.sub(r"[\s.-]+", "", raw or "").upper()


def tva_valide(tva):
    """Format intracommunautaire : 2 lettres pays + 2 à 13 caractères."""
    return bool(re.fullmatch(r"[A-Z]{2}[0-9A-Z]{2,13}", tva))


def est_en_france(pays):
    return (pays or "France").strip().lower() in ("france", "fr", "")


def email_valide(email):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", email)) and len(email) <= 160


def _verifier_mdp(mdp):
    if len(mdp or "") < MDP_MIN:
        raise ErreurCompte(f"Le mot de passe doit faire au moins {MDP_MIN} caractères.")
    if len(mdp) > 200:
        raise ErreurCompte("Mot de passe trop long.")


# --- Comptes -----------------------------------------------------------------

def _adresse(adresse="", code_postal="", ville="", pays=""):
    return {"adresse": (adresse or "").strip()[:160], "code_postal": (code_postal or "").strip()[:12],
            "ville": (ville or "").strip()[:80], "pays": (pays or "").strip()[:60] or "France"}


def creer_client(db_path, *, societe, siret, tva, contact, email, tel, mdp,
                 adresse="", code_postal="", ville="", pays=""):
    adr = _adresse(adresse, code_postal, ville, pays)
    societe = (societe or "").strip()[:120]
    contact = (contact or "").strip()[:80]
    tel = (tel or "").strip()[:30]
    email = (email or "").strip().lower()
    siret = normaliser_siret(siret)
    tva = normaliser_tva(tva)
    if not societe:
        raise ErreurCompte("Indiquez le nom de la société.")
    if est_en_france(adr["pays"]):
        if not siret_valide(siret):
            raise ErreurCompte("SIRET invalide : 14 chiffres attendus.")
    else:
        # entreprise étrangère : numéro d'immatriculation libre (pas de SIRET)
        siret = (siret or "").strip()[:40]
    if tva and not tva_valide(tva):
        raise ErreurCompte("Numéro de TVA invalide (ex. FR12345678901).")
    if not email_valide(email):
        raise ErreurCompte("Adresse e-mail invalide.")
    _verifier_mdp(mdp)
    if email_pris(db_path, email):
        raise ErreurCompte("Un compte existe déjà avec cette adresse e-mail.")
    try:
        with connect(db_path) as con:
            cur = con.execute(
                "INSERT INTO clients (societe, siret, tva, contact, email, tel, mdp_hash, cree_le,"
                " adresse, code_postal, ville, pays) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (societe, siret, tva, contact, email, tel, generate_password_hash(mdp), _now(),
                 adr["adresse"], adr["code_postal"], adr["ville"], adr["pays"]))
            return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ErreurCompte("Un compte existe déjà avec cette adresse e-mail.")


def get_client(db_path, client_id):
    with connect(db_path) as con:
        row = con.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return dict(row) if row else None


def client_par_email(db_path, email):
    with connect(db_path) as con:
        row = con.execute("SELECT * FROM clients WHERE email = ?", ((email or "").strip(),)).fetchone()
    return dict(row) if row else None


# Empreinte factice : un e-mail inconnu coûte le même temps de calcul qu'un vrai
_HASH_FACTICE = generate_password_hash(secrets.token_hex(16))


def authentifier(db_path, email, mdp):
    """Renvoie le client si e-mail + mot de passe corrects (quel que soit son statut), sinon None."""
    c = client_par_email(db_path, email)
    if not c:
        check_password_hash(_HASH_FACTICE, mdp or "")
        return None
    if not check_password_hash(c["mdp_hash"], mdp or ""):
        return None
    with connect(db_path) as con:
        con.execute("UPDATE clients SET derniere_connexion = ? WHERE id = ?", (_now(), c["id"]))
    return c


def changer_langue(db_path, client_id, langue):
    with connect(db_path) as con:
        con.execute("UPDATE clients SET langue = ? WHERE id = ?", ((langue or "fr")[:5], client_id))


def modifier_profil(db_path, client_id, *, contact, tel, tva, adresse, code_postal, ville, pays):
    tva = normaliser_tva(tva)
    if tva and not tva_valide(tva):
        raise ErreurCompte("Numéro de TVA invalide (ex. FR12345678901).")
    adr = _adresse(adresse, code_postal, ville, pays)
    with connect(db_path) as con:
        con.execute("UPDATE clients SET contact = ?, tel = ?, tva = ?, adresse = ?, code_postal = ?, ville = ?,"
                    " pays = ? WHERE id = ?",
                    ((contact or "").strip()[:80], (tel or "").strip()[:30], tva, adr["adresse"],
                     adr["code_postal"], adr["ville"], adr["pays"], client_id))


def changer_mdp(db_path, client_id, actuel, nouveau):
    c = get_client(db_path, client_id)
    if not c or not check_password_hash(c["mdp_hash"], actuel or ""):
        raise ErreurCompte("Mot de passe actuel incorrect.")
    _verifier_mdp(nouveau)
    with connect(db_path) as con:
        con.execute("UPDATE clients SET mdp_hash = ? WHERE id = ?", (generate_password_hash(nouveau), client_id))


def lister_clients(db_path):
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT id, societe, siret, tva, contact, email, tel, statut, niveau, credits, cree_le,"
            " valide_le, derniere_connexion, adresse, code_postal, ville, pays FROM clients"
            " ORDER BY CASE statut WHEN 'en_attente' THEN 0 ELSE 1 END, cree_le DESC").fetchall()
    return [dict(r) for r in rows]


def changer_statut(db_path, client_id, statut):
    if statut not in STATUTS:
        raise ErreurCompte("Statut inconnu.")
    with connect(db_path) as con:
        if statut == "actif":
            con.execute("UPDATE clients SET statut = ?, valide_le = COALESCE(valide_le, ?) WHERE id = ?",
                        (statut, _now(), client_id))
        else:
            con.execute("UPDATE clients SET statut = ? WHERE id = ?", (statut, client_id))


def changer_niveau(db_path, client_id, niveau):
    niveau = (niveau or "").strip()[:30] or "Standard"
    with connect(db_path) as con:
        con.execute("UPDATE clients SET niveau = ? WHERE id = ?", (niveau, client_id))


def mouvement(db_path, client_id, montant, libelle):
    """Ajoute (ou retire) des crédits et journalise, dans une seule transaction.
    Refuse de passer le solde en négatif."""
    montant = int(montant)
    libelle = (libelle or "").strip()[:200] or "Ajustement"
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT credits FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not row:
            raise ErreurCompte("Client introuvable.")
        if row["credits"] + montant < 0:
            raise ErreurCompte("Solde insuffisant.")
        con.execute("UPDATE clients SET credits = credits + ? WHERE id = ?", (montant, client_id))
        con.execute("INSERT INTO mouvements (client_id, date, libelle, montant) VALUES (?, ?, ?, ?)",
                    (client_id, _now(), libelle, montant))
        return row["credits"] + montant


def mouvements(db_path, client_id, limite=50):
    with connect(db_path) as con:
        rows = con.execute("SELECT date, libelle, montant FROM mouvements WHERE client_id = ?"
                           " ORDER BY id DESC LIMIT ?", (client_id, limite)).fetchall()
    return [dict(r) for r in rows]


# --- Mot de passe oublié -----------------------------------------------------

def _empreinte(jeton):
    return hashlib.sha256(jeton.encode()).hexdigest()


def creer_jeton(db_path, client_id, duree=JETON_DUREE, utilisateur_id=None):
    """Lien de (ré)initialisation du mot de passe : du titulaire, ou d'un utilisateur rattaché."""
    jeton = secrets.token_urlsafe(32)
    with connect(db_path) as con:
        con.execute("DELETE FROM jetons WHERE expire < ? OR utilise = 1", (int(time.time()),))
        con.execute("INSERT INTO jetons (empreinte, client_id, expire, utilisateur_id) VALUES (?, ?, ?, ?)",
                    (_empreinte(jeton), client_id, int(time.time()) + duree, utilisateur_id))
    return jeton


def jeton_valide(db_path, jeton):
    with connect(db_path) as con:
        row = con.execute("SELECT client_id FROM jetons WHERE empreinte = ? AND utilise = 0 AND expire >= ?",
                          (_empreinte(jeton or ""), int(time.time()))).fetchone()
    return row["client_id"] if row else None


def reinitialiser_mdp(db_path, jeton, mdp):
    _verifier_mdp(mdp)
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT client_id, utilisateur_id FROM jetons WHERE empreinte = ? AND utilise = 0 AND expire >= ?",
                          (_empreinte(jeton or ""), int(time.time()))).fetchone()
        if not row:
            raise ErreurCompte("Ce lien a expiré ou a déjà servi. Refaites une demande.")
        # les autres liens de la MÊME personne deviennent inutilisables
        con.execute("UPDATE jetons SET utilise = 1 WHERE client_id = ? AND utilisateur_id IS ?",
                    (row["client_id"], row["utilisateur_id"]))
        if row["utilisateur_id"]:
            con.execute("UPDATE utilisateurs SET mdp_hash = ? WHERE id = ? AND client_id = ?",
                        (generate_password_hash(mdp), row["utilisateur_id"], row["client_id"]))
        else:
            con.execute("UPDATE clients SET mdp_hash = ? WHERE id = ?",
                        (generate_password_hash(mdp), row["client_id"]))
        return row["client_id"]


def jeton_utilisateur(db_path, jeton):
    """Utilisateur rattaché visé par un lien (None pour le titulaire du compte)."""
    with connect(db_path) as con:
        row = con.execute("SELECT utilisateur_id FROM jetons WHERE empreinte = ?", (_empreinte(jeton or ""),)).fetchone()
    return row["utilisateur_id"] if row else None


# --- Utilisateurs rattachés à un compte client -------------------------------

def email_pris(db_path, email):
    with connect(db_path) as con:
        return bool(con.execute("SELECT 1 FROM clients WHERE email = ? UNION ALL SELECT 1 FROM utilisateurs WHERE email = ?",
                                ((email or "").strip(), (email or "").strip())).fetchone())


def creer_utilisateur(db_path, client_id, *, nom, email, peut_acheter=False, mdp=None, langue="fr"):
    """Ajoute une personne au compte (même solde, mêmes fichiers). Sans mot de passe : un mot de passe
    aléatoire est posé et l'appelant envoie une invitation."""
    nom = (nom or "").strip()[:80]
    email = (email or "").strip().lower()
    if not nom:
        raise ErreurCompte("Indiquez le nom de la personne.")
    if not email_valide(email):
        raise ErreurCompte("Adresse e-mail invalide.")
    if mdp is not None:
        _verifier_mdp(mdp)
    if email_pris(db_path, email):
        raise ErreurCompte("Un compte existe déjà avec cette adresse e-mail.")
    with connect(db_path) as con:
        n = con.execute("SELECT COUNT(*) n FROM utilisateurs WHERE client_id = ? AND actif = 1", (client_id,)).fetchone()["n"]
        if n >= UTILISATEURS_MAX:
            raise ErreurCompte(f"{UTILISATEURS_MAX} utilisateurs au maximum par compte.")
        try:
            return con.execute(
                "INSERT INTO utilisateurs (client_id, nom, email, mdp_hash, peut_acheter, langue, cree_le)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (client_id, nom, email, generate_password_hash(mdp or secrets.token_urlsafe(24)), int(bool(peut_acheter)),
                 (langue or "fr")[:5], _now())).lastrowid
        except sqlite3.IntegrityError:
            raise ErreurCompte("Un compte existe déjà avec cette adresse e-mail.")


def lister_utilisateurs(db_path, client_id):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT id, nom, email, peut_acheter, actif, cree_le, derniere_connexion FROM utilisateurs"
            " WHERE client_id = ? AND email NOT LIKE '%@invalid' ORDER BY nom", (client_id,))]


def get_utilisateur(db_path, utilisateur_id, client_id=None):
    sql, args = "SELECT * FROM utilisateurs WHERE id = ?", [utilisateur_id]
    if client_id is not None:
        sql += " AND client_id = ?"
        args.append(client_id)
    with connect(db_path) as con:
        row = con.execute(sql, args).fetchone()
    return dict(row) if row else None


def utilisateur_par_email(db_path, email):
    with connect(db_path) as con:
        row = con.execute("SELECT * FROM utilisateurs WHERE email = ?", ((email or "").strip(),)).fetchone()
    return dict(row) if row else None


def modifier_utilisateur(db_path, client_id, utilisateur_id, *, actif=None, peut_acheter=None):
    with connect(db_path) as con:
        if actif is not None:
            con.execute("UPDATE utilisateurs SET actif = ? WHERE id = ? AND client_id = ?",
                        (int(bool(actif)), utilisateur_id, client_id))
        if peut_acheter is not None:
            con.execute("UPDATE utilisateurs SET peut_acheter = ? WHERE id = ? AND client_id = ?",
                        (int(bool(peut_acheter)), utilisateur_id, client_id))


def supprimer_utilisateur(db_path, client_id, utilisateur_id):
    """Retire l'accès. Le nom reste sur les demandes qu'il a envoyées ; l'e-mail est libéré."""
    with connect(db_path) as con:
        con.execute("UPDATE utilisateurs SET actif = 0, email = ?, mdp_hash = ? WHERE id = ? AND client_id = ?",
                    (f"supprime-u{int(utilisateur_id)}@invalid", generate_password_hash(secrets.token_hex(32)),
                     utilisateur_id, client_id))
        con.execute("DELETE FROM jetons WHERE utilisateur_id = ?", (utilisateur_id,))


def changer_mdp_utilisateur(db_path, utilisateur_id, actuel, nouveau):
    u = get_utilisateur(db_path, utilisateur_id)
    if not u or not check_password_hash(u["mdp_hash"], actuel or ""):
        raise ErreurCompte("Mot de passe actuel incorrect.")
    _verifier_mdp(nouveau)
    with connect(db_path) as con:
        con.execute("UPDATE utilisateurs SET mdp_hash = ? WHERE id = ?", (generate_password_hash(nouveau), utilisateur_id))


def authentifier_compte(db_path, email, mdp):
    """Connexion à l'espace client : titulaire OU utilisateur rattaché.
    Renvoie (client, utilisateur ou None), ou None si identifiants incorrects."""
    c = authentifier(db_path, email, mdp) if client_par_email(db_path, email) else None
    if c:
        return c, None
    u = utilisateur_par_email(db_path, email)
    if not u:
        if not client_par_email(db_path, email):
            check_password_hash(_HASH_FACTICE, mdp or "")
        return None
    if not check_password_hash(u["mdp_hash"], mdp or "") or not u["actif"]:
        return None
    with connect(db_path) as con:
        con.execute("UPDATE utilisateurs SET derniere_connexion = ? WHERE id = ?", (_now(), u["id"]))
    return get_client(db_path, u["client_id"]), u


# --- Alertes SMS -------------------------------------------------------------

def normaliser_mobile(raw, pays="France"):
    """Numéro au format international (+33612345678). Les numéros français 06/07 sont convertis."""
    n = re.sub(r"[\s.\-()]", "", raw or "")
    if n.startswith("00"):
        n = "+" + n[2:]
    if re.fullmatch(r"0[67]\d{8}", n) and est_en_france(pays):
        n = "+33" + n[1:]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", n):
        raise ErreurCompte("Numéro de mobile invalide (ex. 06 12 34 56 78 ou +33 6 12 34 56 78).")
    return n


def changer_sms(db_path, client_id, mobile, actif):
    c = get_client(db_path, client_id)
    mobile = normaliser_mobile(mobile, c["pays"] if c else "France") if (mobile or "").strip() else ""
    if actif and not mobile:
        raise ErreurCompte("Indiquez un numéro de mobile pour recevoir les SMS.")
    with connect(db_path) as con:
        con.execute("UPDATE clients SET sms_mobile = ?, sms_actif = ? WHERE id = ?", (mobile, int(bool(actif)), client_id))


# --- Anti-force brute (mémoire du processus) ---------------------------------

class Limiteur:
    """Au-delà de `max_echecs` échecs en `fenetre` secondes pour une même clé, la clé est bloquée."""

    def __init__(self, max_echecs=5, fenetre=900):
        self.max = max_echecs
        self.fenetre = fenetre
        self._echecs = {}

    def _recents(self, cle, now):
        return [t for t in self._echecs.get(cle, []) if now - t < self.fenetre]

    def bloque(self, *cles):
        now = time.time()
        return any(len(self._recents(c, now)) >= self.max for c in cles)

    def echec(self, *cles):
        now = time.time()
        for c in cles:
            self._echecs[c] = self._recents(c, now) + [now]
        if len(self._echecs) > 10000:
            self._echecs = {k: v for k, v in self._echecs.items() if self._recents(k, now)}

    def reussite(self, *cles):
        for c in cles:
            self._echecs.pop(c, None)
