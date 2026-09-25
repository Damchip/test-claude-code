"""
Demandes du fileservice : fichiers envoyés par les clients, traitement atelier.

Même base que les comptes (data/fileservice.db). Les fichiers sont rangés dans
un dossier par demande : <fichiers>/<id>/original_<nom>, livre_v<n>_<nom>,
pj_<n>_<nom> (pièces jointes des messages).

Règles :
  - le prix est TOUJOURS recalculé ici depuis le catalogue (jamais celui du navigateur) ;
  - débit des crédits, création de la demande et écriture du fichier se font en
    une transaction : si l'un échoue, rien n'est gardé ;
  - un refus rembourse le client une seule fois.
"""
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil

from werkzeug.security import generate_password_hash

import catalogue
import comptes
from comptes import ErreurCompte, connect

STATUTS = {
    "recu": "Reçu",
    "en_cours": "En traitement",
    "attente": "Info requise",
    "pret": "Prêt",
    "refuse": "Refusé",
}
OUVERTS = ("recu", "en_cours", "attente")
TAILLE_MAX = 64 * 1024 * 1024
REVISION_JOURS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS demandes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero TEXT UNIQUE,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    cree_le TEXT NOT NULL,
    maj_le TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'recu',
    categorie TEXT NOT NULL,
    vehicule TEXT NOT NULL DEFAULT '{}',
    lecture TEXT NOT NULL DEFAULT '{}',
    prestations TEXT NOT NULL DEFAULT '[]',
    lignes TEXT NOT NULL DEFAULT '[]',
    total INTEGER NOT NULL,
    siege INTEGER NOT NULL DEFAULT 0,
    retour TEXT NOT NULL DEFAULT '',
    garantie TEXT NOT NULL DEFAULT '',
    commentaire TEXT NOT NULL DEFAULT '',
    fichier_nom TEXT NOT NULL,
    fichier_taille INTEGER NOT NULL,
    fichier_sha256 TEXT NOT NULL,
    detection TEXT NOT NULL DEFAULT '{}',
    motif_refus TEXT NOT NULL DEFAULT '',
    rembourse INTEGER NOT NULL DEFAULT 0,
    livre_le TEXT,
    telecharge_le TEXT
);
CREATE INDEX IF NOT EXISTS demandes_client ON demandes(client_id, id);
CREATE INDEX IF NOT EXISTS demandes_statut ON demandes(statut, id);
CREATE TABLE IF NOT EXISTS livrables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    demande_id INTEGER NOT NULL REFERENCES demandes(id),
    version INTEGER NOT NULL,
    nom TEXT NOT NULL,
    fichier TEXT NOT NULL,
    taille INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    cree_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    demande_id INTEGER NOT NULL REFERENCES demandes(id),
    auteur TEXT NOT NULL,              -- 'client' | 'atelier'
    auteur_nom TEXT NOT NULL DEFAULT '',
    texte TEXT NOT NULL DEFAULT '',
    pj_nom TEXT NOT NULL DEFAULT '',
    pj_fichier TEXT NOT NULL DEFAULT '',
    cree_le TEXT NOT NULL,
    lu INTEGER NOT NULL DEFAULT 0      -- lu par le destinataire
);
CREATE INDEX IF NOT EXISTS messages_demande ON messages(demande_id, id);
"""


def init_db(db_path):
    comptes.init_db(db_path)
    with connect(db_path) as con:
        con.executescript(SCHEMA)


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def nom_sur(nom, defaut="fichier.bin"):
    """Nom de fichier affichable et sûr (pas de chemin, pas de caractères spéciaux)."""
    nom = os.path.basename((nom or "").replace("\\", "/")).strip()
    nom = re.sub(r"[^\w.\- ()+]", "_", nom)[:120].strip(" .")
    return nom or defaut


def _dossier(files_dir, demande_id):
    d = os.path.join(files_dir, str(int(demande_id)))
    os.makedirs(d, exist_ok=True)
    return d


def chemin(files_dir, demande_id, fichier):
    """Chemin absolu d'un fichier stocké, en refusant toute sortie du dossier de la demande."""
    base = os.path.realpath(os.path.join(files_dir, str(int(demande_id))))
    p = os.path.realpath(os.path.join(base, os.path.basename(fichier or "")))
    if not p.startswith(base + os.sep) or not os.path.isfile(p):
        return None
    return p


