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
import datetime as dt
import sqlite3

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


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db(db_path):
    with connect(db_path) as con:
        con.executescript(SCHEMA)


def existe(db_path):
    with connect(db_path) as con:
        return con.execute("SELECT 1 FROM techniciens WHERE actif = 1 LIMIT 1").fetchone() is not None


def lister(db_path):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT id, identifiant, nom, role, actif, cree_le, derniere_connexion FROM techniciens ORDER BY nom")]


def get(db_path, tech_id):
    with connect(db_path) as con:
        r = con.execute("SELECT id, identifiant, nom, role, actif FROM techniciens WHERE id = ?", (tech_id,)).fetchone()
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


def modifier(db_path, tech_id, *, nom, role, actif, mdp=None):
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


def authentifier(db_path, identifiant, mdp):
    with connect(db_path) as con:
        r = con.execute("SELECT * FROM techniciens WHERE identifiant = ? AND actif = 1",
                        ((identifiant or "").strip(),)).fetchone()
        if not r or not check_password_hash(r["mdp_hash"], mdp or ""):
            return None
        con.execute("UPDATE techniciens SET derniere_connexion = ? WHERE id = ?", (_now(), r["id"]))
    return {"id": r["id"], "identifiant": r["identifiant"], "nom": r["nom"], "role": r["role"]}


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
