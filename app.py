"""
Serveur local Carto Matcher.

Lance une petite interface web accessible UNIQUEMENT depuis ta machine
(127.0.0.1). Rien n'est exposé sur le réseau. Démarrage :

    python app.py

puis ouvre http://127.0.0.1:5000 dans ton navigateur.
"""

import json
import io
import re
import os
import sys
import secrets
import threading
import time
import zlib
from datetime import timedelta

from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from matcher import ai, atelier, batch, import_complet, db, dossiers as dos, engine, extract, importer, maps as mapsmod, pack as packmod, patch as pmod
import catalogue
import comptes
import demandes
import equipe
import factures
import livraison_auto
import mailer
import mise_a_jour
import passerelle
import modeles
import pages_legales
import push
import relances
import sante
import sauvegarde_externe
import sms
import statistiques
import stripe_api
import taches
import traductions

app = Flask(__name__)
# Filtres d'affichage partagés avec l'espace client (facture vue par l'atelier)
import fileservice as _fs_vues  # noqa: E402
app.jinja_env.filters.update(euros=_fs_vues._fmt_euros, date_fr=_fs_vues._fmt_date, credits=_fs_vues._fmt_credits,
                             taille=_fs_vues._fmt_taille)
APP_VERSION = "1.66.0"
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 Mo — même plafond que le portail
DB_PATH = os.environ.get("CARTO_DB", db.DEFAULT_DB)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def resolve_api_key():
    """Clé saisie dans l'UI (config locale) en priorité, sinon variable d'env."""
    return (load_config().get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")).strip()


# --- Clé de session : générée une fois, stockée localement (nécessaire pour
#     que la connexion reste active entre les requêtes). Ne protège rien en
#     elle-même : ne sert qu'à signer le cookie de session. ---
def get_secret_key():
    cfg = load_config()
    key = cfg.get("secret_key")
    if not key:
        key = secrets.token_hex(32)
        cfg["secret_key"] = key
        save_config(cfg)
    return key


app.secret_key = get_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
PROD = os.environ.get("CARTO_PROD") == "1"   # outil publié sur internet (O2switch…) : HTTPS et comptes obligatoires
if PROD:
    app.config["SESSION_COOKIE_SECURE"] = True


def access_password_set():
    return bool(load_config().get("access_password_hash"))


def check_access_password(pw):
    h = load_config().get("access_password_hash")
    return bool(h) and check_password_hash(h, pw)


def _safe_next(raw, fallback):
    """Uniquement un chemin relatif local — bloque open-redirect (//evil, https://…)."""
    raw = (raw or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or ":" in raw:
        return fallback
    return raw


@app.before_request
def _refuser_requetes_inter_sites():
    """Anti-CSRF de l'outil interne : une page d'un autre site ouverte sur le poste de l'atelier
    ne doit pas pouvoir envoyer de formulaire ici (livrer un fichier, créditer un client…).
    Les navigateurs indiquent l'origine d'une requête (Sec-Fetch-Site / Origin) : on refuse toute
    écriture qui ne vient pas de l'outil lui-même."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.headers.get("Sec-Fetch-Site", "") == "cross-site":
        return jsonify({"error": "Requête refusée (origine externe)."}), 403
    origin = request.headers.get("Origin")
    if origin and origin != "null":
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc != request.host:
            return jsonify({"error": "Requête refusée (origine externe)."}), 403
    elif origin == "null":
        return jsonify({"error": "Requête refusée (origine externe)."}), 403
    return None


# Actions réservées aux administrateurs quand des comptes atelier existent
ADMIN_ENDPOINTS = {"settings_set", "portal_config_set", "clients_supprimer", "clients_credits", "clients_facture",
                   "fs_reglages_set", "clients_smtp_set", "backups_restore", "backups_import",
                   "equipe_creer", "equipe_modifier", "equipe_securite", "fs_sauvegarde_externe", "fs_sms_test",
                   "maj_etat", "maj_verifier", "maj_installer", "maj_zip", "maj_revenir", "maj_reglages",
                   "fs_passerelle_liste", "fs_passerelle_creer", "fs_passerelle_revoquer",
                   "enligne_reglages_get", "enligne_reglages_set", "enligne_test", "enligne_demandes",
                   "enligne_preparer", "enligne_livrer_auto", "enligne_original", "enligne_livrer", "enligne_traitement",
                   "enligne_synchroniser", "fs_tarifs", "fs_tarifs_set", "files_liberer"}


def _equipe_active():
    try:
        equipe.init_db(FS_DB)
        return equipe.existe(FS_DB)
    except Exception:
        return False


# Tant que la double authentification obligatoire n'est pas activée par le technicien, seules ces routes répondent
ENDPOINTS_SANS_DOUBLE_AUTH = {"index", "logout", "equipe_liste", "equipe_2fa_debut", "equipe_2fa_activer"}


def exiger_double_auth():
    return bool(load_config().get("exiger_double_auth"))


LIMITEUR_PASSERELLE = comptes.Limiteur(max_echecs=20, fenetre=900)


@app.before_request
def _require_login():
    g.tech = None
    g.passerelle = None
    if request.path.startswith("/passerelle/v1/"):
        # PC de l'atelier : clé de passerelle (en-tête Authorization), jamais de session
        ip = "passerelle:" + (request.remote_addr or "?")
        if LIMITEUR_PASSERELLE.bloque(ip):
            return jsonify({"error": "Trop de tentatives, réessaie plus tard."}), 429
        entete = request.headers.get("Authorization", "")
        _fs_init()
        cle = passerelle.verifier_cle(FS_DB, entete[7:].strip() if entete.lower().startswith("bearer ") else "",
                                      request.remote_addr or "")
        if not cle:
            LIMITEUR_PASSERELLE.echec(ip)
            return jsonify({"error": "Clé de passerelle refusée (révoquée ou invalide)."}), 401
        g.passerelle = cle
        return None
    if request.endpoint in ("login", "static", "sante"):
        return None
    if _equipe_active():
        t = equipe.get(FS_DB, session.get("tech_id") or 0)
        if t and t["actif"]:
            g.tech = t
            if exiger_double_auth() and not t["double_auth"] and request.endpoint not in ENDPOINTS_SANS_DOUBLE_AUTH:
                return jsonify({"error": "Active d'abord la double authentification (onglet Fileservice → Équipe).",
                                "double_auth_requise": True}), 403
            if request.endpoint in ADMIN_ENDPOINTS and t["role"] != "admin":
                return jsonify({"error": "Action réservée à un administrateur de l'atelier."}), 403
            return None
        session.pop("tech_id", None)
    elif PROD:
        # en ligne, jamais d'accès libre ni de mot de passe unique : le premier compte se crée en ligne de commande
        return render_template("login.html", error=None, version=APP_VERSION, equipe=True, installation=True), 503
    elif not access_password_set():
        return None  # ni comptes ni mot de passe -> accès libre (comportement d'origine)
    elif session.get("authed"):
        return None
    if request.endpoint == "index":
        return redirect(url_for("login", next=request.path))
    return jsonify({"error": "Session expirée, reconnecte-toi."}), 401


# Journal des actions : libellé par route (écritures réussies seulement)
JOURNAL_ACTIONS = {
    "fs_message": "Message au client", "fs_livrer": "Livraison", "fs_livrer_auto": "Livraison en un clic",
    "fs_refuser": "Refus + remboursement", "fs_statut": "Changement de statut",
    "clients_statut": "Statut client", "clients_creer": "Compte client créé", "clients_inviter": "Invitation renvoyée", "clients_credits": "Crédits (ajustement)", "clients_facture": "Facture hors ligne",
    "clients_supprimer": "Suppression de compte (RGPD)", "clients_niveau": "Niveau client",
    "fs_reglages_set": "Réglages fileservice", "clients_smtp_set": "Réglages e-mail",
    "equipe_creer": "Compte atelier créé", "equipe_modifier": "Compte atelier modifié",
    "passerelle_livrer": "Livraison (PC atelier)", "passerelle_statut": "Changement de statut (PC atelier)",
    "fs_passerelle_creer": "Clé de passerelle PC créée", "fs_passerelle_revoquer": "Clé de passerelle PC révoquée",
    "maj_installer": "Mise à jour du logiciel (GitHub)", "maj_zip": "Mise à jour du logiciel (fichier .zip)",
    "maj_revenir": "Retour à la version précédente", "maj_reglages": "Réglages de mise à jour",
    "fs_modeles_enregistrer": "Réponse type enregistrée", "fs_modeles_supprimer": "Réponse type supprimée",
    "equipe_2fa_activer": "Double authentification activée", "equipe_2fa_desactiver": "Double authentification retirée",
    "equipe_securite": "Double authentification obligatoire",
    "settings_set": "Réglages de l'outil", "fs_tarifs_set": "Tarifs modifiés", "backups_restore": "Restauration de la bibliothèque",
}


@app.after_request
def _journaliser(resp):
    ep = request.endpoint
    if request.method != "POST" or ep not in JOURNAL_ACTIONS or resp.status_code >= 400:
        return resp
    try:
        b = request.get_json(silent=True) or {}
        cible, detail = "", ""
        did = (request.view_args or {}).get("did")
        if did:
            d = demandes.get(FS_DB, did)
            cible = f"{d['numero']} · {d['societe']}" if d else f"demande {did}"
        elif b.get("id") and ep.startswith("clients_"):
            c = comptes.get_client(FS_DB, int(b["id"]))
            cible = c["societe"] if c else f"client {b['id']}"
        elif b.get("identifiant"):
            cible = b["identifiant"]
        elif b.get("societe"):
            cible = b["societe"]
        elif b.get("titre"):
            cible = b["titre"]
        detail = " · ".join(str(x) for x in (b.get("statut"), b.get("motif"), b.get("montant"), b.get("libelle"),
                                             b.get("niveau"), b.get("credits") and f"{b.get('credits')} cr.",
                                             b.get("ht") and f"{b.get('ht')} € HT", b.get("reference"),
                                             request.form.get("note")) if x)
        qui = g.tech["nom"] if getattr(g, "tech", None) else (f"PC · {g.passerelle['nom']}" if getattr(g, "passerelle", None) else "")
        equipe.noter(FS_DB, qui, JOURNAL_ACTIONS[ep], cible, detail)
    except Exception:
        pass
    return resp


DOUBLE_AUTH_DELAI = 300   # secondes pour saisir le code après le mot de passe


def _ouvrir_session_tech(tech_id):
    session.clear()
    session["tech_id"] = tech_id
    session.permanent = True
    return redirect(_safe_next(request.args.get("next"), url_for("index")))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    avec_equipe = _equipe_active()
    if PROD and not avec_equipe:
        return render_template("login.html", error=None, version=APP_VERSION, equipe=True, installation=True), 503
    attente = session.get("tech_2fa")
    if attente and time.time() - session.get("tech_2fa_t", 0) > DOUBLE_AUTH_DELAI:
        session.pop("tech_2fa", None)
        attente = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        cles = ("ip:" + (request.remote_addr or "?"),)
        if attente and "code" in request.form:
            cles += (f"code:{attente}",)   # limite aussi par compte : le code à 6 chiffres ne se devine pas depuis plusieurs IP
        if LIMITEUR_OUTIL.bloque(*cles):
            error = "Trop de tentatives, réessaie dans 15 minutes."
        elif avec_equipe and attente and "code" in request.form:
            # 2e étape : code de l'application (ou code de secours)
            if equipe.verifier_second_facteur(FS_DB, attente, request.form.get("code", "")):
                LIMITEUR_OUTIL.reussite(*cles)
                return _ouvrir_session_tech(attente)
            LIMITEUR_OUTIL.echec(*cles)
            error = "Code incorrect ou déjà utilisé."
        elif avec_equipe:
            t = equipe.authentifier(FS_DB, request.form.get("identifiant", ""), pw)
            if t and t["double_auth"]:
                session.clear()
                session["tech_2fa"], session["tech_2fa_t"] = t["id"], time.time()
                return render_template("login.html", error=None, version=APP_VERSION, equipe=True, code=True)
            if t:
                LIMITEUR_OUTIL.reussite(*cles)
                return _ouvrir_session_tech(t["id"])
            LIMITEUR_OUTIL.echec(*cles)
            error = "Identifiant ou mot de passe incorrect."
        elif check_access_password(pw):
            LIMITEUR_OUTIL.reussite(*cles)
            session["authed"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next"), url_for("index")))
        else:
            LIMITEUR_OUTIL.echec(*cles)
            error = "Mot de passe incorrect."
    code = bool(avec_equipe and session.get("tech_2fa") and request.args.get("recommencer") is None)
    if request.args.get("recommencer") is not None:
        session.pop("tech_2fa", None)
    return render_template("login.html", error=error, version=APP_VERSION, equipe=avec_equipe, code=code)


@app.route("/logout")
def logout():
    session.pop("authed", None)
    session.pop("tech_id", None)
    session.pop("tech_2fa", None)
    return redirect(url_for("login"))


LIMITEUR_OUTIL = comptes.Limiteur(max_echecs=8, fenetre=900)


def _auteur_nom():
    """Signature des messages : le technicien connecté, sinon le champ « Signature »."""
    if getattr(g, "tech", None):
        return g.tech["nom"]
    if getattr(g, "passerelle", None):
        return f"PC · {g.passerelle['nom']}"
    return (request.form.get("auteur") or (request.get_json(silent=True) or {}).get("auteur") or "").strip()[:40]


@app.route("/equipe")
def equipe_liste():
    _fs_init()
    moi = dict(g.tech) if g.tech else None
    if moi:
        moi["double_auth"] = bool(moi["double_auth"])
        moi["codes_secours"] = equipe.codes_secours_restants(FS_DB, moi["id"]) if moi["double_auth"] else 0
    return jsonify({"moi": moi, "active": equipe.existe(FS_DB), "exiger_double_auth": exiger_double_auth(),
                    "techniciens": equipe.lister(FS_DB) if (not moi or moi["role"] == "admin") else [],
                    "roles": list(equipe.ROLES)})


@app.route("/equipe/2fa/debut", methods=["POST"])
def equipe_2fa_debut():
    """Nouveau secret (gardé en session tant qu'il n'est pas confirmé par un code)."""
    if not g.tech:
        return jsonify({"error": "Crée d'abord ton compte atelier."}), 400
    secret = equipe.nouveau_secret()
    session["totp_en_cours"] = secret
    nom = (load_portal_config().get("shop_name") or "E85-FRANCE") + " atelier"
    return jsonify({"secret": secret, "uri": equipe.uri_otpauth(secret, g.tech["identifiant"], nom)})


@app.route("/equipe/2fa/activer", methods=["POST"])
def equipe_2fa_activer():
    secret = session.get("totp_en_cours")
    if not g.tech or not secret:
        return jsonify({"error": "Recommence la configuration (bouton « Activer »)."}), 400
    try:
        codes = equipe.activer_double_auth(FS_DB, g.tech["id"], secret, (request.json or {}).get("code"))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    session.pop("totp_en_cours", None)
    return jsonify({"ok": True, "codes_secours": codes})


@app.route("/equipe/2fa/desactiver", methods=["POST"])
def equipe_2fa_desactiver():
    b = request.json or {}
    if not g.tech:
        return jsonify({"error": "Aucun compte connecté."}), 400
    if exiger_double_auth():
        return jsonify({"error": "La double authentification est obligatoire dans cet atelier."}), 400
    if not equipe.verifier_mdp(FS_DB, g.tech["id"], b.get("mdp")):
        return jsonify({"error": "Mot de passe incorrect."}), 400
    equipe.desactiver_double_auth(FS_DB, g.tech["id"])
    return jsonify({"ok": True})


@app.route("/equipe/securite", methods=["POST"])
def equipe_securite():
    """Double authentification obligatoire pour tous les comptes (administrateur)."""
    b = request.json or {}
    exiger = bool(b.get("exiger_double_auth"))
    if exiger and g.tech and not g.tech["double_auth"]:
        return jsonify({"error": "Active d'abord la double authentification sur ton propre compte."}), 400
    cfg = load_config()
    cfg["exiger_double_auth"] = exiger
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/equipe/creer", methods=["POST"])
def equipe_creer():
    _fs_init()
    b = request.json or {}
    premier = not equipe.existe(FS_DB)
    try:
        tid = equipe.creer(FS_DB, b.get("identifiant"), b.get("nom"), "admin" if premier else b.get("role"),
                           b.get("mdp") or "")
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    if premier:
        # le premier compte est administrateur et reste connecté : sinon on s'enfermerait dehors
        session["tech_id"] = tid
        session.permanent = True
    return jsonify({"ok": True, "premier": premier})


@app.route("/equipe/modifier", methods=["POST"])
def equipe_modifier():
    b = request.json or {}
    try:
        equipe.modifier(FS_DB, int(b.get("id") or 0), nom=b.get("nom"), role=b.get("role"),
                        actif=bool(b.get("actif", True)), mdp=b.get("mdp") or None,
                        retirer_double_auth=bool(b.get("retirer_double_auth")))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/equipe/journal")
def equipe_journal():
    _fs_init()
    return jsonify({"journal": equipe.journal(FS_DB, recherche=(request.args.get("q") or "").strip()[:60])})


DATA_DIR = os.path.dirname(os.path.abspath(DB_PATH))
SCAN_STATE_PATH = os.path.join(DATA_DIR, "scan_state.json")


def _norm_path(p):
    return os.path.normcase(os.path.abspath(p))


def load_scan_state():
    try:
        with open(SCAN_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_last_scan(path):
    return load_scan_state().get(_norm_path(path))


def set_last_scan(path, ts):
    st = load_scan_state()
    st[_norm_path(path)] = ts
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCAN_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


# Mémoire de la dernière analyse — PAR SESSION, pas globale (LAN multi-postes).
_LAST_LOCK = threading.Lock()
_LAST_BY_SID = {}
_LAST_MAX = 64


def _session_id():
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_hex(16)
        session["sid"] = sid
        session.permanent = True
    return sid


def _put_last(result, data=None):
    sid = _session_id()
    with _LAST_LOCK:
        _LAST_BY_SID[sid] = {"result": result, "history": [], "ts": time.time(), "data": data}
        if len(_LAST_BY_SID) > _LAST_MAX:
            oldest = min(_LAST_BY_SID.items(), key=lambda kv: kv[1]["ts"])[0]
            if oldest != sid:
                _LAST_BY_SID.pop(oldest, None)


def _get_last():
    with _LAST_LOCK:
        return _LAST_BY_SID.get(_session_id())


@app.route("/")
def index():
    db.init_db(DB_PATH)
    return render_template("index.html", db_size=db.count(DB_PATH), version=APP_VERSION, prod=PROD,
                           distants=_fichiers_distants())


@app.route("/analyze", methods=["POST"])
def analyze():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Aucun fichier reçu"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "Fichier vide"}), 400
    relpath = (request.form.get("relpath") or "").strip() or f.filename
    result = engine.match(data, DB_PATH, path=relpath)
    result["filename"] = f.filename
    _put_last(result, data)
    payload = {k: v for k, v in result.items() if k not in ("minhash", "minhash_v1")}
    return jsonify(payload)


@app.route("/chat", methods=["POST"])
def chat():
    last = _get_last()
    if not last or not last.get("result"):
        return jsonify({"error": "Analyse un fichier d'abord"}), 400
    question = (request.json or {}).get("question", "")
    out = ai.explain(last["result"], question, last["history"], api_key=resolve_api_key())
    if question:
        last["history"].append({"role": "user", "content": question})
        last["history"].append({"role": "assistant", "content": out["text"]})
    return jsonify(out)


@app.route("/save", methods=["POST"])
def save():
    """Enregistre le dernier fichier analysé comme nouvelle solution en base."""
    last = _get_last()
    if not last or not last.get("result"):
        return jsonify({"error": "Rien à enregistrer"}), 400
    body = request.json or {}
    r = last["result"]
    ids = r["incoming"]["candidate_ids"]
    nm = r["incoming"].get("name_meta") or {}
    name_vehicle = " ".join(x for x in [nm.get("brand"), nm.get("vehicle")] if x).strip()
    new_id = db.add_solution(
        DB_PATH,
        ecu_version=body.get("ecu_version") or r["incoming"].get("best_ecu_version") or (ids[0] if ids else ""),
        ecu_hw=body.get("ecu_hw", ""),
        ecu_platform=body.get("ecu_platform") or (r["incoming"].get("platform") or ""),
        manufacturer=body.get("manufacturer") or (r["incoming"].get("manufacturer") or ""),
        vehicle_label=body.get("vehicle_label") or name_vehicle,
        solution_type=body.get("solution_type") or (nm.get("solution_type") or ""),
        tested_status=body.get("tested_status", "a_confirmer"),
        stock_sha256=r["incoming"].get("sha256_body") or r["incoming"]["sha256"],
        stock_size=r["incoming"].get("body_size") or r["incoming"]["size"],
        minhash=r.get("minhash"),
        minhash_ver=r.get("minhash_ver") or 2,
        notes=body.get("notes", ""),
    )
    raw = last.get("data")
    if raw:
        dest = os.path.join(db.files_root(DB_PATH), f"{int(new_id):06d}")
        os.makedirs(dest, exist_ok=True)
        ori_path = os.path.join(dest, "original.bin")
        with open(ori_path, "wb") as fh:
            fh.write(raw)
        db.archive_files(DB_PATH, new_id, ori_path, "")
    return jsonify({"ok": True, "id": new_id, "db_size": db.count(DB_PATH)})


@app.route("/solutions")
def solutions():
    q = request.args.get("q", "")
    return jsonify({"solutions": db.list_solutions(DB_PATH, q, hydrate=False),
                    "db_size": db.count(DB_PATH), "synchro": passerelle.etat_base(DATA_DIR)})


@app.route("/solutions/update", methods=["POST"])
def solutions_update():
    b = request.json or {}
    ok = db.update_solution(DB_PATH, b.get("id"),
                            **{k: b[k] for k in db.EDITABLE if k in b})
    return jsonify({"ok": ok})


@app.route("/solutions/delete", methods=["POST"])
def solutions_delete():
    db.delete_solution(DB_PATH, (request.json or {}).get("id"))
    return jsonify({"ok": True, "db_size": db.count(DB_PATH)})


@app.route("/pick-folder", methods=["POST"])
def pick_folder():
    """Ouvre la boîte de dialogue native 'Choisir un dossier' SUR CE POSTE.
    Fonctionne en local (serveur = poste de l'utilisateur). En mode réseau, la
    fenêtre s'ouvre sur le poste serveur : dans ce cas, tape le chemin à la main."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return jsonify({"error": "Sélecteur indisponible sur ce poste. "
                                 "Tape le chemin à la main."})
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        path = filedialog.askdirectory(title="Choisir le dossier à importer")
        root.destroy()
        return jsonify({"path": path or ""})
    except Exception:
        return jsonify({"error": "Impossible d'ouvrir le sélecteur ici "
                                 "(mode réseau ?). Tape le chemin à la main."})


@app.route("/scan-info", methods=["POST"])
def scan_info():
    b = request.json or {}
    path = (b.get("path") or "").strip().strip('"').strip("'")
    ts = get_last_scan(path) if path else None
    when = None
    if ts:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    return jsonify({"last_scan": when})


@app.route("/import", methods=["POST"])
def import_route():
    b = request.json or {}
    path = (b.get("path") or "").strip().strip('"').strip("'")
    if not path:
        return jsonify({"error": "Indique un dossier"}), 400
    if not os.path.isdir(path):
        return jsonify({"error": f"Dossier introuvable : {path}"}), 400
    since = get_last_scan(path) if b.get("incremental") else None
    try:
        res = importer.scan_folder(
            path, status=b.get("status", "a_confirmer"),
            type_override=(b.get("type") or None),
            dry_run=bool(b.get("dry_run", True)),
            db_path=DB_PATH, since_ts=since,
        )
        res["incremental"] = bool(b.get("incremental"))
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/import/stream")
def import_stream():
    path = (request.args.get("path") or "").strip().strip('"').strip("'")
    status = request.args.get("status", "a_confirmer")
    dry = request.args.get("dry_run", "true") == "true"
    type_override = request.args.get("type") or None
    incremental = request.args.get("incremental", "false") == "true"
    since = get_last_scan(path) if incremental else None

    def sse(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    if not os.path.isdir(path):
        return Response(sse({"event": "error", "error": f"Dossier introuvable : {path}"}),
                        mimetype="text/event-stream")

    def gen():
        started = time.time()
        try:
            for ev in importer.iter_import(path, status=status,
                                           type_override=type_override,
                                           dry_run=dry, db_path=DB_PATH,
                                           since_ts=since):
                yield sse(ev)
            if not dry:
                set_last_scan(path, started)
        except Exception as e:
            yield sse({"event": "error", "error": str(e)})

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


_IMPORT_COMPLET = None


def _import_complet():
    global _IMPORT_COMPLET
    if _IMPORT_COMPLET is None or _IMPORT_COMPLET.db_path != DB_PATH:
        _IMPORT_COMPLET = import_complet.ImportComplet(DB_PATH, os.path.join(DATA_DIR, "import_complet.json"))
    return _IMPORT_COMPLET


@app.route("/import/complet")
def import_complet_etat():
    return jsonify(_import_complet().public())


@app.route("/import/complet", methods=["POST"])
def import_complet_demarrer():
    """Import de tout un dossier en tâche de fond, avec reprise (bibliothèque de dizaines de milliers de fichiers)."""
    b = request.json or {}
    path = (b.get("path") or "").strip().strip('"').strip("'")
    try:
        _import_complet().demarrer(path, status=b.get("status") or "a_confirmer", reprendre=b.get("reprendre", True) is not False)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/import/complet/arreter", methods=["POST"])
def import_complet_arreter():
    _import_complet().arreter()
    return jsonify({"ok": True})


@app.route("/batch/stream")
def batch_stream():
    path = (request.args.get("path") or "").strip().strip('"').strip("'")
    min_score = float(request.args.get("min_score", "0.6"))
    apply_clean = request.args.get("apply_clean", "true") == "true"

    def sse(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    if not os.path.isdir(path):
        return Response(sse({"event": "error", "error": f"Dossier introuvable : {path}"}),
                        mimetype="text/event-stream")

    def gen():
        try:
            for ev in batch.iter_batch(path, db_path=DB_PATH, min_score=min_score,
                                       apply_clean=apply_clean):
                yield sse(ev)
        except Exception as e:
            yield sse({"event": "error", "error": str(e)})

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/solutions/backfill_originals", methods=["POST"])
def solutions_backfill():
    return jsonify(db.backfill_originals(DB_PATH))


@app.route("/solutions/backfill_metadata", methods=["POST"])
def solutions_backfill_metadata():
    db.backup_db(DB_PATH)
    force = bool((request.json or {}).get("force"))
    return jsonify(db.backfill_metadata(DB_PATH, force=force))


@app.route("/files/prefix")
def files_prefix():
    return jsonify({"old_prefix": db.common_file_prefix(DB_PATH),
                    "count": db.count(DB_PATH)})


@app.route("/files/remap", methods=["POST"])
def files_remap():
    b = request.json or {}
    return jsonify(db.remap_roots(
        DB_PATH, new_root=b.get("root") or "", apply=bool(b.get("apply"))))


@app.route("/files/liberer", methods=["POST"])
def files_liberer():
    """Supprime les copies de data/files/ dont l'original est retrouvé dans le dossier CARTOS (aperçu par défaut)."""
    b = request.json or {}
    return jsonify(atelier.liberer_espace(DB_PATH, racine=(b.get("root") or "").strip(), apply=bool(b.get("apply"))))


@app.route("/backup", methods=["POST"])
def backup_now():
    path = db.backup_db(DB_PATH)
    return jsonify({"ok": bool(path), "path": path, "count": db.count(DB_PATH)})


@app.route("/zones/label", methods=["POST"])
def zones_label():
    body = request.json or {}
    return jsonify(ai.label_zones(body, api_key=resolve_api_key()))


@app.route("/settings", methods=["GET"])
def settings_get():
    cfg = load_config()
    ui_key = (cfg.get("api_key") or "").strip()
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    source = "ui" if ui_key else ("env" if env_key else "none")
    key = ui_key or env_key
    masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("•" * len(key))
    return jsonify({"has_key": bool(key), "source": source, "masked": masked,
                    "has_password": bool(cfg.get("access_password_hash")),
                    "copier_fichiers": bool(cfg.get("copier_fichiers", False))})


@app.route("/settings", methods=["POST"])
def settings_set():
    b = request.json or {}
    cfg = load_config()
    if "api_key" in b:
        k = (b["api_key"] or "").strip()
        if k:
            cfg["api_key"] = k
        else:
            cfg.pop("api_key", None)
    if "access_password" in b:
        pw = (b["access_password"] or "").strip()
        if pw:
            cfg["access_password_hash"] = generate_password_hash(pw)
            session["authed"] = True
        else:
            cfg.pop("access_password_hash", None)
    if "copier_fichiers" in b:
        cfg["copier_fichiers"] = bool(b["copier_fichiers"])
    save_config(cfg)
    return jsonify({"ok": True})


PORTAL_CONFIG_PATH = os.path.join(DATA_DIR, "portal_config.json")
PORTAL_DEFAULTS = {
    "shop_name": "E85-FRANCE",
    "intro": "Déposez votre fichier d'origine : nous vérifions instantanément "
             "si une solution est disponible pour votre calculateur.",
    "show_prices": False, "currency": "€", "default_price": None,
    "prices": {}, "delays": {}, "default_delay": "",
    "contact": "", "ask_contact": True,
}


def load_portal_config():
    cfg = dict(PORTAL_DEFAULTS)
    try:
        with open(PORTAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except Exception:
        pass
    return cfg


@app.route("/portal-config", methods=["GET"])
def portal_config_get():
    cfg = load_portal_config()
    lines = [f"{k} = {v}" for k, v in (cfg.get("prices") or {}).items()]
    cfg["prices_text"] = "\n".join(lines)
    dlines = [f"{k} = {v}" for k, v in (cfg.get("delays") or {}).items()]
    cfg["delays_text"] = "\n".join(dlines)
    cfg["has_password"] = bool(cfg.get("access_password_hash"))
    cfg.pop("access_password_hash", None)
    cfg.pop("smtp", None)     # géré par /clients/smtp, jamais renvoyé avec son mot de passe
    cfg.pop("stripe", None)   # géré par /fs/reglages, clés jamais renvoyées
    return jsonify(cfg)


@app.route("/portal-config", methods=["POST"])
def portal_config_set():
    b = request.json or {}
    cfg = load_portal_config()
    for key in ("shop_name", "intro", "currency", "contact", "default_delay"):
        if key in b:
            cfg[key] = (b.get(key) or "").strip()
    for key in ("show_prices", "ask_contact"):
        if key in b:
            cfg[key] = bool(b.get(key))
    if "default_price" in b:
        dp = str(b.get("default_price") or "").strip()
        cfg["default_price"] = _num(dp) if dp else None
    if "access_password" in b:
        pw = (b["access_password"] or "").strip()
        if pw:
            cfg["access_password_hash"] = generate_password_hash(pw)
        else:
            cfg.pop("access_password_hash", None)
    if "prices_text" in b:
        prices = {}
        for line in (b.get("prices_text") or "").splitlines():
            if "=" in line:
                name, val = line.split("=", 1)
                name, val = name.strip(), val.strip()
                if name and val:
                    prices[name] = _num(val)
        cfg["prices"] = prices
    if "delays_text" in b:
        delays = {}
        for line in (b.get("delays_text") or "").splitlines():
            if "=" in line:
                name, val = line.split("=", 1)
                name, val = name.strip(), val.strip()
                if name and val:
                    delays[name] = val
        cfg["delays"] = delays
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PORTAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Clients du fileservice (comptes créés sur l'espace client du portail)
# ---------------------------------------------------------------------------
FS_DB = os.environ.get("CARTO_FS_DB") or os.path.join(DATA_DIR, "fileservice.db")


def _save_portal_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PORTAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _mail_client(to, sujet, texte):
    cfg = load_portal_config()
    return mailer.envoyer(cfg.get("smtp") or {}, to, sujet, texte,
                          nom_expediteur=cfg.get("shop_name") or "E85-FRANCE", journal_dir=DATA_DIR)


@app.route("/clients")
def clients_list():
    _fs_init()
    packs = [{"credits": p["credits"] + p["bonus"], "ht": p["prix_eur"],
              "label": f"Pack {p['credits']}" + (f" + {p['bonus']} offerts" if p["bonus"] else "") + f" · {p['prix_eur']} € HT",
              "designation": factures.designation_pack(p)} for p in catalogue.PACKS_CREDITS]
    clients = comptes.lister_clients(FS_DB)
    for c in clients:
        c["utilisateurs"] = [{"nom": u["nom"], "email": u["email"], "actif": u["actif"]}
                             for u in comptes.lister_utilisateurs(FS_DB, c["id"])]
    return jsonify({"clients": clients, "packs": packs,
                    "niveaux": catalogue.remises(load_portal_config())})


@app.route("/clients/statut", methods=["POST"])
def clients_statut():
    b = request.json or {}
    cid = int(b.get("id") or 0)
    avant = comptes.get_client(FS_DB, cid)
    if not avant:
        return jsonify({"error": "Client introuvable."}), 404
    try:
        comptes.changer_statut(FS_DB, cid, b.get("statut"))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    mail = None
    if b.get("statut") == "actif" and avant["statut"] == "en_attente":
        cfg = load_portal_config()
        base = (cfg.get("public_url") or "").rstrip("/")
        lg = avant.get("langue")
        acces = (traductions.mail("acces_lien", lg, lien=f"{base}/connexion")[1] if base
                 else traductions.mail("acces_sans_lien", lg)[1])
        ok, err = _mail_client(avant["email"], *traductions.mail(
            "compte_ouvert", lg, atelier=cfg.get("shop_name") or "E85-FRANCE", societe=avant["societe"], acces=acces))
        mail = "E-mail d'activation envoyé." if ok else err
        if not base:
            mail += " (Adresse publique du portail non renseignée : l'e-mail ne contient pas de lien.)"
    return jsonify({"ok": True, "mail": mail})


@app.route("/clients/creer", methods=["POST"])
def clients_creer():
    """Compte créé par l'atelier : actif tout de suite, invitation par e-mail (ou mot de passe fixé ici)."""
    b = request.json or {}
    _fs_init()
    champs = {k: str(b.get(k) or "").strip() for k in ("societe", "siret", "tva", "contact", "email", "tel",
                                                       "adresse", "code_postal", "ville", "pays")}
    mdp = str(b.get("mdp") or "")
    inviter = not mdp
    if str(b.get("credits") or "0").strip() not in ("", "0") and getattr(g, "tech", None) and g.tech["role"] != "admin":
        return jsonify({"error": "Crédits d'ouverture réservés à un administrateur."}), 403
    try:
        cid = comptes.creer_client(FS_DB, mdp=mdp or secrets.token_urlsafe(24), **champs)
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    comptes.changer_statut(FS_DB, cid, "actif")
    niveaux = catalogue.remises(load_portal_config())
    comptes.changer_niveau(FS_DB, cid, b.get("niveau") if b.get("niveau") in niveaux else "Standard")
    langue = traductions.normaliser(b.get("langue") or "fr")
    comptes.changer_langue(FS_DB, cid, langue)
    credits = 0
    try:
        credits = int(str(b.get("credits") or "0").replace(" ", ""))
    except ValueError:
        pass
    if credits > 0:
        comptes.mouvement(FS_DB, cid, credits, (b.get("libelle_credits") or "Crédits d'ouverture").strip()[:200])
    out = {"ok": True, "id": cid}
    if inviter:
        cfg = load_portal_config()
        base = (cfg.get("public_url") or "").rstrip("/")
        jeton = comptes.creer_jeton(FS_DB, cid, duree=comptes.JETON_INVITATION)
        lien = f"{base}/reinitialiser/{jeton}" if base else f"/reinitialiser/{jeton}"
        ok, err = _mail_client(champs["email"], *traductions.mail(
            "invitation", langue, atelier=cfg.get("shop_name") or "E85-FRANCE", societe=champs["societe"],
            lien=lien, email=champs["email"].lower()))
        out["mail"] = "Invitation envoyée par e-mail (lien valable 7 jours)." if ok else err
        if not base or not ok:
            # sans adresse publique ou sans SMTP : l'atelier transmet le lien lui-même
            out["lien"] = lien
            out["mail"] += (" Adresse publique du portail non renseignée : complète le lien avec l'adresse du portail."
                            if not base else "")
    else:
        out["mail"] = "Compte créé avec le mot de passe choisi : communique-le au client."
    return jsonify(out)


@app.route("/clients/inviter", methods=["POST"])
def clients_inviter():
    """Renvoie une invitation (nouveau lien 7 jours) à un client existant."""
    b = request.json or {}
    c = comptes.get_client(FS_DB, int(b.get("id") or 0))
    if not c or c["email"].endswith("@invalid"):
        return jsonify({"error": "Client introuvable."}), 404
    cfg = load_portal_config()
    base = (cfg.get("public_url") or "").rstrip("/")
    jeton = comptes.creer_jeton(FS_DB, c["id"], duree=comptes.JETON_INVITATION)
    lien = f"{base}/reinitialiser/{jeton}" if base else f"/reinitialiser/{jeton}"
    ok, err = _mail_client(c["email"], *traductions.mail(
        "invitation", c.get("langue"), atelier=cfg.get("shop_name") or "E85-FRANCE", societe=c["societe"],
        lien=lien, email=c["email"]))
    return jsonify({"ok": True, "mail": "Invitation renvoyée." if ok else err, "lien": None if ok and base else lien})


@app.route("/clients/supprimer", methods=["POST"])
def clients_supprimer():
    """Droit à l'effacement : anonymise le compte et supprime ses fichiers (factures conservées)."""
    b = request.json or {}
    if b.get("confirmation") != "SUPPRIMER":
        return jsonify({"error": "Confirmation manquante."}), 400
    _fs_init()
    try:
        n = demandes.anonymiser_client(FS_DB, FS_FILES, int(b.get("id") or 0))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "demandes": n})


@app.route("/clients/niveau", methods=["POST"])
def clients_niveau():
    b = request.json or {}
    comptes.changer_niveau(FS_DB, int(b.get("id") or 0), b.get("niveau"))
    return jsonify({"ok": True})


@app.route("/clients/credits", methods=["POST"])
def clients_credits():
    b = request.json or {}
    try:
        montant = int(str(b.get("montant") or "0").replace(" ", ""))
    except ValueError:
        return jsonify({"error": "Montant invalide (nombre entier de crédits)."}), 400
    if not montant:
        return jsonify({"error": "Montant nul."}), 400
    try:
        solde = comptes.mouvement(FS_DB, int(b.get("id") or 0), montant,
                                  b.get("libelle") or "Ajustement par l'atelier")
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "credits": solde})


@app.route("/clients/facture", methods=["POST"])
def clients_facture():
    """Paiement reçu hors ligne : crédite le compte ET émet la facture correspondante."""
    b = request.json or {}
    _fs_init()
    c = comptes.get_client(FS_DB, int(b.get("id") or 0))
    if not c:
        return jsonify({"error": "Client introuvable."}), 404
    try:
        credits = int(str(b.get("credits") or "0").replace(" ", ""))
        ht = float(str(b.get("ht") or "0").replace(",", ".").replace(" ", ""))
        fac = factures.enregistrer_manuel(FS_DB, c, credits=credits, ht=ht, designation=b.get("designation"),
                                          paiement=b.get("paiement"), reference=b.get("reference"),
                                          vendeur=load_portal_config().get("societe") or {})
    except ValueError:
        return jsonify({"error": "Crédits (entier) et montant HT (nombre) attendus."}), 400
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "numero": fac["numero"], "nouvelle": fac["nouvelle"]})