def _decoder(row):
    if row is None:
        return None
    d = dict(row)
    for k in ("vehicule", "lecture", "detection"):
        d[k] = json.loads(d.get(k) or "{}")
    for k in ("prestations", "lignes"):
        d[k] = json.loads(d.get(k) or "[]")
    d["statut_label"] = STATUTS.get(d["statut"], d["statut"])
    return d


# --- Création ----------------------------------------------------------------

def creer(db_path, files_dir, client_id, *, categorie, prestations, siege=False, garantie="",
          retour="", vehicule=None, lecture=None, commentaire="", fichier_nom, contenu, detection=None,
          remise=0, niveau=""):
    if not contenu:
        raise ErreurCompte("Le fichier est vide.")
    if len(contenu) > TAILLE_MAX:
        raise ErreurCompte("Fichier trop volumineux (64 Mo maximum).")
    devis = catalogue.devis(categorie, prestations, siege=siege, garantie=garantie or None,
                            remise=remise, niveau=niveau)
    if devis["erreur"]:
        raise ErreurCompte(devis["erreur"])
    if not prestations or not devis["lignes"]:
        raise ErreurCompte("Choisissez au moins une prestation.")
    siege = bool(siege and devis["siege_possible"])
    retour = retour if siege and retour in {r["code"] for r in catalogue.RETOURS} else ""
    garantie = garantie if garantie in {g["code"] for g in catalogue.GARANTIES} else ""
    vehicule = {k: str(v).strip()[:80] for k, v in (vehicule or {}).items() if str(v).strip()}
    lecture = {k: str(v).strip()[:80] for k, v in (lecture or {}).items() if str(v).strip()}
    nom = nom_sur(fichier_nom)
    now = _now()
    total = devis["total"]

    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT credits, statut FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not row or row["statut"] != "actif":
            raise ErreurCompte("Compte inactif.")
        if row["credits"] < total:
            raise ErreurCompte(f"Solde insuffisant : {total} crédits nécessaires, {row['credits']} disponibles.")
        cur = con.execute(
            "INSERT INTO demandes (client_id, cree_le, maj_le, categorie, vehicule, lecture, prestations, lignes,"
            " total, siege, retour, garantie, commentaire, fichier_nom, fichier_taille, fichier_sha256, detection)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (client_id, now, now, categorie, json.dumps(vehicule, ensure_ascii=False),
             json.dumps(lecture, ensure_ascii=False), json.dumps(list(dict.fromkeys(prestations))),
             json.dumps(devis["lignes"], ensure_ascii=False), total, int(siege), retour, garantie,
             (commentaire or "").strip()[:2000], nom, len(contenu), hashlib.sha256(contenu).hexdigest(),
             json.dumps(detection or {}, ensure_ascii=False)))
        did = cur.lastrowid
        numero = f"F-{did:05d}"
        con.execute("UPDATE demandes SET numero = ? WHERE id = ?", (numero, did))
        con.execute("UPDATE clients SET credits = credits - ? WHERE id = ?", (total, client_id))
        libelle = f"Fichier {numero} · " + " + ".join(l["nom"] for l in devis["lignes"] if l["credits"] > 0)
        con.execute("INSERT INTO mouvements (client_id, date, libelle, montant) VALUES (?, ?, ?, ?)",
                    (client_id, now, libelle[:200], -total))
        dossier = _dossier(files_dir, did)
        try:
            with open(os.path.join(dossier, "original_" + nom), "wb") as fh:
                fh.write(contenu)
        except OSError:
            shutil.rmtree(dossier, ignore_errors=True)
            raise
    return did


# --- Lecture -----------------------------------------------------------------

def get(db_path, demande_id, client_id=None):
    sql = ("SELECT d.*, c.societe, c.email, c.contact, c.tel FROM demandes d"
           " JOIN clients c ON c.id = d.client_id WHERE d.id = ?")
    args = [demande_id]
    if client_id is not None:
        sql += " AND d.client_id = ?"
        args.append(client_id)
    with connect(db_path) as con:
        return _decoder(con.execute(sql, args).fetchone())


def get_par_numero(db_path, numero, client_id=None):
    with connect(db_path) as con:
        row = con.execute("SELECT id FROM demandes WHERE numero = ?", (numero,)).fetchone()
    return get(db_path, row["id"], client_id) if row else None


