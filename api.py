"""
API revendeurs (JSON) : /api/v1/…

Authentification : en-tête  Authorization: Bearer <clé>  — clé générée par le client dans
Paramètres de son espace ; seule son empreinte SHA-256 est stockée. L'API est désactivée tant
que l'atelier ne l'a pas activée (Fileservice → Réglages).

Toutes les règles du site s'appliquent : prix recalculé par le serveur, remise du niveau,
crédits débités à l'envoi, livraison automatique éventuelle, e-mails.
"""
import datetime as dt
import hashlib
import secrets
import time

from flask import Blueprint, abort, current_app, g, jsonify, request, send_file

import catalogue
import comptes
import demandes
import fileservice
from comptes import ErreurCompte, connect

bp = Blueprint("api", __name__, url_prefix="/api/v1")

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    nom TEXT NOT NULL DEFAULT '',
    prefixe TEXT NOT NULL,
    empreinte TEXT NOT NULL UNIQUE,
    cree_le TEXT NOT NULL,
    derniere_utilisation TEXT,
    revoquee INTEGER NOT NULL DEFAULT 0
);
"""
LIMITE_MINUTE = 120
MAX_CLES = 5
_appels = {}


def init_db(db_path):
    with connect(db_path) as con:
        con.executescript(SCHEMA)


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _empreinte(cle):
    return hashlib.sha256(cle.encode()).hexdigest()


def creer_cle(db_path, client_id, nom=""):
    """Renvoie la clé en clair (affichée une seule fois)."""
    with connect(db_path) as con:
        n = con.execute("SELECT COUNT(*) n FROM api_cles WHERE client_id = ? AND revoquee = 0", (client_id,)).fetchone()["n"]
        if n >= MAX_CLES:
            raise ErreurCompte(f"{MAX_CLES} clés actives au maximum : révoquez-en une.")
        cle = "e85_" + secrets.token_hex(24)
        con.execute("INSERT INTO api_cles (client_id, nom, prefixe, empreinte, cree_le) VALUES (?, ?, ?, ?, ?)",
                    (client_id, (nom or "").strip()[:60], cle[:12], _empreinte(cle), _now()))
    return cle


def lister_cles(db_path, client_id):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT id, nom, prefixe, cree_le, derniere_utilisation FROM api_cles"
            " WHERE client_id = ? AND revoquee = 0 ORDER BY id DESC", (client_id,))]


def revoquer_cle(db_path, client_id, cle_id):
    with connect(db_path) as con:
        con.execute("UPDATE api_cles SET revoquee = 1 WHERE id = ? AND client_id = ?", (cle_id, client_id))


def _client_par_cle(db_path, cle):
    with connect(db_path) as con:
        r = con.execute("SELECT id, client_id FROM api_cles WHERE empreinte = ? AND revoquee = 0",
                        (_empreinte(cle),)).fetchone()
        if not r:
            return None
        con.execute("UPDATE api_cles SET derniere_utilisation = ? WHERE id = ?", (_now(), r["id"]))
    return r["client_id"]


def _erreur(message, code):
    return jsonify({"erreur": message}), code


@bp.before_request
def _auth():
    if not fileservice.reglages().get("api_active"):
        return _erreur("API désactivée par l'atelier.", 403)
    entete = request.headers.get("Authorization", "")
    cle = entete[7:].strip() if entete.lower().startswith("bearer ") else ""
    if not cle:
        return _erreur("Clé d'API manquante (en-tête Authorization: Bearer …).", 401)
    cid = _client_par_cle(current_app.config["FS_DB"], cle)
    c = comptes.get_client(current_app.config["FS_DB"], cid) if cid else None
    if not c or c["statut"] != "actif":
        return _erreur("Clé d'API invalide ou compte inactif.", 401)
    now = time.time()
    fenetre = [t for t in _appels.get(cid, []) if now - t < 60]
    if len(fenetre) >= LIMITE_MINUTE:
        return _erreur("Trop de requêtes : 120 par minute au maximum.", 429)
    _appels[cid] = fenetre + [now]
    g.client, g.utilisateur = c, None
    return None


def _demande_publique(d, detail=False):
    out = {"numero": d["numero"], "cree_le": d["cree_le"], "statut": d["statut"], "statut_label": d["statut_label"],
           "categorie": d["categorie"], "prestations": d["prestations"], "lignes": d["lignes"], "total": d["total"],
           "vehicule": d["vehicule"], "lecture": d["lecture"], "fichier": d["fichier_nom"],
           "livre_le": d["livre_le"], "telecharge_le": d["telecharge_le"],
           "motif_refus": d["motif_refus"] or None, "messages_non_lus": d.get("non_lus"), "express": bool(d.get("express"))}
    if detail:
        db = current_app.config["FS_DB"]
        out["livrables"] = [{"version": l["version"], "nom": l["nom"], "taille": l["taille"], "note": l["note"],
                             "cree_le": l["cree_le"], "url": f"/api/v1/demandes/{d['numero']}/livrables/{l['version']}"}
                            for l in demandes.livrables(db, d["id"])]
        out["messages"] = [{"auteur": m["auteur"], "texte": m["texte"], "piece_jointe": m["pj_nom"] or None,
                            "cree_le": m["cree_le"]}
                           for m in demandes.messages(db, d["id"], marquer_lus_pour="client")]
    return out


def _demande(numero):
    d = demandes.get_par_numero(current_app.config["FS_DB"], numero, g.client["id"])
    if not d:
        abort(404)
    return d


@bp.errorhandler(404)
def _introuvable(_e):
    return _erreur("Introuvable.", 404)


@bp.route("/compte")
def compte():
    c = g.client
    return jsonify({"societe": c["societe"], "email": c["email"], "credits": c["credits"], "niveau": c["niveau"],
                    "remise_pct": fileservice._remise_client()["remise"]})


@bp.route("/catalogue")
def api_catalogue():
    cats = []
    for c in catalogue.CATEGORIES:
        cats.append({**c, "prestations": [{k: p.get(k) for k in ("code", "nom", "desc", "prix", "prix_siege")}
                                          for p in catalogue.prestations_de(c["code"])]})
    return jsonify({"categories": cats, "packs": [{"nom": p["nom"], "categorie": k, "codes": sorted(p["codes"]),
                                                    "prix": p["prix"], "prix_siege": p.get("prix_siege")}
                                                   for k, lst in catalogue.PACKS.items() for p in lst],
                    "garanties": catalogue.GARANTIES, "retours": catalogue.RETOURS,
                    "express": catalogue.express(fileservice.reglages()),
                    "remise_pct": fileservice._remise_client()["remise"]})


@bp.route("/devis", methods=["POST"])
def api_devis():
    b = request.get_json(silent=True) or {}
    codes = b.get("prestations") if isinstance(b.get("prestations"), list) else []
    return jsonify(catalogue.devis(str(b.get("categorie", "")), [str(x) for x in codes][:20],
                                   siege=bool(b.get("siege")), garantie=str(b.get("garantie") or ""),
                                   express=catalogue.supplement_express(fileservice.reglages(), bool(b.get("express"))),
                                   **fileservice._remise_client()))


@bp.route("/demandes", methods=["GET"])
def api_liste():
    statut = request.args.get("statut")
    rows = demandes.lister(current_app.config["FS_DB"], g.client["id"],
                           statut=statut if statut in demandes.STATUTS else None)
    return jsonify({"demandes": [_demande_publique(d) for d in rows]})


@bp.route("/demandes", methods=["POST"])
def api_creer():
    """multipart/form-data : file (obligatoire), categorie, prestations (codes séparés par des virgules),
    marque, modele, moteur, annee, boite, km, vin, immat, outil, ecu, methode, siege, retour, garantie, commentaire,
    express (1 = traitement prioritaire, si proposé), annexes (0 à 4 fichiers complémentaires)."""
    f = request.files.get("file")
    if not f:
        return _erreur("Champ « file » manquant (lecture d'origine).", 400)
    codes = [c.strip() for c in (request.form.get("prestations") or "").split(",") if c.strip()]
    try:
        d, livre = fileservice.creer_demande(request.form, codes, f.filename, f.read(), source="api",
                                             annexes=fileservice._annexes_envoyees(request.files.getlist("annexes")))
    except ErreurCompte as e:
        return _erreur(str(e), 400)
    return jsonify({"demande": _demande_publique(d), "livree_automatiquement": livre,
                    "credits_restants": comptes.get_client(current_app.config["FS_DB"], g.client["id"])["credits"]}), 201


@bp.route("/demandes/<numero>")
def api_detail(numero):
    return jsonify(_demande_publique(_demande(numero), detail=True))


def _fichier(d, fichier, nom):
    p = demandes.chemin(current_app.config["FS_FILES"], d["id"], fichier)
    if not p:
        abort(404)
    return send_file(p, as_attachment=True, download_name=nom, mimetype="application/octet-stream")


@bp.route("/demandes/<numero>/original")
def api_original(numero):
    d = _demande(numero)
    return _fichier(d, "original_" + d["fichier_nom"], d["fichier_nom"])


@bp.route("/demandes/<numero>/livrables/<int:version>")
def api_livrable(numero, version):
    d = _demande(numero)
    liv = next((l for l in demandes.livrables(current_app.config["FS_DB"], d["id"]) if l["version"] == version), None)
    if not liv:
        abort(404)
    demandes.marquer_telecharge(current_app.config["FS_DB"], d["id"])
    return _fichier(d, liv["fichier"], f"{d['numero']}_v{version}_{liv['nom']}")


@bp.route("/demandes/<numero>/messages", methods=["POST"])
def api_message(numero):
    d = _demande(numero)
    b = request.get_json(silent=True) or {}
    try:
        demandes.ajouter_message(current_app.config["FS_DB"], current_app.config["FS_FILES"], d["id"], "client",
                                 (g.client["contact"] or g.client["societe"]) + " (API)", b.get("texte", ""))
    except ErreurCompte as e:
        return _erreur(str(e), 400)
    fileservice._mail_atelier(f"Message client sur {d['numero']} · {g.client['societe']} (API)",
                              f"{b.get('texte', '')}\n\nRéponse dans l'outil interne, onglet Fileservice.")
    return jsonify({"ok": True}), 201