@app.route("/clients/<int:cid>/factures")
def clients_factures(cid):
    _fs_init()
    return jsonify({"factures": factures.lister(FS_DB, cid)})


@app.route("/fs/factures/<numero>")
def fs_facture(numero):
    """Facture vue par l'atelier (même gabarit que le client)."""
    _fs_init()
    fac = factures.get(FS_DB, numero)
    if not fac:
        return "Facture introuvable", 404
    return render_template("fs/facture.html", fac=fac, shop={"name": load_portal_config().get("shop_name") or "E85-FRANCE"})


CHAMPS_SOCIETE = pages_legales.CHAMPS


@app.route("/fs/synthese")
def fs_synthese():
    _fs_init()
    return jsonify(factures.synthese(FS_DB))


@app.route("/fs/factures.csv")
def fs_factures_csv():
    _fs_init()
    debut, fin = request.args.get("debut", ""), request.args.get("fin", "")
    nom = "factures" + (f"_{debut}" if debut else "") + (f"_{fin}" if fin and fin != debut else "") + ".csv"
    return Response(factures.export_csv(FS_DB, debut, fin), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@app.route("/fs/sauvegarde", methods=["GET", "POST"])
def fs_sauvegarde():
    _fs_init()
    if request.method == "POST":
        try:
            comptes.sauvegarder(FS_DB)
        except Exception as e:
            return jsonify({"error": f"Sauvegarde impossible : {e}"}), 500
    return jsonify({"sauvegardes": comptes.dernieres_sauvegardes(FS_DB),
                    "dossier_fichiers": FS_FILES})


@app.route("/fs/statistiques")
def fs_statistiques():
    _fs_init()
    try:
        mois = int(request.args.get("mois") or 12)
    except ValueError:
        mois = 12
    return jsonify(statistiques.calculer(FS_DB, mois))


@app.route("/fs/modeles")
def fs_modeles():
    _fs_init()
    return jsonify({"modeles": modeles.lister(FS_DB), "atelier": load_portal_config().get("shop_name") or "E85-FRANCE"})


@app.route("/fs/modeles", methods=["POST"])
def fs_modeles_enregistrer():
    _fs_init()
    b = request.json or {}
    try:
        mid = modeles.enregistrer(FS_DB, b.get("titre"), b.get("texte"), bool(b.get("attente")),
                                  int(b.get("id") or 0) or None)
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": mid})


@app.route("/fs/modeles/supprimer", methods=["POST"])
def fs_modeles_supprimer():
    modeles.supprimer(FS_DB, int((request.json or {}).get("id") or 0))
    return jsonify({"ok": True})


# --- Passerelle PC atelier : côté serveur (outil en ligne) -------------------------

@app.before_request
def _tarifs_atelier():
    catalogue.appliquer_tarifs(load_portal_config())


@app.route("/fs/tarifs")
def fs_tarifs():
    return jsonify({"lignes": catalogue.tableau_tarifs(load_portal_config()), "prix_credit": catalogue.PRIX_CREDIT_EUR})


@app.route("/fs/tarifs", methods=["POST"])
def fs_tarifs_set():
    """Prix des prestations existantes et retrait de l'offre ; les prestations elles-mêmes ne se créent pas ici."""
    try:
        t = catalogue.normaliser_tarifs((request.json or {}).get("tarifs"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    cfg = load_portal_config()
    cfg["tarifs"] = t
    _save_portal_config(cfg)
    catalogue.appliquer_tarifs(cfg)
    return jsonify({"ok": True, "lignes": catalogue.tableau_tarifs(cfg)})


def _fichiers_distants():
    """Outil en ligne relié à un PC atelier : les fichiers des fiches sont sur le PC, pas sur ce serveur."""
    try:
        _fs_init()
        return bool(passerelle.lister_cles(FS_DB))
    except Exception:
        return False


@app.route("/fs/passerelle")
def fs_passerelle_liste():
    _fs_init()
    limite = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - passerelle.PC_EN_LIGNE))
    cles = [dict(c, connecte=bool(c["derniere_utilisation"] and c["derniere_utilisation"] >= limite))
            for c in passerelle.lister_cles(FS_DB)]
    return jsonify({"cles": cles, "base": passerelle.etat_base(DATA_DIR)})


@app.route("/fs/passerelle/creer", methods=["POST"])
def fs_passerelle_creer():
    _fs_init()
    try:
        cle = passerelle.creer_cle(FS_DB, (request.json or {}).get("nom"))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "cle": cle, "url": request.host_url.rstrip("/")})