def lister(db_path, client_id=None, statut=None, limite=500):
    sql = ("SELECT d.*, c.societe, c.email, c.contact, c.tel,"
           " (SELECT COUNT(*) FROM messages m WHERE m.demande_id = d.id AND m.lu = 0 AND m.auteur = ?) AS non_lus"
           " FROM demandes d JOIN clients c ON c.id = d.client_id WHERE 1 = 1")
    # messages non lus : ceux de l'atelier pour le client, ceux du client pour l'atelier
    args = ["atelier" if client_id is not None else "client"]
    if client_id is not None:
        sql += " AND d.client_id = ?"
        args.append(client_id)
    if statut:
        sql += " AND d.statut = ?"
        args.append(statut)
    sql += " ORDER BY d.id DESC LIMIT ?"
    args.append(limite)
    with connect(db_path) as con:
        return [_decoder(r) for r in con.execute(sql, args).fetchall()]


def livrables(db_path, demande_id):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM livrables WHERE demande_id = ? ORDER BY version DESC", (demande_id,)).fetchall()]


def messages(db_path, demande_id, marquer_lus_pour=None):
    """Messages d'une demande. marquer_lus_pour='client' marque lus ceux de l'atelier (et inversement)."""
    with connect(db_path) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM messages WHERE demande_id = ? ORDER BY id", (demande_id,)).fetchall()]
        if marquer_lus_pour:
            autre = "atelier" if marquer_lus_pour == "client" else "client"
            con.execute("UPDATE messages SET lu = 1 WHERE demande_id = ? AND auteur = ?", (demande_id, autre))
    return rows