@app.route("/fs/passerelle/revoquer", methods=["POST"])
def fs_passerelle_revoquer():
    passerelle.revoquer_cle(FS_DB, int((request.json or {}).get("id") or 0))
    return jsonify({"ok": True})


@app.route("/passerelle/v1/etat")
def passerelle_etat():
    cfg = load_portal_config()
    return jsonify({"ok": True, "version": APP_VERSION, "atelier": cfg.get("shop_name") or "E85-FRANCE",
                    "poste": g.passerelle["nom"], "a_traiter": demandes.alertes(FS_DB)["a_traiter"],
                    "base_empreinte": passerelle.etat_base(DATA_DIR).get("empreinte")})


@app.route("/passerelle/v1/base", methods=["POST"])
def passerelle_base():
    """Le PC envoie sa base de solutions (gzip), sans aucun fichier : elle remplace celle de l'outil en ligne."""
    tmp = os.path.join(DATA_DIR, f"solutions-passerelle-{secrets.token_hex(6)}.db")
    try:
        empreinte = passerelle.recevoir_base(request.stream, tmp)
        if empreinte != request.headers.get("X-Empreinte", empreinte):
            return jsonify({"error": "Base altérée pendant l'envoi, nouvel essai au prochain passage."}), 400
        out = db.import_db_file(DB_PATH, tmp)
        if not out.get("ok"):
            return jsonify(out), 400
    except (comptes.ErreurCompte, OSError, zlib.error) as e:
        return jsonify({"error": f"Base refusée : {e}"}), 400
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    passerelle.noter_base(DATA_DIR, empreinte, out["count"], g.passerelle["nom"])
    equipe.noter(FS_DB, f"PC · {g.passerelle['nom']}", "Bibliothèque synchronisée (PC atelier)", "", f"{out['count']} fiches")
    return jsonify({"ok": True, "fiches": out["count"]})


@app.route("/passerelle/v1/fichiers")
def passerelle_fichiers():
    passerelle.purger_fichiers(FS_DB, DATA_DIR)
    return jsonify({"fichiers": passerelle.fichiers_en_attente(FS_DB)})


@app.route("/passerelle/v1/fichiers/<int:rid>", methods=["POST"])
def passerelle_fichier_deposer(rid):
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Fichier manquant."}), 400
    try:
        d = passerelle.deposer_fichier(FS_DB, DATA_DIR, rid, f.filename, f.read(), g.passerelle["nom"])
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    equipe.noter(FS_DB, f"PC · {g.passerelle['nom']}", "Fichier envoyé à la demande (PC atelier)", d["libelle"],
                 f"pour {d['demandeur']}" if d["demandeur"] else "")
    return jsonify({"ok": True})


@app.route("/passerelle/v1/fichiers/<int:rid>/echec", methods=["POST"])
def passerelle_fichier_echec(rid):
    passerelle.refuser_fichier(FS_DB, rid, str((request.json or {}).get("message") or ""), g.passerelle["nom"])
    return jsonify({"ok": True})


# --- Fichiers demandés au PC depuis l'outil en ligne (techniciens) ------------------

def _fichier_pc(rid):
    d = passerelle.fichier_demande(FS_DB, rid)
    if not d:
        return None, (jsonify({"error": "Demande de fichier introuvable."}), 404)
    t = getattr(g, "tech", None)
    if t and d["demandeur_id"] and d["demandeur_id"] != t["id"] and t["role"] != "admin":
        return None, (jsonify({"error": "Ce fichier a été demandé par un autre technicien."}), 403)
    return d, None


@app.route("/fs/passerelle/fichiers", methods=["POST"])
def fs_passerelle_fichier_demander():
    _fs_init()
    b = request.json or {}
    sol = db.get_solution(DB_PATH, b.get("id"))
    if not sol:
        return jsonify({"error": "Fiche introuvable."}), 404
    quoi = b.get("quoi") or "solution"
    if not (sol.get("solution_file") if quoi == "solution" else sol.get("original_file")):
        return jsonify({"error": "Cette fiche n'a pas de fichier enregistré."}), 400
    passerelle.purger_fichiers(FS_DB, DATA_DIR)
    if not passerelle.pc_connecte(FS_DB):
        return jsonify({"error": "Le PC atelier n'est pas connecté : Carto Matcher doit être ouvert sur le PC "
                                 "(onglet En ligne → connexion configurée)."}), 409
    t = getattr(g, "tech", None)
    libelle = f"fiche {sol['id']} · {sol.get('vehicle_label') or 'sans libellé'}"
    try:
        rid = passerelle.demander_fichier(FS_DB, sol["id"], quoi, libelle, t["id"] if t else None,
                                          (t["nom"] if t else _auteur_nom()) or "")
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    equipe.noter(FS_DB, (t["nom"] if t else ""), "Fichier demandé au PC atelier", libelle, quoi)
    return jsonify({"ok": True, "id": rid})


@app.route("/fs/passerelle/fichiers/<int:rid>")
def fs_passerelle_fichier_etat(rid):
    d, err = _fichier_pc(rid)
    if err:
        return err
    if d["statut"] == "attente" and time.time() - d["cree_le"] > 60 and not passerelle.pc_connecte(FS_DB):
        return jsonify({"statut": "erreur", "message": "Le PC atelier ne répond plus."})
    return jsonify({"statut": d["statut"], "message": d["message"], "nom": d["nom"], "taille": d["taille"]})


@app.route("/fs/passerelle/fichiers/<int:rid>/telecharger")
def fs_passerelle_fichier_telecharger(rid):
    d, err = _fichier_pc(rid)
    if err:
        return err
    r = passerelle.retirer_fichier(FS_DB, DATA_DIR, rid)
    if not r:
        return jsonify({"error": "Fichier plus disponible (déjà téléchargé ou délai de 10 minutes dépassé) : redemande-le."}), 410
    nom, contenu = r
    resp = send_file(io.BytesIO(contenu), as_attachment=True, download_name=nom, mimetype="application/octet-stream")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/passerelle/v1/demandes")
def passerelle_demandes():
    rows = demandes.lister(FS_DB, limite=300)
    if not request.args.get("tous"):
        rows = [d for d in rows if d["statut"] in demandes.OUVERTS]
    return jsonify({"demandes": [passerelle.demande_publique(d, len(demandes.livrables(FS_DB, d["id"]))) for d in rows]})


@app.route("/passerelle/v1/demandes/<int:did>")
def passerelle_demande(did):
    d, err = _fs_demande(did)
    if err:
        return err
    liv = demandes.livrables(FS_DB, did)
    return jsonify(passerelle.demande_publique(d, len(liv)) | {
        "messages": [{k: m[k] for k in ("auteur", "auteur_nom", "texte", "cree_le")} for m in demandes.messages(FS_DB, did)]})


@app.route("/passerelle/v1/demandes/<int:did>/original")
def passerelle_original(did):
    d, err = _fs_demande(did)
    return err or _fs_send(did, "original_" + d["fichier_nom"], d["fichier_nom"])


@app.route("/passerelle/v1/demandes/<int:did>/livrer", methods=["POST"])
def passerelle_livrer(did):
    return fs_livrer(did)


@app.route("/passerelle/v1/demandes/<int:did>/statut", methods=["POST"])
def passerelle_statut(did):
    return fs_statut(did)


# --- Passerelle PC atelier : côté PC (Carto Matcher local) --------------------------

def _enligne_reglages():
    return dict(load_config().get("passerelle") or {})


def _enligne_client():
    r = _enligne_reglages()
    if not r.get("url") or not r.get("cle"):
        raise passerelle.ErreurPasserelle("Passerelle non configurée : indique l'adresse de l'outil en ligne et la clé.")
    return passerelle.Client(r["url"], r["cle"])


def _enligne(fn):
    try:
        return fn()
    except passerelle.ErreurPasserelle as e:
        return jsonify({"error": str(e)}), 400


@app.route("/enligne/reglages")
def enligne_reglages_get():
    r = _enligne_reglages()
    return jsonify({"url": r.get("url", ""), "cle_set": bool(r.get("cle")), "auto": bool(r.get("auto")),
                    "base": bool(r.get("base", True)), "fichiers": bool(r.get("fichiers", True)),
                    "automate": AUTOMATE.derniere if AUTOMATE else None, "automate_actif": bool(AUTOMATE),
                    "etat_base": AUTOMATE.base if AUTOMATE else None, "etat_fichiers": AUTOMATE.fichiers if AUTOMATE else None})


@app.route("/enligne/reglages", methods=["POST"])
def enligne_reglages_set():
    b = request.json or {}
    cfg = load_config()
    r = dict(cfg.get("passerelle") or {})
    url = str(b.get("url") or "").strip().rstrip("/")
    cle = str(b.get("cle") or "").strip()
    if cle == "-":
        r.pop("cle", None)
    elif cle:
        r["cle"] = cle
    r["url"], r["auto"] = url, bool(b.get("auto"))
    for k in ("base", "fichiers"):
        if k in b:
            r[k] = bool(b[k])
    if url or r.get("cle"):
        try:
            passerelle.Client(url, r.get("cle") or passerelle.PREFIXE + "x")
        except passerelle.ErreurPasserelle as e:
            return jsonify({"error": str(e)}), 400
    cfg["passerelle"] = r
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/enligne/test", methods=["POST"])
def enligne_test():
    return _enligne(lambda: jsonify(_enligne_client().etat()))


@app.route("/enligne/synchroniser", methods=["POST"])
def enligne_synchroniser():
    """Envoie tout de suite la liste des solutions (base .db, sans les fichiers) à l'outil en ligne."""
    def faire():
        a = AUTOMATE or _nouvel_automate()
        n = a.synchroniser_base(_enligne_client(), forcer=True)
        a.base = {"date": passerelle._now(), "message": f"{n} fiche(s) envoyée(s)" if n is not None else "à jour"}
        return jsonify({"ok": True, "fiches": n, "a_jour": n is None})
    return _enligne(faire)


@app.route("/enligne/demandes")
def enligne_demandes():
    return _enligne(lambda: jsonify({"demandes": _enligne_client().demandes(tous=bool(request.args.get("tous")))}))


def _enligne_preparation(did):
    c = _enligne_client()
    d = c.demande(did)
    return c, d, livraison_auto.preparer(d, c.original(did), DB_PATH)


@app.route("/enligne/demandes/<int:did>/preparer", methods=["POST"])
def enligne_preparer(did):
    def faire():
        _, _, prep = _enligne_preparation(did)
        prep.pop("patched", None)
        return jsonify(prep)
    return _enligne(faire)


@app.route("/enligne/demandes/<int:did>/livrer-auto", methods=["POST"])
def enligne_livrer_auto(did):
    def faire():
        c, d, prep = _enligne_preparation(did)
        if not prep["ok"]:
            return jsonify({"error": prep["raison"]}), 400
        cr = prep["compte_rendu"]
        r = c.livrer(did, livraison_auto.nom_fichier(d), prep["patched"],
                     note=f"{' + '.join(cr['types'])} · checksum {cr['checksum'] or 'OK'}", auteur=_auteur_nom())
        livraison_auto.journaliser(DB_PATH, d, prep, _auteur_nom() or "PC atelier")
        return jsonify({"ok": True, "compte_rendu": cr, "version": r.get("version"), "mail": r.get("mail")})
    return _enligne(faire)


@app.route("/enligne/demandes/<int:did>/original")
def enligne_original(did):
    def faire():
        c = _enligne_client()
        d = c.demande(did)
        return send_file(io.BytesIO(c.original(did)), as_attachment=True, download_name=f"{d['numero']}_{d['fichier_nom']}",
                         mimetype="application/octet-stream")
    return _enligne(faire)


@app.route("/enligne/demandes/<int:did>/livrer", methods=["POST"])
def enligne_livrer(did):
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Choisis le fichier modifié à livrer."}), 400
    return _enligne(lambda: jsonify(_enligne_client().livrer(did, f.filename, f.read(), request.form.get("note", ""),
                                                             _auteur_nom())))


@app.route("/enligne/demandes/<int:did>/traitement", methods=["POST"])
def enligne_traitement(did):
    return _enligne(lambda: jsonify(_enligne_client().en_traitement(did)))


AUTOMATE = None


def _chemin_fichier_fiche(db_path, sol_id, quoi):
    sol = db.get_solution(db_path, sol_id)
    return sol and (sol.get("solution_file") if quoi == "solution" else sol.get("original_file"))


def _nouvel_automate():
    return passerelle.Automate(_enligne_reglages, DB_PATH, DATA_DIR, livraison_auto.preparer,
                               livraison_auto.nom_fichier, livraison_auto.journaliser, chemin_fichier=_chemin_fichier_fiche)


def demarrer_automate():
    """PC de l'atelier : fichiers demandés en ligne, envoi de la liste des solutions et livraison automatique."""
    global AUTOMATE
    AUTOMATE = _nouvel_automate()
    threading.Thread(target=AUTOMATE.boucle, name="passerelle-auto", daemon=True).start()


# --- Mise à jour du logiciel (administrateurs) -----------------------------------

def _maj_confirmer():
    """Installer du code est l'action la plus sensible : mot de passe du compte redemandé."""
    if not getattr(g, "tech", None):
        return None   # outil sans comptes (poste local) : l'accès à l'outil suffit
    mdp = request.form.get("mdp") if request.files else (request.get_json(silent=True) or {}).get("mdp")
    if not equipe.verifier_mdp(FS_DB, g.tech["id"], mdp):
        return jsonify({"error": "Mot de passe incorrect : installation annulée."}), 403
    return None


def _maj_reponse(fn):
    try:
        res = fn()
    except mise_a_jour.ErreurMaj as e:
        mise_a_jour._journal(DATA_DIR, f"Échec : {e}")
        return jsonify({"error": str(e)}), 400
    res["redemarrage"] = ("Redémarrage automatique en cours (hébergeur)…" if PROD else
                          "Ferme puis relance « Lancer Carto Matcher » et « Lancer le portail client » pour utiliser la nouvelle version.")
    return jsonify({"ok": True} | res)


@app.route("/maj")
def maj_etat():
    cfg = load_portal_config()
    return jsonify({"locale": mise_a_jour.version_locale(), "en_memoire": APP_VERSION, "git": mise_a_jour.mode_git(),
                    "reglages": mise_a_jour.reglages(cfg), "etat": mise_a_jour.etat(DATA_DIR)})


@app.route("/maj/verifier", methods=["POST"])
def maj_verifier():
    try:
        v = mise_a_jour.verifier(load_portal_config(), DATA_DIR)
    except mise_a_jour.ErreurMaj as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(v)


@app.route("/maj/installer", methods=["POST"])
def maj_installer():
    refus = _maj_confirmer()
    if refus:
        return refus
    cfg = load_portal_config()

    def faire():
        v = mise_a_jour.verifier(cfg, DATA_DIR)
        if not v["nouvelle"]:
            raise mise_a_jour.ErreurMaj(f"Déjà à jour (version {v['locale']}).")
        if mise_a_jour.mode_git():
            return mise_a_jour.installer_git(v["release"]["tag"], DATA_DIR)
        return mise_a_jour.installer_zip(mise_a_jour.telecharger(cfg, v["release"]), DATA_DIR)
    return _maj_reponse(faire)


@app.route("/maj/zip", methods=["POST"])
def maj_zip():
    refus = _maj_confirmer()
    if refus:
        return refus
    f = request.files.get("zip")
    if not f:
        return jsonify({"error": "Choisis le fichier .zip de la release."}), 400
    contenu = f.read()
    return _maj_reponse(lambda: mise_a_jour.installer_zip(contenu, DATA_DIR))