def stats(db_path, client_id=None):
    filtre, args = ("WHERE client_id = ?", [client_id]) if client_id is not None else ("", [])
    with connect(db_path) as con:
        counts = {k: 0 for k in STATUTS}
        for r in con.execute(f"SELECT statut, COUNT(*) n FROM demandes {filtre} GROUP BY statut", args):
            counts[r["statut"]] = r["n"]
        depuis = (dt.datetime.now() - dt.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        et = "AND" if filtre else "WHERE"
        delai = con.execute(
            f"SELECT AVG((julianday(livre_le) - julianday(cree_le)) * 1440) m FROM demandes {filtre}"
            f" {et} livre_le IS NOT NULL AND cree_le >= ?", args + [depuis]).fetchone()["m"]
        mois = dt.datetime.now().strftime("%Y-%m")
        livres = con.execute(
            f"SELECT COUNT(*) n FROM demandes {filtre} {et} livre_le IS NOT NULL AND substr(livre_le, 1, 7) = ?",
            args + [mois]).fetchone()["n"]
    return {"counts": counts, "ouverts": sum(counts[k] for k in OUVERTS),
            "delai_moyen": round(delai) if delai is not None else None, "livres_mois": livres}


# --- Actions -----------------------------------------------------------------

def ajouter_message(db_path, files_dir, demande_id, auteur, auteur_nom, texte, pj_nom="", pj_contenu=None):
    if auteur not in ("client", "atelier"):
        raise ErreurCompte("Auteur inconnu.")
    texte = (texte or "").strip()[:4000]
    if not texte and not pj_contenu:
        raise ErreurCompte("Message vide.")
    if pj_contenu is not None and len(pj_contenu) > TAILLE_MAX:
        raise ErreurCompte("Pièce jointe trop volumineuse (64 Mo maximum).")
    now = _now()
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM demandes WHERE id = ?", (demande_id,)).fetchone():
            raise ErreurCompte("Demande introuvable.")
        cur = con.execute("INSERT INTO messages (demande_id, auteur, auteur_nom, texte, cree_le) VALUES (?, ?, ?, ?, ?)",
                          (demande_id, auteur, (auteur_nom or "")[:80], texte, now))
        if pj_contenu:
            nom = nom_sur(pj_nom, "piece_jointe.bin")
            fichier = f"pj_{cur.lastrowid}_{nom}"
            with open(os.path.join(_dossier(files_dir, demande_id), fichier), "wb") as fh:
                fh.write(pj_contenu)
            con.execute("UPDATE messages SET pj_nom = ?, pj_fichier = ? WHERE id = ?", (nom, fichier, cur.lastrowid))
        # le client répond à une demande d'info : la demande repart en traitement
        if auteur == "client":
            con.execute("UPDATE demandes SET statut = 'en_cours', maj_le = ? WHERE id = ? AND statut = 'attente'",
                        (now, demande_id))
        else:
            con.execute("UPDATE demandes SET maj_le = ? WHERE id = ?", (now, demande_id))
        return cur.lastrowid


def changer_statut(db_path, demande_id, statut):
    """Statuts manuels de l'atelier : en_cours / attente (prêt et refusé passent par livrer/refuser)."""
    if statut not in ("recu", "en_cours", "attente"):
        raise ErreurCompte("Statut non modifiable ici.")
    with connect(db_path) as con:
        cur = con.execute("UPDATE demandes SET statut = ?, maj_le = ? WHERE id = ? AND statut != 'refuse'",
                          (statut, _now(), demande_id))
        if not cur.rowcount:
            raise ErreurCompte("Demande introuvable ou déjà refusée.")


def livrer(db_path, files_dir, demande_id, nom, contenu, note=""):
    """Dépose une version du fichier modifié ; la demande passe « Prêt »."""
    if not contenu:
        raise ErreurCompte("Fichier livré vide.")
    nom = nom_sur(nom)
    now = _now()
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        d = con.execute("SELECT statut FROM demandes WHERE id = ?", (demande_id,)).fetchone()
        if not d:
            raise ErreurCompte("Demande introuvable.")
        if d["statut"] == "refuse":
            raise ErreurCompte("Demande refusée : impossible de livrer.")
        version = con.execute("SELECT COALESCE(MAX(version), 0) + 1 v FROM livrables WHERE demande_id = ?",
                              (demande_id,)).fetchone()["v"]
        fichier = f"livre_v{version}_{nom}"
        with open(os.path.join(_dossier(files_dir, demande_id), fichier), "wb") as fh:
            fh.write(contenu)
        con.execute("INSERT INTO livrables (demande_id, version, nom, fichier, taille, note, cree_le)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (demande_id, version, nom, fichier, len(contenu), (note or "").strip()[:500], now))
        con.execute("UPDATE demandes SET statut = 'pret', maj_le = ?, livre_le = COALESCE(livre_le, ?)"
                    " WHERE id = ?", (now, now, demande_id))
        return version


def refuser(db_path, demande_id, motif):
    """Refus avec remboursement intégral (une seule fois)."""
    motif = (motif or "").strip()[:500]
    if not motif:
        raise ErreurCompte("Indiquez le motif du refus (il est affiché au client).")
    now = _now()
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        d = con.execute("SELECT client_id, numero, total, statut, rembourse, livre_le FROM demandes WHERE id = ?",
                        (demande_id,)).fetchone()
        if not d:
            raise ErreurCompte("Demande introuvable.")
        if d["statut"] == "pret" or d["livre_le"]:
            # y compris pendant une révision : le client a déjà reçu un fichier
            raise ErreurCompte("Fichier déjà livré : refus impossible (pour un geste commercial, recrédite depuis l'onglet Clients).")
        con.execute("UPDATE demandes SET statut = 'refuse', motif_refus = ?, maj_le = ?, rembourse = 1 WHERE id = ?",
                    (motif, now, demande_id))
        if not d["rembourse"]:
            con.execute("UPDATE clients SET credits = credits + ? WHERE id = ?", (d["total"], d["client_id"]))
            con.execute("INSERT INTO mouvements (client_id, date, libelle, montant) VALUES (?, ?, ?, ?)",
                        (d["client_id"], now, f"Remboursement {d['numero']} · {motif}"[:200], d["total"]))
        return d["total"]


def marquer_telecharge(db_path, demande_id):
    with connect(db_path) as con:
        con.execute("UPDATE demandes SET telecharge_le = COALESCE(telecharge_le, ?) WHERE id = ?",
                    (_now(), demande_id))


def revision_possible(demande, derniere_livraison):
    if demande["statut"] != "pret" or not derniere_livraison:
        return False
    livre = dt.datetime.strptime(derniere_livraison["cree_le"], "%Y-%m-%d %H:%M:%S")
    return dt.datetime.now() - livre <= dt.timedelta(days=REVISION_JOURS)


def demander_revision(db_path, files_dir, demande_id, client_id, auteur_nom, texte):
    d = get(db_path, demande_id, client_id)
    if not d:
        raise ErreurCompte("Demande introuvable.")
    liv = livrables(db_path, demande_id)
    if not revision_possible(d, liv[0] if liv else None):
        raise ErreurCompte(f"Révision possible uniquement dans les {REVISION_JOURS} jours suivant la livraison.")
    texte = (texte or "").strip()
    if not texte:
        raise ErreurCompte("Décrivez ce qui doit être revu.")
    ajouter_message(db_path, files_dir, demande_id, "client", auteur_nom, "Demande de révision : " + texte)
    with connect(db_path) as con:
        con.execute("UPDATE demandes SET statut = 'en_cours', maj_le = ? WHERE id = ?", (_now(), demande_id))


# --- RGPD --------------------------------------------------------------------

def export_client(db_path, client_id):
    """Toutes les données d'un client, pour son droit d'accès / de portabilité."""
    c = comptes.get_client(db_path, client_id)
    if not c:
        return None
    c.pop("mdp_hash", None)
    with connect(db_path) as con:
        factures = [dict(r) for r in con.execute(
            "SELECT numero, date, designation, credits, ht, tva, ttc, paiement FROM factures WHERE client_id = ?"
            " ORDER BY id", (client_id,))]
    out = []
    for d in lister(db_path, client_id, limite=100000):
        out.append({k: d[k] for k in ("numero", "cree_le", "statut", "categorie", "vehicule", "lecture", "lignes",
                                       "total", "commentaire", "fichier_nom", "motif_refus", "livre_le")}
                   | {"messages": [{k: m[k] for k in ("auteur", "texte", "pj_nom", "cree_le")}
                                   for m in messages(db_path, d["id"])]})
    return {"export_du": _now(), "compte": c, "mouvements": comptes.mouvements(db_path, client_id, limite=100000),
            "factures": factures, "demandes": out}


def anonymiser_client(db_path, files_dir, client_id):
    """Droit à l'effacement : supprime les données personnelles et les fichiers du client.
    Les factures (identité figée) et les montants des demandes sont conservés : obligation
    comptable de 10 ans."""
    c = comptes.get_client(db_path, client_id)
    if not c:
        raise ErreurCompte("Client introuvable.")
    ids = [r["id"] for r in lister(db_path, client_id, limite=100000)]
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        if c["credits"]:
            con.execute("INSERT INTO mouvements (client_id, date, libelle, montant) VALUES (?, ?, ?, ?)",
                        (client_id, _now(), "Solde annulé (compte supprimé)", -c["credits"]))
        con.execute(
            "UPDATE clients SET societe = ?, siret = '', tva = '', contact = '', email = ?, tel = '', adresse = '',"
            " code_postal = '', ville = '', pays = '', mdp_hash = ?, statut = 'bloque', credits = 0 WHERE id = ?",
            (f"Compte supprimé n°{client_id}", f"supprime-{client_id}@invalid",
             generate_password_hash(secrets.token_hex(32)), client_id))
        con.execute("DELETE FROM jetons WHERE client_id = ?", (client_id,))
        for did in ids:
            con.execute("UPDATE demandes SET vehicule = '{}', lecture = '{}', commentaire = '', detection = '{}',"
                        " fichier_nom = 'supprime.bin' WHERE id = ?", (did,))
            con.execute("UPDATE messages SET texte = '', pj_nom = '', pj_fichier = '', auteur_nom = '' WHERE demande_id = ?",
                        (did,))
            con.execute("UPDATE livrables SET nom = 'supprime.bin', fichier = '', note = '' WHERE demande_id = ?", (did,))
    for did in ids:
        shutil.rmtree(os.path.join(files_dir, str(int(did))), ignore_errors=True)
    return len(ids)


def alertes(db_path):
    """Instantané léger pour les alertes de l'outil interne (interrogé toutes les ~20 s)."""
    with connect(db_path) as con:
        r = con.execute(
            "SELECT (SELECT COALESCE(MAX(id), 0) FROM demandes) AS derniere_demande,"
            " (SELECT COALESCE(MAX(id), 0) FROM messages WHERE auteur = 'client') AS dernier_message,"
            " (SELECT COUNT(*) FROM demandes WHERE statut IN ('recu', 'en_cours')) AS a_traiter,"
            " (SELECT COUNT(*) FROM messages m JOIN demandes d ON d.id = m.demande_id"
            "   WHERE m.auteur = 'client' AND m.lu = 0 AND d.statut != 'refuse') AS non_lus,"
            " (SELECT COUNT(*) FROM clients WHERE statut = 'en_attente') AS inscriptions").fetchone()
        return dict(r)