@app.route("/maj/revenir", methods=["POST"])
def maj_revenir():
    refus = _maj_confirmer()
    if refus:
        return refus
    return _maj_reponse(lambda: mise_a_jour.revenir(DATA_DIR))


@app.route("/maj/reglages", methods=["POST"])
def maj_reglages():
    b = request.json or {}
    cfg = load_portal_config()
    r = mise_a_jour.reglages(cfg, masquer=False)
    depot = str(b.get("depot") or r["depot"]).strip()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", depot):
        return jsonify({"error": "Dépôt GitHub invalide (format propriétaire/nom)."}), 400
    jeton = str(b.get("jeton") or "").strip()
    cfg["mise_a_jour"] = {"depot": depot, "auto": bool(b.get("auto")),
                          "jeton": "" if jeton == "-" else (jeton[:200] or r["jeton"])}
    _save_portal_config(cfg)
    return jsonify({"ok": True})


@app.route("/fs/sante")
def fs_sante():
    """État du service : problèmes à corriger, e-mails en échec, dernières sauvegardes."""
    _fs_init()
    cfg = load_portal_config()
    s = sante.verifier(FS_DB, DATA_DIR, cfg)
    return jsonify(s | {"mails_echecs": mailer.echecs(DATA_DIR, 20),
                        "sauvegarde_externe": sauvegarde_externe.derniere(DATA_DIR),
                        "taches": taches.derniere_execution(DATA_DIR)})


@app.route("/fs/sante/acquitter", methods=["POST"])
def fs_sante_acquitter():
    mailer.acquitter_echecs(DATA_DIR)
    return jsonify({"ok": True})


@app.route("/fs/sauvegarde-externe", methods=["POST"])
def fs_sauvegarde_externe():
    """Lance la sauvegarde externe maintenant (test des réglages)."""
    _fs_init()
    cfg = load_portal_config()
    if not sauvegarde_externe.reglages(cfg)["mode"]:
        return jsonify({"error": "Choisis d'abord un mode (FTP ou e-mail) et enregistre les réglages."}), 400
    r = sauvegarde_externe.executer(cfg, FS_DB, FS_FILES, DATA_DIR, cfg.get("shop_name") or "E85-FRANCE")
    return jsonify(r), (200 if r["ok"] else 400)


@app.route("/fs/reglages", methods=["GET"])
def fs_reglages_get():
    cfg = load_portal_config()
    stripe = cfg.get("stripe") or {}
    horaires = cfg.get("horaires") or {str(d): v for d, v in
                                       {0: [8, 19], 1: [8, 19], 2: [8, 19], 3: [8, 19], 4: [8, 19], 5: [9, 13], 6: None}.items()}
    return jsonify({"societe": {k: (cfg.get("societe") or {}).get(k, "") for k in CHAMPS_SOCIETE},
                    "horaires": horaires,
                    "stripe": {"secret_key_set": bool(stripe.get("secret_key")),
                               "mode": "test" if str(stripe.get("secret_key", "")).startswith(("sk_test", "rk_test")) else
                                       ("live" if stripe.get("secret_key") else ""),
                               "webhook_secret_set": bool(stripe.get("webhook_secret"))},
                    "public_url": cfg.get("public_url", ""),
                    "livraison_auto": bool(cfg.get("livraison_auto")),
                    "api_active": bool(cfg.get("api_active")),
                    "remises": catalogue.remises(cfg),
                    "relances": relances.reglages(cfg),
                    "sauvegarde_externe": sauvegarde_externe.reglages(cfg),
                    "express": catalogue.express(cfg),
                    "sms": sms.reglages(cfg),
                    "pages": {k: {"titre": t, "texte": pages_legales.texte(cfg, k),
                                  "a_completer": pages_legales.a_completer(cfg, k)}
                              for k, t in pages_legales.PAGES.items()}})


@app.route("/fs/reglages", methods=["POST"])
def fs_reglages_set():
    b = request.json or {}
    cfg = load_portal_config()
    if isinstance(b.get("societe"), dict):
        cfg["societe"] = {k: str(b["societe"].get(k, "")).strip()[:160] for k in CHAMPS_SOCIETE}
    if isinstance(b.get("horaires"), dict):
        h = {}
        for d in range(7):
            v = b["horaires"].get(str(d))
            if v in (None, "", [], False):
                h[str(d)] = None
                continue
            try:
                o, f = int(v[0]), int(v[1])
            except (TypeError, ValueError, IndexError):
                return jsonify({"error": "Horaires invalides."}), 400
            if not (0 <= o < f <= 24):
                return jsonify({"error": "Horaires invalides : ouverture avant fermeture, entre 0 et 24 h."}), 400
            h[str(d)] = [o, f]
        cfg["horaires"] = h
    if isinstance(b.get("stripe"), dict):
        stripe = dict(cfg.get("stripe") or {})
        for key in ("secret_key", "webhook_secret"):
            val = str(b["stripe"].get(key) or "").strip()
            if val == "-":
                stripe.pop(key, None)          # « - » efface la valeur enregistrée
            elif val:
                stripe[key] = val              # vide = on garde l'actuelle
        if stripe.get("secret_key") and not stripe_api.configure(stripe):
            return jsonify({"error": "Clé secrète Stripe invalide (elle commence par sk_live_ ou sk_test_)."}), 400
        cfg["stripe"] = stripe
    if "livraison_auto" in b:
        cfg["livraison_auto"] = bool(b.get("livraison_auto"))
    if "api_active" in b:
        cfg["api_active"] = bool(b.get("api_active"))
    if isinstance(b.get("relances"), dict):
        rr = b["relances"]
        try:
            cfg["relances"] = {"solde_bas": bool(rr.get("solde_bas")), "non_telecharge": bool(rr.get("non_telecharge")),
                               "seuil": max(0, int(rr.get("seuil", 50))), "delai_h": max(1, int(rr.get("delai_h", 48)))}
        except (TypeError, ValueError):
            return jsonify({"error": "Relances : seuil et délai doivent être des nombres entiers."}), 400
    if isinstance(b.get("sauvegarde_externe"), dict):
        se, ancien = b["sauvegarde_externe"], sauvegarde_externe.reglages(cfg, masquer=False)
        if se.get("mode") not in ("", "ftp", "email"):
            return jsonify({"error": "Mode de sauvegarde externe inconnu."}), 400
        ftp = dict(ancien["ftp"])
        for k in ("host", "user", "dossier"):
            if k in (se.get("ftp") or {}):
                ftp[k] = str(se["ftp"][k] or "").strip()[:200]
        try:
            ftp["port"] = int((se.get("ftp") or {}).get("port") or ftp.get("port") or 21)
            garder = max(1, min(365, int(se.get("garder") or 14)))
        except (TypeError, ValueError):
            return jsonify({"error": "Sauvegarde externe : port et nombre d'archives gardées doivent être des nombres."}), 400
        ftp["tls"] = bool((se.get("ftp") or {}).get("tls", True))
        mdp = str((se.get("ftp") or {}).get("password") or "").strip()
        if mdp == "-":
            ftp.pop("password", None)
        elif mdp:
            ftp["password"] = mdp
        email = str(se.get("email") or "").strip()
        if se.get("mode") == "email" and not comptes.email_valide(email):
            return jsonify({"error": "Sauvegarde externe : adresse e-mail de réception invalide."}), 400
        if se.get("mode") == "ftp" and not ftp.get("host"):
            return jsonify({"error": "Sauvegarde externe : indique le serveur FTP."}), 400
        cfg["sauvegarde_externe"] = {"mode": se.get("mode"), "ftp": ftp, "email": email,
                                     "fichiers": bool(se.get("fichiers")), "garder": garder}
    if isinstance(b.get("express"), dict):
        try:
            cr = int(str(b["express"].get("credits") or "0").strip())
        except ValueError:
            return jsonify({"error": "Option express : supplément en crédits (nombre entier)."}), 400
        if not 1 <= cr <= 1000:
            return jsonify({"error": "Option express : supplément entre 1 et 1000 crédits."}), 400
        cfg["express"] = {"actif": bool(b["express"].get("actif")), "credits": cr}
    if isinstance(b.get("sms"), dict):
        bs, ancien = b["sms"], sms.reglages(cfg, masquer=False)
        fournisseur = bs.get("fournisseur") or ancien["fournisseur"]
        if fournisseur not in sms.FOURNISSEURS:
            return jsonify({"error": "Fournisseur SMS inconnu."}), 400
        nouveau = {"fournisseur": fournisseur, "actif": bool(bs.get("actif")),
                   "compte": str(bs.get("compte") or "").strip()[:64], "expediteur": str(bs.get("expediteur") or "").strip()[:20],
                   "cle": ancien["cle"]}
        cle = str(bs.get("cle") or "").strip()
        if cle == "-":
            nouveau["cle"] = ""
        elif cle:
            nouveau["cle"] = cle[:200]
        if nouveau["actif"]:
            err = sms.verifier_expediteur(fournisseur, nouveau["expediteur"])
            if err:
                return jsonify({"error": err}), 400
            if not nouveau["cle"] or (fournisseur == "twilio" and not nouveau["compte"]):
                return jsonify({"error": "SMS : clé API (et Account SID pour Twilio) obligatoires pour activer."}), 400
        cfg["sms"] = nouveau
    if isinstance(b.get("remises"), dict):
        rem = {}
        for k, v in b["remises"].items():
            k = str(k).strip()[:30]
            if not k:
                continue
            try:
                v = float(str(v).replace(",", ".") or 0)
            except ValueError:
                return jsonify({"error": f"Remise invalide pour {k}."}), 400
            if not 0 <= v <= 90:
                return jsonify({"error": "Une remise doit être entre 0 et 90 %."}), 400
            rem[k] = v
        if "Standard" not in rem:
            rem["Standard"] = 0.0
        cfg["remises"] = rem
    if isinstance(b.get("pages"), dict):
        pages = dict(cfg.get("pages") or {})
        for k in pages_legales.PAGES:
            if k not in b["pages"]:
                continue
            txt = str(b["pages"][k] or "").strip()[:60000]
            # vide ou identique au modèle : on revient au modèle (qui suivra ses mises à jour)
            if not txt or txt == pages_legales.MODELES[k].strip():
                pages.pop(k, None)
            else:
                pages[k] = txt
        cfg["pages"] = pages
    _save_portal_config(cfg)
    return jsonify({"ok": True})


@app.route("/fs/sms/test", methods=["POST"])
def fs_sms_test():
    try:
        numero = comptes.normaliser_mobile((request.json or {}).get("numero", ""))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    cfg = load_portal_config()
    ok, err = sms.envoyer(cfg, numero, f"{cfg.get('shop_name') or 'E85-FRANCE'} : test d'envoi SMS du fileservice.")
    return jsonify({"ok": True, "message": "SMS envoyé."}) if ok else (jsonify({"error": err}), 400)


@app.route("/clients/smtp", methods=["GET"])
def clients_smtp_get():
    cfg = load_portal_config()
    smtp = dict(cfg.get("smtp") or {})
    smtp["password_set"] = bool(smtp.pop("password", ""))
    smtp["public_url"] = cfg.get("public_url", "")
    return jsonify(smtp)


@app.route("/clients/smtp", methods=["POST"])
def clients_smtp_set():
    b = request.json or {}
    cfg = load_portal_config()
    smtp = dict(cfg.get("smtp") or {})
    for key in ("host", "user", "from", "atelier"):
        if key in b:
            smtp[key] = (b.get(key) or "").strip()
    if "port" in b:
        try:
            smtp["port"] = int(b.get("port") or 465)
        except ValueError:
            return jsonify({"error": "Port invalide."}), 400
    if (b.get("password") or "").strip():          # vide = on garde l'actuel
        smtp["password"] = b["password"].strip()
    cfg["smtp"] = smtp
    if "public_url" in b:
        cfg["public_url"] = (b.get("public_url") or "").strip().rstrip("/")
    _save_portal_config(cfg)
    return jsonify({"ok": True})


@app.route("/clients/smtp/test", methods=["POST"])
def clients_smtp_test():
    to = ((request.json or {}).get("to") or "").strip()
    if not to:
        return jsonify({"error": "Indique une adresse de test."}), 400
    ok, err = _mail_client(to, "Test d'envoi — fileservice", "Si vous lisez ce message, l'envoi d'e-mails fonctionne.")
    return jsonify({"ok": ok, "error": err})


# ---------------------------------------------------------------------------
# Demandes du fileservice (fichiers envoyés depuis l'espace client)
# ---------------------------------------------------------------------------
FS_FILES = os.environ.get("CARTO_FS_FILES") or os.path.join(DATA_DIR, "fileservice_fichiers")


def _fs_init():
    demandes.init_db(FS_DB)
    factures.init_db(FS_DB)
    equipe.init_db(FS_DB)
    modeles.init_db(FS_DB)
    push.init_db(FS_DB)
    passerelle.init_db(FS_DB)


def _fs_demande(did):
    d = demandes.get(FS_DB, did)
    if not d:
        return None, (jsonify({"error": "Demande introuvable."}), 404)
    return d, None


def _fs_lien(numero):
    base = (load_portal_config().get("public_url") or "").rstrip("/")
    return f"\n{base}/fichiers/{numero}" if base else ""


def _fs_prevenir(d, cle, **champs):
    """E-mail au client (et à l'utilisateur qui a envoyé la demande), SMS s'il les a demandés ;
    renvoie un message à afficher à l'atelier."""
    return _fs_vues.prevenir(load_portal_config(), d, cle, _fs_lien(d["numero"]).strip(), journal_dir=DATA_DIR,
                             db_path=FS_DB, config_path=PORTAL_CONFIG_PATH, **champs)


@app.route("/fs/demandes")
def fs_demandes():
    _fs_init()
    statut = request.args.get("statut") or None
    rows = demandes.lister(FS_DB, statut=statut if statut in demandes.STATUTS else None)
    return jsonify({"demandes": rows, "stats": demandes.stats(FS_DB)})


@app.route("/fs/alertes")
def fs_alertes():
    _fs_init()
    return jsonify(demandes.alertes(FS_DB) | {"mails_echecs": len(mailer.echecs(DATA_DIR))})


@app.route("/fs/demandes/<int:did>")
def fs_demande(did):
    d, err = _fs_demande(did)
    if err:
        return err
    return jsonify({"demande": d, "livrables": demandes.livrables(FS_DB, did), "annexes": demandes.annexes(FS_DB, did),
                    "appareils_push": push.nombre(FS_DB, d["client_id"]),
                    "messages": demandes.messages(FS_DB, did, marquer_lus_pour="atelier")})


def _fs_send(did, fichier, nom):
    p = demandes.chemin(FS_FILES, did, fichier)
    if not p:
        return jsonify({"error": "Fichier introuvable."}), 404
    return send_file(p, as_attachment=True, download_name=nom, mimetype="application/octet-stream")


@app.route("/fs/demandes/<int:did>/original")
def fs_original(did):
    d, err = _fs_demande(did)
    return err or _fs_send(did, "original_" + d["fichier_nom"], f"{d['numero']}_{d['fichier_nom']}")


@app.route("/fs/demandes/<int:did>/livre/<int:version>")
def fs_livre(did, version):
    liv = next((l for l in demandes.livrables(FS_DB, did) if l["version"] == version), None)
    return _fs_send(did, liv["fichier"], liv["nom"]) if liv else (jsonify({"error": "Version introuvable."}), 404)


@app.route("/fs/demandes/<int:did>/annexe/<int:aid>")
def fs_annexe(did, aid):
    a = next((a for a in demandes.annexes(FS_DB, did) if a["id"] == aid and a["fichier"]), None)
    d = demandes.get(FS_DB, did)
    return (_fs_send(did, a["fichier"], f"{d['numero']}_{a['nom']}") if a and d
            else (jsonify({"error": "Fichier introuvable."}), 404))


@app.route("/fs/demandes/<int:did>/pj/<int:mid>")
def fs_pj(did, mid):
    m = next((m for m in demandes.messages(FS_DB, did) if m["id"] == mid and m["pj_fichier"]), None)
    return _fs_send(did, m["pj_fichier"], m["pj_nom"]) if m else (jsonify({"error": "Pièce jointe introuvable."}), 404)


@app.route("/fs/demandes/<int:did>/recapitulatif")
def fs_recapitulatif(did):
    d, err = _fs_demande(did)
    if err:
        return err
    cfg = load_portal_config()
    return render_template("fs/recapitulatif.html", f=d, livrables=demandes.livrables(FS_DB, did),
                           annexes=demandes.annexes(FS_DB, did),
                           vendeur=cfg.get("societe") or {}, prix_credit=catalogue.PRIX_CREDIT_EUR,
                           shop={"name": cfg.get("shop_name") or "E85-FRANCE"}, t=lambda x: x, langue="fr")


@app.route("/fs/demandes/<int:did>/analyser", methods=["POST"])
def fs_analyser(did):
    d, err = _fs_demande(did)
    if err:
        return err
    p = demandes.chemin(FS_FILES, did, "original_" + d["fichier_nom"])
    if not p:
        return jsonify({"error": "Fichier d'origine introuvable."}), 404
    with open(p, "rb") as f:
        data = f.read()
    result = engine.match(data, DB_PATH, path=d["fichier_nom"])
    return jsonify({k: v for k, v in result.items() if k != "minhash"})


def _fs_original_bytes(d):
    p = demandes.chemin(FS_FILES, d["id"], "original_" + d["fichier_nom"])
    if not p:
        return None
    with open(p, "rb") as f:
        return f.read()


@app.route("/fs/demandes/<int:did>/preparer", methods=["POST"])
def fs_preparer(did):
    """Livraison en un clic, étape 1 : vérifie sans rien écrire que le fichier peut être préparé."""
    d, err = _fs_demande(did)
    if err:
        return err
    data = _fs_original_bytes(d)
    if data is None:
        return jsonify({"error": "Fichier d'origine introuvable."}), 404
    prep = livraison_auto.preparer(d, data, DB_PATH)
    prep.pop("patched", None)
    return jsonify(prep)


@app.route("/fs/demandes/<int:did>/livrer-auto", methods=["POST"])
def fs_livrer_auto(did):
    """Livraison en un clic, étape 2 : prépare à nouveau et livre (déterministe)."""
    d, err = _fs_demande(did)
    if err:
        return err
    if d["statut"] == "refuse":
        return jsonify({"error": "Demande refusée."}), 400
    data = _fs_original_bytes(d)
    if data is None:
        return jsonify({"error": "Fichier d'origine introuvable."}), 404
    prep = livraison_auto.preparer(d, data, DB_PATH)
    if not prep["ok"]:
        return jsonify({"error": prep["raison"]}), 400
    cr = prep["compte_rendu"]
    auteur = _auteur_nom()
    version = demandes.livrer(FS_DB, FS_FILES, did, livraison_auto.nom_fichier(d), prep["patched"],
                              f"{' + '.join(cr['types'])} · checksum {cr['checksum'] or 'OK'}")
    livraison_auto.journaliser(DB_PATH, d, prep, auteur)
    vtxt = f" (version {version})" if version > 1 else ""
    return jsonify({"ok": True, "version": version, "compte_rendu": cr,
                    "mail": _fs_prevenir(d, "fichier_pret", version=vtxt)})


@app.route("/fs/demandes/<int:did>/statut", methods=["POST"])
def fs_statut(did):
    d, err = _fs_demande(did)
    if err:
        return err
    try:
        demandes.changer_statut(FS_DB, did, (request.json or {}).get("statut"))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.route("/fs/demandes/<int:did>/message", methods=["POST"])
def fs_message(did):
    d, err = _fs_demande(did)
    if err:
        return err
    pj = request.files.get("pj")
    texte = request.form.get("texte", "")
    try:
        demandes.ajouter_message(FS_DB, FS_FILES, did, "atelier", _auteur_nom(),
                                 texte, pj_nom=pj.filename if pj else "", pj_contenu=(pj.read() or None) if pj else None)
        if request.form.get("attente"):
            demandes.changer_statut(FS_DB, did, "attente")
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    info = _fs_prevenir(d, "info_requise" if request.form.get("attente") else "message", texte=texte)
    return jsonify({"ok": True, "mail": info})


@app.route("/fs/demandes/<int:did>/livrer", methods=["POST"])
def fs_livrer(did):
    d, err = _fs_demande(did)
    if err:
        return err
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Choisis le fichier modifié à livrer."}), 400
    try:
        version = demandes.livrer(FS_DB, FS_FILES, did, f.filename, f.read(), request.form.get("note", ""))
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    vtxt = f" (version {version})" if version > 1 else ""
    return jsonify({"ok": True, "version": version, "mail": _fs_prevenir(d, "fichier_pret", version=vtxt)})


@app.route("/fs/demandes/<int:did>/refuser", methods=["POST"])
def fs_refuser(did):
    d, err = _fs_demande(did)
    if err:
        return err
    motif = (request.json or {}).get("motif", "")
    try:
        montant = demandes.refuser(FS_DB, did, motif)
    except comptes.ErreurCompte as e:
        return jsonify({"error": str(e)}), 400
    info = _fs_prevenir(d, "refus", motif=motif.strip(), credits=montant)
    return jsonify({"ok": True, "mail": info})


def _num(s):
    """'150' -> 150, '149.9' -> 149.9, sinon renvoie la chaîne telle quelle."""
    try:
        f = float(str(s).replace(",", "."))
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return s


INBOX_DIR = os.path.join(DATA_DIR, "portal_inbox")
INBOX_STATE_PATH = os.path.join(INBOX_DIR, "etat.json")


def _load_inbox_state():
    try:
        with open(INBOX_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_inbox_state(st):
    os.makedirs(INBOX_DIR, exist_ok=True)
    with open(INBOX_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def _inbox_file_path(name):
    """Chemin sûr d'un fichier de l'inbox (refuse tout chemin hors du dossier)."""
    name = os.path.basename(name or "")
    if not name or name in ("demandes.log", "etat.json"):
        return None
    path = os.path.join(INBOX_DIR, name)
    return path if os.path.isfile(path) else None


@app.route("/history")
def history_stock():
    sha = request.args.get("sha") or ""
    ecu = request.args.get("ecu") or ""
    rows = db.list_history(DB_PATH, sha=sha, ecu=ecu)
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"),
            "when": r.get("created_at"),
            "label": r.get("solution_label") or "",
            "verdict": r.get("verdict") or "",
            "note": r.get("note") or "",
            "dossier_id": r.get("dossier_id"),
            "client_name": r.get("client_name") or "",
        })
    return jsonify({"jobs": out})


@app.route("/inbox")
def inbox_list():
    state = _load_inbox_state()
    entries = {}
    log_path = os.path.join(INBOX_DIR, "demandes.log")
    if os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                name = rec.get("fichier")
                if name:
                    entries[name] = rec
    if os.path.isdir(INBOX_DIR):
        for name in os.listdir(INBOX_DIR):
            if name in ("demandes.log", "etat.json"):
                continue
            full = os.path.join(INBOX_DIR, name)
            if not os.path.isfile(full):
                continue
            rec = entries.get(name) or {"fichier": name, "date": None, "verdict": None,
                                         "plateforme": None, "fabricant": None, "contact": {}}
            rec["present"] = True
            rec["taille"] = os.path.getsize(full)
            entries[name] = rec
    out = []
    for name, rec in entries.items():
        st = state.get(name) or {}
        if st.get("supprime"):
            continue
        rec.setdefault("present", False)
        rec["traite"] = bool(st.get("traite"))
        out.append(rec)
    out.sort(key=lambda r: r.get("fichier") or "", reverse=True)
    n_new = sum(1 for r in out if not r["traite"])
    return jsonify({"demandes": out, "non_traitees": n_new})


@app.route("/inbox/analyze", methods=["POST"])
def inbox_analyze():
    b = request.json or {}
    path = _inbox_file_path(b.get("fichier"))
    if not path:
        return jsonify({"error": "Fichier introuvable dans la boîte de réception."}), 404
    with open(path, "rb") as f:
        data = f.read()
    result = engine.match(data, DB_PATH, path=os.path.basename(path))
    payload = {k: v for k, v in result.items() if k != "minhash"}
    return jsonify(payload)


@app.route("/inbox/mark", methods=["POST"])
def inbox_mark():
    b = request.json or {}
    name = os.path.basename(b.get("fichier") or "")
    if not name:
        return jsonify({"error": "Fichier manquant."}), 400
    st = _load_inbox_state()
    entry = st.get(name) or {}
    entry["traite"] = bool(b.get("traite"))
    st[name] = entry
    _save_inbox_state(st)
    return jsonify({"ok": True})


@app.route("/inbox/pack", methods=["POST"])
def inbox_pack():
    """Match + combo des prestas commandées + pack, puis marque traité."""
    b = request.json or request.form or {}
    name = os.path.basename(b.get("fichier") or "")
    path = _inbox_file_path(name)
    if not path:
        return jsonify({"error": "Fichier introuvable dans la boîte."}), 404
    rec = _inbox_record(name) or {}
    want = rec.get("prestas") or []
    with open(path, "rb") as fh:
        data = fh.read()
    result = engine.match(data, DB_PATH, path=name)
    matches = result.get("matches") or []
    ids = []
    for m in matches:
        atoms = m.get("type_atoms") or []
        hit = (not want) or any(
            w in atoms or w in (m.get("solution_type") or "") for w in want)
        if (m.get("exact") or m.get("same_stock") or m.get("calibration_exact")) and hit:
            if m["id"] not in ids:
                ids.append(m["id"])
    if not ids:
        return jsonify({"error": "Aucune fiche même stock pour cette commande.",
                        "prestas": want}), 400
    # Réutilise le moteur pack via un faux form
    class _F:
        filename = name
        def read(self_inner):
            return data
    # on construit le pack ici (même code que patch_pack, ids connus)
    bundles, errors = pmod.load_many(DB_PATH, ids)
    if not bundles:
        return jsonify({"error": "Fiches introuvables", "load_errors": errors}), 404
    if len(bundles) == 1:
        bb = bundles[0]
        res = pmod.build_patched(
            data, bb["orig"], bb["sol"],
            fiche_type=(bb["fiche"] or {}).get("solution_type") or "",
            platform=(bb["fiche"] or {}).get("ecu_platform") or "",
        )
        label = (bb["fiche"] or {}).get("solution_type") or ""
        sid = (bb["fiche"] or {}).get("id")
    else:
        res = pmod.build_combined(data, bundles)
        if res.get("error") == "stocks_melanges":
            return jsonify(res), 400
        label = res.get("combined_type") or " + ".join(
            (x["fiche"].get("solution_type") or "") for x in bundles)
        sid = bundles[0]["fiche"].get("id")
    patched = res.get("patched")
    if not patched:
        return jsonify({"error": res.get("error") or "Pack impossible."}), 400
    contact = rec.get("contact") or {}
    dossier_id = None
    # dossier existant même immat, sinon on en crée un depuis la demande
    plate = contact.get("immat") or contact.get("plate") or ""
    if plate:
        hits = dos.suggest(DB_PATH, plate=plate)
        if hits:
            dossier_id = hits[0]["id"]
    if not dossier_id:
        dossier_id = dos.from_inbox(DB_PATH, rec, dump_bytes=data)
    doss = dos.get(DB_PATH, dossier_id) or {}
    basename = packmod.pack_basename(
        doss.get("plate") or plate, doss.get("vin") or contact.get("vin") or "",
        label, name)
    meta = packmod.meta_from_result(
        res, client_name=name, plate=doss.get("plate") or plate,
        vin=doss.get("vin") or "", vehicle=doss.get("vehicle_label") or "",
        dossier_id=dossier_id, label=label, ids=ids)
    dest = packmod.pack_dir(DB_PATH, dossier_id)
    paths = packmod.write_files(dest, basename, patched, meta)
    buf, zip_name = packmod.zip_pack(paths, basename)
    db.add_job(
        DB_PATH, client_name=name, solution_id=sid, solution_label=label,
        verdict=res.get("verdict") or "",
        zones=res.get("applied") or res.get("applied_bytes") or 0,
        note=f"pack inbox {os.path.basename(dest)}",
        dossier_id=dossier_id,
    )
    st = _load_inbox_state()
    st[name] = {**(st.get(name) or {}), "traite": True}
    _save_inbox_state(st)
    resp = send_file(buf, as_attachment=True, download_name=zip_name,
                     mimetype="application/zip")
    resp.headers["X-Pack-Dir"] = dest
    resp.headers["X-Pack-Name"] = zip_name
    resp.headers["X-Dossier-Id"] = str(dossier_id)
    resp.headers["X-Inbox-File"] = name
    resp.headers["Access-Control-Expose-Headers"] = (
        "X-Pack-Dir, X-Pack-Name, X-Dossier-Id, X-Inbox-File, Content-Disposition")
    return resp


@app.route("/inbox/file")
def inbox_file():
    path = _inbox_file_path(request.args.get("fichier"))
    if not path:
        return jsonify({"error": "Fichier introuvable."}), 404
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route("/inbox/delete", methods=["POST"])
def inbox_delete():
    b = request.json or {}
    name = os.path.basename(b.get("fichier") or "")
    if not name:
        return jsonify({"error": "Fichier manquant."}), 400
    path = _inbox_file_path(name)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    st = _load_inbox_state()
    entry = st.get(name) or {}
    entry["supprime"] = True
    st[name] = entry
    _save_inbox_state(st)
    return jsonify({"ok": True})


@app.route("/settings/test", methods=["POST"])
def settings_test():
    return jsonify(ai.ping(api_key=resolve_api_key()))


@app.route("/export.csv")
def export_csv():
    import csv, io
    sols = db.list_solutions(DB_PATH)
    cols = ["id", "vehicle_label", "ecu_version", "ecu_platform", "manufacturer",
            "solution_type", "tested_status", "tags", "stock_size",
            "original_file", "solution_file", "notes"]
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for s in sols:
        w.writerow([s.get(c, "") for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=carto_matcher_base.csv"})


@app.route("/duplicates")
def duplicates():
    return jsonify({"groups": db.find_duplicates(DB_PATH)})


@app.route("/solutions/bulk", methods=["POST"])
def solutions_bulk():
    b = request.json or {}
    n = db.bulk_update(DB_PATH, ids=b.get("ids", []),
                       set_status=b.get("set_status"), add_tags=b.get("add_tags"))
    return jsonify({"ok": True, "updated": n})


@app.route("/backups")
def backups_list():
    return jsonify({"backups": db.list_backups(DB_PATH)})


@app.route("/backups/restore", methods=["POST"])
def backups_restore():
    return jsonify(db.restore_backup(DB_PATH, (request.json or {}).get("name", "")))


@app.route("/backups/import", methods=["POST"])
def backups_import():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Aucun fichier reçu."}), 400
    name = os.path.basename(f.filename)
    if not name.lower().endswith(".db"):
        return jsonify({"ok": False, "error": "Fichier .db attendu (sauvegarde Carto Matcher)."}), 400
    bdir = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stored = os.path.join(bdir, f"solutions-import-{stamp}.db")
    f.save(stored)
    out = db.import_db_file(DB_PATH, stored)
    if not out.get("ok"):
        try:
            os.remove(stored)
        except OSError:
            pass
        return jsonify(out), 400
    return jsonify(out)


@app.route("/jobs", methods=["GET"])
def jobs_list():
    return jsonify({"jobs": db.list_jobs(DB_PATH)})


@app.route("/jobs", methods=["POST"])
def jobs_add():
    b = request.json or {}
    jid = db.add_job(DB_PATH, client_name=b.get("client_name", ""),
                     solution_id=b.get("solution_id"), solution_label=b.get("solution_label", ""),
                     verdict=b.get("verdict", ""), zones=b.get("zones", 0),
                     applied=b.get("applied", 1), note=b.get("note", ""),
                     dossier_id=b.get("dossier_id"))
    return jsonify({"ok": True, "id": jid})


@app.route("/jobs/update", methods=["POST"])
def jobs_update():
    b = request.json or {}
    db.update_job(DB_PATH, b.get("id"), note=b.get("note"))
    return jsonify({"ok": True})


@app.route("/jobs/delete", methods=["POST"])
def jobs_delete():
    db.delete_job(DB_PATH, (request.json or {}).get("id"))
    return jsonify({"ok": True})


def _inbox_record(name):
    name = os.path.basename(name or "")
    if not name:
        return None
    log_path = os.path.join(INBOX_DIR, "demandes.log")
    rec = {"fichier": name, "contact": {}}
    if os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("fichier") == name:
                    rec = r
    rec.setdefault("contact", {})
    rec.setdefault("fichier", name)
    return rec


def _dossier_fields(src):
    src = src or {}
    out = {}
    for k in ("client_name", "phone", "email", "company", "plate", "vin",
              "vehicle_label", "ecu_version", "ecu_platform", "manufacturer",
              "notes", "source", "inbox_file", "status"):
        if k in src:
            out[k] = src.get(k)
    if "prestas" in src:
        out["prestas"] = src.get("prestas")
    if "solution_id" in src:
        try:
            out["solution_id"] = int(src["solution_id"]) if src["solution_id"] else None
        except (TypeError, ValueError):
            out["solution_id"] = None
    return out


@app.route("/dossiers")
def dossiers_list():
    q = request.args.get("q") or ""
    status = request.args.get("status") or ""
    rows = dos.list_dossiers(DB_PATH, q=q, status=status)
    return jsonify({
        "dossiers": rows,
        "counts": dos.counts(DB_PATH),
        "unattached_jobs": dos.unattached_jobs(DB_PATH),
        "presta_options": metadata_atoms(),
    })


def metadata_atoms():
    from matcher.metadata import TYPE_ATOMS_ORDER
    return [a for a in TYPE_ATOMS_ORDER if a != "Origine (stock)"]


@app.route("/dossiers/suggest")
def dossiers_suggest():
    return jsonify({"hits": dos.suggest(
        DB_PATH,
        plate=request.args.get("plate") or "",
        vin=request.args.get("vin") or "",
        name=request.args.get("name") or "",
    )})


@app.route("/dossiers/create", methods=["POST"])
def dossiers_create():
    b = request.json or {}
    fields = _dossier_fields(b)
    last = _get_last()
    if b.get("from_last") and last and last.get("result"):
        inc = last["result"].get("incoming") or {}
        nm = inc.get("name_meta") or {}
        fields.setdefault("ecu_version", inc.get("best_ecu_version") or "")
        fields.setdefault("ecu_platform", inc.get("platform") or "")
        fields.setdefault("manufacturer", inc.get("manufacturer") or "")
        if not fields.get("vehicle_label"):
            fields["vehicle_label"] = " ".join(
                x for x in (nm.get("brand"), nm.get("vehicle")) if x).strip()
        if not fields.get("prestas"):
            fields["prestas"] = nm.get("solution_type") or ""
        fields.setdefault("source", "atelier")
    did = dos.create(DB_PATH, **fields)
    if b.get("from_last") and last and last.get("data"):
        name = (last.get("result") or {}).get("incoming", {}).get("filename") or "client.bin"
        dos.save_dump(DB_PATH, did, last["data"], name)
    return jsonify({"ok": True, "id": did, "dossier": dos.get(DB_PATH, did)})


@app.route("/dossiers/from_inbox", methods=["POST"])
def dossiers_from_inbox():
    b = request.json or {}
    name = os.path.basename(b.get("fichier") or "")
    rec = _inbox_record(name)
    if not rec:
        return jsonify({"error": "Demande introuvable."}), 404
    extra = _dossier_fields(b)
    for k, v in extra.items():
        if v:
            rec[k] = v
    if extra.get("client_name"):
        rec.setdefault("contact", {})["nom"] = extra["client_name"]
    if extra.get("plate"):
        rec.setdefault("contact", {})["immat"] = extra["plate"]
    dump = None
    path = _inbox_file_path(name)
    if path:
        with open(path, "rb") as fh:
            dump = fh.read()
    did = dos.from_inbox(DB_PATH, rec, dump_bytes=dump)
    if extra:
        dos.update(DB_PATH, did, **{k: v for k, v in extra.items() if v not in (None, "")})
    return jsonify({"ok": True, "id": did, "dossier": dos.get(DB_PATH, did)})


@app.route("/dossiers/<int:did>")
def dossiers_get(did):
    d = dos.get(DB_PATH, did)
    if not d:
        return jsonify({"error": "Dossier introuvable"}), 404
    return jsonify(d)


@app.route("/dossiers/<int:did>", methods=["POST"])
def dossiers_update(did):
    if not dos.get(DB_PATH, did):
        return jsonify({"error": "Dossier introuvable"}), 404
    b = request.json or {}
    dos.update(DB_PATH, did, **_dossier_fields(b))
    if "prestas" in b:
        dos.update(DB_PATH, did, prestas=b.get("prestas"))
    return jsonify({"ok": True, "dossier": dos.get(DB_PATH, did)})


@app.route("/dossiers/<int:did>/delete", methods=["POST"])
def dossiers_delete(did):
    dos.delete(DB_PATH, did)
    return jsonify({"ok": True})


@app.route("/dossiers/<int:did>/dump", methods=["POST"])
def dossiers_dump_upload(did):
    if not dos.get(DB_PATH, did):
        return jsonify({"error": "Dossier introuvable"}), 404
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Aucun fichier"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "Fichier vide"}), 400
    path = dos.save_dump(DB_PATH, did, data, f.filename or "client.bin")
    return jsonify({"ok": True, "dump_file": path, "dossier": dos.get(DB_PATH, did)})


@app.route("/dossiers/<int:did>/dump")
def dossiers_dump_get(did):
    d = dos.get(DB_PATH, did)
    if not d or not d.get("dump_present"):
        return jsonify({"error": "Aucun dump archivé"}), 404
    return send_file(d["dump_file"], as_attachment=True,
                     download_name=os.path.basename(d["dump_file"]))


@app.route("/dossiers/<int:did>/livre", methods=["POST"])
def dossiers_livre(did):
    if not dos.get(DB_PATH, did):
        return jsonify({"error": "Dossier introuvable"}), 404
    dos.update(DB_PATH, did, status="livre")
    return jsonify({"ok": True, "dossier": dos.get(DB_PATH, did)})


@app.route("/reveal", methods=["POST"])
def reveal_folder():
    b = request.json or {}
    path = (b.get("path") or "").strip()
    if not path or not os.path.exists(path):
        return jsonify({"error": "Dossier introuvable", "path": path}), 404
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if os.name == "nt":
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        return jsonify({"ok": False, "path": folder, "error": str(e)})
    return jsonify({"ok": True, "path": folder})


@app.route("/dossiers/<int:did>/attach_job", methods=["POST"])
def dossiers_attach_job(did):
    if not dos.get(DB_PATH, did):
        return jsonify({"error": "Dossier introuvable"}), 404
    jid = (request.json or {}).get("job_id")
    dos.attach_job(DB_PATH, jid, did)
    return jsonify({"ok": True, "dossier": dos.get(DB_PATH, did)})


@app.route("/patch/analyze", methods=["POST"])
def patch_analyze():
    f = request.files.get("file")
    sol_id = request.form.get("id") or request.args.get("id")
    if not f:
        return jsonify({"error": "Aucun fichier reçu"}), 400
    if not sol_id:
        return jsonify({"error": "Aucune solution sélectionnée"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "Fichier vide"}), 400
    fiche, bins = pmod.load_fiche_bins(DB_PATH, sol_id)
    if bins == "original_missing":
        return jsonify({"error": "original_missing",
                        "detail": "Le fichier original de cette solution est introuvable."}), 404
    if bins == "solution_missing":
        return jsonify({"error": "solution_missing",
                        "detail": "Le fichier solution de cette fiche est introuvable."}), 404
    if not fiche:
        return jsonify({"error": "Solution introuvable"}), 404
    orig, soldata = bins
    report = pmod.analyze_solution(data, orig, soldata, fiche=fiche)
    report["clientName"] = f.filename
    report["solutionId"] = int(sol_id)
    return jsonify(report)


@app.route("/patch/apply", methods=["POST"])
def patch_apply():
    f = request.files.get("file")
    sol_id = request.form.get("id")
    partial = str(request.form.get("partial", "0")).lower() in ("1", "true", "on")
    if not f or not sol_id:
        return jsonify({"error": "Fichier et solution requis."}), 400
    data = f.read()
    fiche, bins = pmod.load_fiche_bins(DB_PATH, sol_id)
    if not isinstance(bins, tuple):
        return jsonify({"error": bins or "Solution introuvable"}), 404
    orig, soldata = bins
    res = pmod.build_patched(
        data, orig, soldata,
        fiche_type=(fiche or {}).get("solution_type") or "",
        platform=(fiche or {}).get("ecu_platform") or "",
        partial=partial,
    )
    patched = res.get("patched")
    if not patched:
        return jsonify({"error": res.get("error") or "Patch impossible."}), 400
    ck = res.get("checksum") or {}
    preview = str(request.form.get("preview", "0")).lower() in ("1", "true", "on")
    if not preview:
        dossier_id = request.form.get("dossier_id") or None
        if dossier_id:
            try:
                dossier_id = int(dossier_id)
            except (TypeError, ValueError):
                dossier_id = None
        db.add_job(
            DB_PATH, client_name=f.filename or "",
            solution_id=int(sol_id),
            solution_label=(fiche or {}).get("solution_type") or "",
            verdict=(res.get("verdict") or "") + " · " + (ck.get("status") or ""),
            zones=res.get("applied") or 0, applied=1,
            note=ck.get("note") or "",
            dossier_id=dossier_id,
        )
    name = f.filename or "fichier.bin"
    dot = name.rfind(".")
    base, ext = (name[:dot], name[dot:]) if dot > 0 else (name, ".bin")
    return send_file(
        io.BytesIO(patched), as_attachment=True,
        download_name=f"{base}_PATCHED{ext}",
        mimetype="application/octet-stream",
    )


def _ids_from_request():
    raw = request.form.get("ids") or request.form.get("id") or request.args.get("ids") or request.args.get("id")
    return pmod._parse_ids(raw)


@app.route("/patch/analyze_multi", methods=["POST"])
def patch_analyze_multi():
    f = request.files.get("file")
    ids = _ids_from_request()
    if not f:
        return jsonify({"error": "Aucun fichier reçu"}), 400
    if not ids:
        return jsonify({"error": "Aucune solution sélectionnée"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "Fichier vide"}), 400
    if len(ids) == 1:
        fiche, bins = pmod.load_fiche_bins(DB_PATH, ids[0])
        if not isinstance(bins, tuple):
            return jsonify({"error": bins or "Solution introuvable"}), 404
        orig, soldata = bins
        report = pmod.analyze_solution(data, orig, soldata, fiche=fiche)
        report["clientName"] = f.filename
        report["solutionId"] = ids[0]
        report["ids"] = ids
        return jsonify(report)
    bundles, errors = pmod.load_many(DB_PATH, ids)
    force_mix = str(request.form.get("force_mix", "0")).lower() in ("1", "true", "on")
    report = pmod.analyze_combined(data, bundles, force_mix=force_mix)
    report["clientName"] = f.filename
    report["solutionId"] = ids[0]
    report["ids"] = ids
    report["load_errors"] = errors
    return jsonify(report)


@app.route("/patch/apply_multi", methods=["POST"])
def patch_apply_multi():
    f = request.files.get("file")
    ids = _ids_from_request()
    partial = str(request.form.get("partial", "0")).lower() in ("1", "true", "on")
    if not f or not ids:
        return jsonify({"error": "Fichier et solutions requis."}), 400
    data = f.read()
    bundles, errors = pmod.load_many(DB_PATH, ids)
    if not bundles:
        return jsonify({"error": "Aucune fiche chargeable", "load_errors": errors}), 404
    if len(bundles) == 1:
        b = bundles[0]
        res = pmod.build_patched(
            data, b["orig"], b["sol"],
            fiche_type=(b["fiche"] or {}).get("solution_type") or "",
            platform=(b["fiche"] or {}).get("ecu_platform") or "",
            partial=partial,
        )
        label = (b["fiche"] or {}).get("solution_type") or ""
        sid = (b["fiche"] or {}).get("id")
    else:
        force_mix = str(request.form.get("force_mix", "0")).lower() in ("1", "true", "on")
        res = pmod.build_combined(data, bundles, partial=partial, force_mix=force_mix)
        label = res.get("combined_type") or " + ".join(
            (b["fiche"].get("solution_type") or "") for b in bundles)
        sid = (bundles[0]["fiche"] or {}).get("id")
    patched = res.get("patched")
    if not patched:
        return jsonify({"error": res.get("error") or "Patch impossible.",
                        "conflicts": res.get("conflicts") or [],
                        "load_errors": errors}), 400
    ck = res.get("checksum") or {}
    preview = str(request.form.get("preview", "0")).lower() in ("1", "true", "on")
    if not preview:
        dossier_id = request.form.get("dossier_id") or None
        if dossier_id:
            try:
                dossier_id = int(dossier_id)
            except (TypeError, ValueError):
                dossier_id = None
        db.add_job(
            DB_PATH, client_name=f.filename or "",
            solution_id=int(sid) if sid else None,
            solution_label=label,
            verdict=(res.get("verdict") or "") + " · " + (ck.get("status") or ""),
            zones=res.get("applied") or 0, applied=1,
            note=ck.get("note") or "",
            dossier_id=dossier_id,
        )
    name = f.filename or "fichier.bin"
    dot = name.rfind(".")
    base, ext = (name[:dot], name[dot:]) if dot > 0 else (name, ".bin")
    tag = (label.replace(" ", "").replace("/", "-") or "MULTI")[:40]
    return send_file(
        io.BytesIO(patched), as_attachment=True,
        download_name=f"{base}_{tag}{ext}",
        mimetype="application/octet-stream",
    )


def _run_patch_from_request():
    """Applique 1 ou N fiches. Renvoie (res, label, sid, ids, errors, data, filename)."""
    f = request.files.get("file")
    ids = _ids_from_request()
    partial = str(request.form.get("partial", "0")).lower() in ("1", "true", "on")
    if not f or not ids:
        return None, "Fichier et solutions requis."
    data = f.read()
    bundles, errors = pmod.load_many(DB_PATH, ids)
    if not bundles:
        return None, {"error": "Aucune fiche chargeable", "load_errors": errors}
    if len(bundles) == 1:
        b = bundles[0]
        res = pmod.build_patched(
            data, b["orig"], b["sol"],
            fiche_type=(b["fiche"] or {}).get("solution_type") or "",
            platform=(b["fiche"] or {}).get("ecu_platform") or "",
            partial=partial,
        )
        label = (b["fiche"] or {}).get("solution_type") or ""
        sid = (b["fiche"] or {}).get("id")
        if res and not res.get("fiches"):
            ev = pmod.analyze_solution(data, b["orig"], b["sol"], fiche=b["fiche"])
            res["zones"] = ev.get("zones") or res.get("zones") or []
            res["verdict_text"] = ev.get("verdict_text") or ""
            res["stats"] = ev.get("stats") or ""
            res["fiche"] = ev.get("fiche") or res.get("fiche")
    else:
        force_mix = str(request.form.get("force_mix", "0")).lower() in ("1", "true", "on")
        res = pmod.build_combined(data, bundles, partial=partial, force_mix=force_mix)
        label = res.get("combined_type") or " + ".join(
            (b["fiche"].get("solution_type") or "") for b in bundles)
        sid = (bundles[0]["fiche"] or {}).get("id")
        ana = pmod.analyze_combined(data, bundles, force_mix=force_mix)
        res["verdict_text"] = ana.get("verdict_text") or res.get("verdict_text")
        res["stats"] = ana.get("stats") or ""
        res["fiches"] = ana.get("fiches") or res.get("fiches")
        res["conflicts"] = ana.get("conflicts") or res.get("conflicts")
    return {
        "res": res,
        "label": label,
        "sid": sid,
        "ids": ids,
        "errors": errors,
        "data": data,
        "filename": f.filename or "fichier.bin",
        "partial": partial,
    }, None


@app.route("/patch/pack", methods=["POST"])
def patch_pack():
    """Génère BIN + rapport TXT/HTML, les pose dans le dossier, renvoie le ZIP."""
    payload, err = _run_patch_from_request()
    if err:
        if isinstance(err, dict):
            return jsonify(err), 400
        return jsonify({"error": err}), 400
    res = payload["res"]
    patched = res.get("patched")
    if not patched:
        return jsonify({"error": res.get("error") or "Patch impossible.",
                        "conflicts": res.get("conflicts") or [],
                        "load_errors": payload["errors"]}), 400

    dossier_id = request.form.get("dossier_id") or None
    if dossier_id:
        try:
            dossier_id = int(dossier_id)
        except (TypeError, ValueError):
            dossier_id = None
    doss = dos.get(DB_PATH, dossier_id) if dossier_id else None
    plate = (doss or {}).get("plate") or request.form.get("plate") or ""
    vin = (doss or {}).get("vin") or request.form.get("vin") or ""
    vehicle = (doss or {}).get("vehicle_label") or ""
    label = payload["label"]
    basename = packmod.pack_basename(plate, vin, label, payload["filename"])
    meta = packmod.meta_from_result(
        res, client_name=payload["filename"], plate=plate, vin=vin,
        vehicle=vehicle, dossier_id=dossier_id, label=label,
        ids=payload["ids"],
        platform=(res.get("fiche") or {}).get("platform") or res.get("platform") or "",
    )
    dest = packmod.pack_dir(DB_PATH, dossier_id)
    paths = packmod.write_files(dest, basename, patched, meta)
    buf, zip_name = packmod.zip_pack(paths, basename)
    ck = res.get("checksum") or {}
    note = f"pack {os.path.basename(dest)} · {basename}.bin"
    if not packmod.ready_flag(res):
        note += " · PAS prêt à flasher"
    db.add_job(
        DB_PATH, client_name=payload["filename"],
        solution_id=int(payload["sid"]) if payload["sid"] else None,
        solution_label=label,
        verdict=(res.get("verdict") or "") + " · " + (ck.get("status") or ""),
        zones=res.get("applied") or res.get("applied_bytes") or 0, applied=1,
        note=note,
        dossier_id=dossier_id,
    )
    resp = send_file(
        buf, as_attachment=True,
        download_name=zip_name,
        mimetype="application/zip",
    )
    resp.headers["X-Pack-Dir"] = dest
    resp.headers["X-Pack-Name"] = zip_name
    if dossier_id:
        resp.headers["X-Dossier-Id"] = str(dossier_id)
    inbox_name = request.form.get("inbox_file") or ""
    if inbox_name:
        resp.headers["X-Inbox-File"] = inbox_name
    resp.headers["Access-Control-Expose-Headers"] = "X-Pack-Dir, X-Pack-Name, X-Dossier-Id, X-Inbox-File, Content-Disposition"
    return resp


@app.route("/solutions/rebuild_fingerprints", methods=["POST"])
def rebuild_fingerprints():
    limit = int((request.json or {}).get("limit") or 12)
    return jsonify(db.rebuild_fingerprints(DB_PATH, limit=limit))


@app.route("/atelier/status")
def atelier_status():
    return jsonify(atelier.status(DB_PATH))


@app.route("/atelier/sync", methods=["POST"])
def atelier_sync():
    limit = int((request.json or {}).get("limit") or 12)
    return jsonify(atelier.sync_batch(DB_PATH, limit=limit))


@app.route("/atelier/cleanup", methods=["POST"])
def atelier_cleanup():
    apply = bool((request.json or {}).get("apply"))
    return jsonify(atelier.cleanup(DB_PATH, apply=apply))


@app.route("/inspect", methods=["POST"])
def inspect():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Aucun fichier reçu"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "Fichier vide"}), 400
    info = extract.extract(data)
    runs = extract.ascii_runs(data)
    MAX = 5000
    strings = [{"offset": r["start"], "value": r["s"], "nb": r["null_bounded"]}
               for r in runs[:MAX]]
    return jsonify({
        "filename": f.filename, "size": len(data),
        "platform": info.get("platform"), "manufacturer": info.get("manufacturer"),
        "candidates": info.get("typed_candidates", []),
        "strings": strings, "strings_total": len(runs),
    })


@app.route("/solution/file")
def solution_file():
    sol_id = request.args.get("id")
    sol = db.get_solution(DB_PATH, sol_id)
    if not sol:
        return jsonify({"error": "Solution introuvable"}), 404
    path = sol.get("solution_file") or ""
    if not path or not os.path.isfile(path):
        return jsonify({"error": "Fichier solution introuvable sur le disque"}), 404
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route("/viz/bytes")
def viz_bytes():
    sol = db.get_solution(DB_PATH, request.args.get("id"))
    if not sol:
        return jsonify({"error": "Solution introuvable"}), 404
    which = request.args.get("which", "solution")
    path = sol.get("original_file" if which == "original" else "solution_file") or ""
    if not path or not os.path.isfile(path):
        return jsonify({"error": f"Fichier {which} introuvable"}), 404
    if os.path.getsize(path) > 32 * 1024 * 1024:
        return jsonify({"error": "Fichier trop volumineux (>32 Mo)"}), 413
    with open(path, "rb") as f:
        data = f.read()
    return Response(data, mimetype="application/octet-stream")


def _mark_changed(map_list, a, b):
    n = min(len(a), len(b))
    for m in map_list:
        s, e = int(m.get("data_off") or 0), int(m.get("end") or 0)
        changed = 0
        for i in range(max(0, s), min(e, n)):
            if a[i] != b[i]:
                changed += 1
        m["changed_bytes"] = changed
        m["changed"] = changed > 0


@app.route("/maps/scan", methods=["POST"])
def maps_scan():
    bits = int(request.form.get("bits") or 16)
    be = (request.form.get("endian") or "le") == "be"
    data = b""
    other = None
    f = request.files.get("file")
    if f:
        data = f.read()
        f2 = request.files.get("file2")
        if f2:
            other = f2.read()
    else:
        sol = db.get_solution(DB_PATH, request.form.get("id"))
        if not sol:
            return jsonify({"error": "Solution introuvable"}), 404
        which = request.form.get("which") or "solution"
        path = sol.get("original_file" if which == "original" else "solution_file") or ""
        if not path or not os.path.isfile(path):
            return jsonify({"error": f"Fichier {which} introuvable"}), 404
        with open(path, "rb") as fh:
            data = fh.read()
        if which in ("diff", "solution"):
            op = sol.get("original_file") or ""
            if op and os.path.isfile(op):
                with open(op, "rb") as fh:
                    other = fh.read()
                if which == "diff":
                    data, other = other, data  # scan l'original, comparer à la solution
    if not data:
        return jsonify({"error": "Aucun fichier"}), 400
    if len(data) > 32 * 1024 * 1024:
        return jsonify({"error": "Fichier trop volumineux (>32 Mo)"}), 413
    out = mapsmod.find_maps(data, bits=bits, be=be)
    if other:
        _mark_changed(out["maps"], data, other)
        out["compared"] = True
        out["changed_maps"] = sum(1 for m in out["maps"] if m.get("changed"))
    return jsonify(out)


@app.route("/maps/guess", methods=["POST"])
def maps_guess():
    try:
        off = int(request.form.get("off") or 0)
        length = int(request.form.get("len") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "offset/longueur invalides"}), 400
    f = request.files.get("file")
    if f:
        data = f.read()
    else:
        sol = db.get_solution(DB_PATH, request.form.get("id"))
        if not sol:
            return jsonify({"error": "Solution introuvable"}), 404
        path = sol.get("original_file") or sol.get("solution_file") or ""
        if not path or not os.path.isfile(path):
            return jsonify({"error": "Fichier introuvable"}), 404
        with open(path, "rb") as fh:
            data = fh.read()
    rec = mapsmod.guess_from_zone(data, off, length)
    if not rec:
        return jsonify({"error": "Pas une table 2D reconnaissable", "off": off, "len": length}), 404
    rec.pop("x", None)
    rec.pop("y", None)
    return jsonify(rec)


@app.route("/maps/csv", methods=["POST"])
def maps_csv():
    """Export CSV d'une carte (ou de toutes) — facteur manuel, pas de Damos."""
    bits = int(request.form.get("bits") or 16)
    be = (request.form.get("endian") or "le") == "be"
    try:
        factor = float(request.form.get("factor") or 1)
    except (TypeError, ValueError):
        factor = 1.0
    try:
        zoff = float(request.form.get("offset") or 0)
    except (TypeError, ValueError):
        zoff = 0.0
    unit = (request.form.get("unit") or "").strip()
    changed_only = str(request.form.get("changed_only") or "0").lower() in ("1", "true", "on")
    data, other = b"", None
    f = request.files.get("file")
    if f:
        data = f.read()
        f2 = request.files.get("file2")
        if f2:
            other = f2.read()
    else:
        sol = db.get_solution(DB_PATH, request.form.get("id"))
        if not sol:
            return jsonify({"error": "Solution introuvable"}), 404
        which = request.form.get("which") or "solution"
        path = sol.get("original_file" if which == "original" else "solution_file") or ""
        if not path or not os.path.isfile(path):
            return jsonify({"error": f"Fichier {which} introuvable"}), 404
        with open(path, "rb") as fh:
            data = fh.read()
        op = sol.get("original_file") or ""
        if op and os.path.isfile(op) and which != "original":
            with open(op, "rb") as fh:
                other = fh.read()
    if not data:
        return jsonify({"error": "Aucun fichier"}), 400
    spec_raw = request.form.get("spec") or ""
    if spec_raw:
        try:
            spec = json.loads(spec_raw)
        except json.JSONDecodeError:
            return jsonify({"error": "spec JSON invalide"}), 400
        decoded = mapsmod.decode_map(data, spec)
        if not decoded:
            return jsonify({"error": "Carte illisible (offset / dimensions)"}), 400
        other_dec = mapsmod.decode_map(other, spec) if other else None
        csv_text = mapsmod.to_csv(
            decoded, factor=factor, offset=zoff, unit=unit,
            other=other_dec,
            title=request.form.get("title") or "Carto Matcher 2D",
        )
        name = "carte.csv"
    else:
        out = mapsmod.find_maps(data, bits=bits, be=be)
        if other:
            _mark_changed(out["maps"], data, other)
        csv_text = mapsmod.maps_to_csv(
            out["maps"], data, other=other, factor=factor, offset=zoff,
            unit=unit, changed_only=changed_only,
        )
        if not csv_text.strip():
            return jsonify({"error": "Aucune carte à exporter"}), 404
        name = "cartes.csv"
    return Response(
        csv_text, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )



def _lan_ip():
    """IP de la machine sur le réseau local (pour l'accès depuis les autres postes)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


if __name__ == "__main__":
    db.init_db(DB_PATH)
    n = db.count(DB_PATH)
    host = os.environ.get("CARTO_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    lan = host not in ("127.0.0.1", "localhost")
    print("=" * 60)
    print(f"  Carto Matcher v{APP_VERSION}")
    print(f"  Base de données : {os.path.abspath(DB_PATH)}")
    print(f"  Fiches en base  : {n}")
    backup = db.backup_db(DB_PATH)
    if backup:
        print(f"  Sauvegarde      : {backup}")
    elif n == 0:
        print("  Base vide -> pas de sauvegarde (rien à protéger).")
        print("  ⚠ Si tu attendais des fiches ici, c'est que le programme")
        print("    lit ce fichier-là. Vérifie que ta base est bien à ce chemin.")
    if lan:
        ip = _lan_ip()
        print("  Mode RÉSEAU ATELIER (LAN) — accessible depuis les autres postes")
        print(f"  Sur ce poste      : http://127.0.0.1:{port}")
        if ip:
            print(f"  Depuis les autres : http://{ip}:{port}")
        print("  Réseau local uniquement — rien n'est exposé sur internet.")
    else:
        print(f"  Ouvre http://127.0.0.1:{port}")
    print("=" * 60)
    demarrer_automate()
    app.run(host=host, port=port, debug=False, threaded=True)
