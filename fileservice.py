"""
Espace client du fileservice, monté par portal.py sous /espace.

Comptes RÉELS (comptes.py, data/fileservice.db) : inscription pro, validation
par l'atelier dans l'outil interne, connexion, mot de passe oublié, solde et
mouvements de crédits.

Encore en DONNÉES DE DÉMONSTRATION (DEMO_*) : les fichiers/demandes et les
factures — prochaine étape.
"""
import datetime as dt
import hmac
import secrets
from zoneinfo import ZoneInfo

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

import catalogue
import comptes
import mailer

bp = Blueprint("fs", __name__, url_prefix="/espace")

TZ = ZoneInfo("Europe/Paris")

SHOP = {"name": "E85-FRANCE"}

# Horaires d'ouverture : (jour 0 = lundi) -> (ouverture, fermeture) en heures
HOURS = {0: (8, 19), 1: (8, 19), 2: (8, 19), 3: (8, 19), 4: (8, 19), 5: (9, 13)}
DAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

STATUSES = {
    "recu": "Reçu",
    "en_cours": "En traitement",
    "attente": "Info requise",
    "pret": "Prêt",
    "refuse": "Refusé",
}

TOOLS = ["KESS3", "Autotuner", "Flex", "CMD Flash", "MagicMotorsport", "KTAG", "PCMFlash", "Autre"]

DEMO_FILES = [
    {"id": "F-24817", "cat": "vl", "vehicule": "Volkswagen Golf VII", "moteur": "2.0 TDI 150 ch", "annee": 2016,
     "ecu": "Bosch EDC17C64", "outil": "KESS3", "methode": "OBD", "prestas": ["Stage 1", "Start & Stop"],
     "credits": 88, "status": "en_cours", "date": "24/09 · 14:02", "vin": "WVWZZZAUZGW******", "km": 128400},
    {"id": "F-24812", "cat": "vl", "vehicule": "Audi A3 8V", "moteur": "1.4 TFSI 125 ch", "annee": 2015,
     "ecu": "Bosch MED17.5.25", "outil": "Autotuner", "methode": "OBD", "prestas": ["Adaptation E85"],
     "credits": 59, "status": "pret", "date": "24/09 · 10:41", "vin": "WAUZZZ8V5FA******", "km": 96500},
    {"id": "F-24806", "cat": "vl", "vehicule": "BMW 330i G20", "moteur": "2.0 258 ch", "annee": 2020,
     "ecu": "Bosch MG1CS201", "outil": "Flex", "methode": "Banc", "prestas": ["Pack E85 + débridage moteur"],
     "credits": 99, "status": "attente", "date": "23/09 · 17:25", "vin": "WBA5R11000F******", "km": 64200},
    {"id": "F-24791", "cat": "vl", "vehicule": "Peugeot 308 II", "moteur": "1.6 THP 205 ch", "annee": 2017,
     "ecu": "Bosch MED17.4.4", "outil": "KESS3", "methode": "Boot", "prestas": ["Stage 1", "Speed limit"],
     "credits": 88, "status": "pret", "date": "23/09 · 09:12", "vin": "VF3LBHZTZHS******", "km": 58300},
    {"id": "F-24770", "cat": "vl", "vehicule": "Ford Focus III", "moteur": "1.0 EcoBoost 125 ch", "annee": 2016,
     "ecu": "Continental SID212", "outil": "CMD Flash", "methode": "Banc", "prestas": ["Adaptation E85 (siège)"],
     "credits": 89, "status": "pret", "date": "22/09 · 15:48", "vin": "WF0NXXGCHNG******", "km": 88100},
    {"id": "F-24752", "cat": "vl", "vehicule": "Renault Mégane IV RS", "moteur": "1.8 TCe 280 ch", "annee": 2019,
     "ecu": "Bosch MG1CS050", "outil": "Autotuner", "methode": "Banc", "prestas": ["Stage 1"],
     "credits": 59, "status": "refuse", "date": "21/09 · 11:30", "vin": "VF1RFB000K0******", "km": 41200,
     "motif": "Lecture incomplète : relire en mode banc complet."},
    {"id": "F-24733", "cat": "moto", "vehicule": "Yamaha MT-09", "moteur": "890 cm³ 119 ch", "annee": 2021,
     "ecu": "Mitsubishi", "outil": "Flex", "methode": "Banc", "prestas": ["Stage 1 – Moto"],
     "credits": 59, "status": "pret", "date": "20/09 · 16:05", "vin": "JYARN39E0MA******", "km": 12900},
    {"id": "F-24728", "cat": "pl", "vehicule": "John Deere 6155R", "moteur": "6.8 L 155 ch", "annee": 2018,
     "ecu": "Denso", "outil": "KESS3", "methode": "OBD", "prestas": ["Stage 1 – PL", "Suppression DTC – PL"],
     "credits": 138, "status": "recu", "date": "24/09 · 14:20", "vin": "1RW6155RCJR******", "km": 5400},
]

DEMO_THREAD = [
    {"me": False, "who": "Atelier · Thomas", "initials": "TR", "time": "Hier 17:40",
     "text": "Bonjour, la lecture est bien reçue. Le véhicule roulera en E85 pur ou en mélange avec du SP98 ? "
             "On adapte l'enrichissement en conséquence."},
    {"me": True, "who": "Vous", "initials": "JM", "time": "Hier 18:02",
     "text": "Mélange, le client fait souvent le plein en SP98 sur autoroute."},
    {"me": False, "who": "Atelier · Thomas", "initials": "TR", "time": "Aujourd'hui 09:15",
     "text": "Parfait, merci. Il nous manque encore la lecture complète en mode banc pour finaliser : "
             "vous pouvez la joindre ici directement."},
]

def service_status(now=None):
    """Ouvert / fermé selon HOURS, et prochaine ouverture si fermé."""
    now = now or dt.datetime.now(TZ)
    today = HOURS.get(now.weekday())
    if today and today[0] <= now.hour < today[1]:
        return {"open": True, "avg_delay": "25 min", "next_open": ""}
    for offset in range(0, 8):
        day = now + dt.timedelta(days=offset)
        hrs = HOURS.get(day.weekday())
        if not hrs or (offset == 0 and now.hour >= hrs[0]):
            continue
        when = "aujourd'hui" if offset == 0 else ("demain" if offset == 1 else DAY_NAMES[day.weekday()].lower())
        return {"open": False, "avg_delay": "", "next_open": f"réouverture {when} à {hrs[0]} h"}
    return {"open": False, "avg_delay": "", "next_open": ""}


def hours_table(now=None):
    now = now or dt.datetime.now(TZ)
    rows = []
    for d in range(7):
        hrs = HOURS.get(d)
        rows.append({"jour": DAY_NAMES[d], "plage": f"{hrs[0]} h – {hrs[1]} h" if hrs else "Fermé",
                     "today": d == now.weekday()})
    return rows


@bp.app_template_filter("euros")
def _fmt_euros(v):
    """1234.5 -> « 1 234,50 € » (espace fine insécable pour les milliers)."""
    return f"{v:,.2f}".replace(",", "\u202f").replace(".", ",") + " €"


@bp.app_template_filter("credits")
def _fmt_credits(n):
    return f"{n:+d}" if isinstance(n, int) else n


PUBLIC_ENDPOINTS = {"fs.login", "fs.register", "fs.forgot", "fs.reset"}
LIMITEUR = comptes.Limiteur(max_echecs=5, fenetre=900)


def _db():
    return current_app.config["FS_DB"]


def _smtp():
    return mailer.config_smtp(current_app.config["FS_CONFIG"])


def _mail(a, sujet, texte):
    ok, err = mailer.envoyer(_smtp(), a, sujet, texte, nom_expediteur=SHOP["name"],
                             journal_dir=current_app.config["FS_DATA_DIR"])
    if not ok:
        current_app.logger.warning("E-mail non envoyé à %s : %s", a, err)
    return ok


def _url_publique(endpoint, **kw):
    """Lien absolu pour les e-mails. public_url (portal_config.json) prime sur l'hôte de la requête."""
    base = (current_app.config.get("FS_PUBLIC_URL")
            or mailer.lire_config(current_app.config["FS_CONFIG"]).get("public_url") or "").rstrip("/")
    if base:
        return base + url_for(endpoint, **kw)
    return url_for(endpoint, _external=True, **kw)


def _ip():
    return current_app.config["FS_CLIENT_IP"]()


def csrf_token():
    tok = session.get("csrf")
    if not tok:
        tok = session["csrf"] = secrets.token_urlsafe(24)
    return tok


@bp.before_request
def _securite():
    # Jeton anti-CSRF sur tout POST (champ de formulaire ou en-tête pour les appels JS)
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
        attendu = session.get("csrf") or ""
        if not attendu or not hmac.compare_digest(sent, attendu):
            if request.is_json:
                return jsonify({"erreur": "Session expirée, rechargez la page."}), 400
            flash("Session expirée, merci de réessayer.")
            return redirect(request.url)

    g.client = None
    cid = session.get("client_id")
    if cid:
        c = comptes.get_client(_db(), cid)
        if c and c["statut"] == "actif":
            g.client = c
        else:
            session.pop("client_id", None)
    if g.client is None and request.endpoint not in PUBLIC_ENDPOINTS:
        if request.is_json:
            return jsonify({"erreur": "Connexion requise."}), 401
        return redirect(url_for("fs.login", next=request.full_path.rstrip("?")))
    return None


def _initiales(c):
    base = c.get("contact") or c.get("societe") or "?"
    parts = [p for p in base.replace("-", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


@bp.context_processor
def _inject():
    user = None
    c = getattr(g, "client", None)
    if c:
        user = {"company": c["societe"], "contact": c["contact"] or c["societe"], "initials": _initiales(c),
                "credits": c["credits"], "level": c["niveau"],
                "open_count": sum(1 for f in DEMO_FILES if f["status"] in ("recu", "en_cours", "attente"))}
    return {"shop": SHOP, "user": user, "service": service_status(), "demo": True,
            "statuses": STATUSES, "csrf_token": csrf_token}


def _safe_next(raw):
    raw = (raw or "").strip()
    if not raw.startswith("/espace") or raw.startswith("//") or "\\" in raw or ":" in raw:
        return url_for("fs.dashboard")
    return raw


def _file(file_id):
    for f in DEMO_FILES:
        if f["id"] == file_id:
            return f
    abort(404)


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if g.client:
        return redirect(url_for("fs.dashboard"))
    erreur, email = None, ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        cles = ("ip:" + _ip(), "email:" + email)
        if LIMITEUR.bloque(*cles):
            erreur = "Trop de tentatives. Réessayez dans 15 minutes ou réinitialisez votre mot de passe."
        else:
            c = comptes.authentifier(_db(), email, request.form.get("password", ""))
            if not c:
                LIMITEUR.echec(*cles)
                erreur = "E-mail ou mot de passe incorrect."
            elif c["statut"] == "en_attente":
                erreur = "Votre compte est en attente de validation par l'atelier. Vous recevrez un e-mail dès son ouverture."
            elif c["statut"] == "bloque":
                erreur = "Ce compte est suspendu. Contactez l'atelier."
            else:
                LIMITEUR.reussite(*cles)
                session.clear()
                session["client_id"] = c["id"]
                session.permanent = bool(request.form.get("remember"))
                return redirect(_safe_next(request.args.get("next")))
    return render_template("fs/login.html", mode="login", erreur=erreur, email=email)


@bp.route("/inscription", methods=["GET", "POST"])
def register():
    if g.client:
        return redirect(url_for("fs.dashboard"))
    erreur, form = None, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in ("societe", "siret", "tva", "contact", "email", "tel")}
        if not request.form.get("cgv"):
            erreur = "Merci d'accepter les conditions générales de vente."
        elif LIMITEUR.bloque("inscription:" + _ip()):
            erreur = "Trop de demandes depuis votre connexion. Réessayez plus tard."
        else:
            try:
                comptes.creer_client(_db(), mdp=request.form.get("password", ""), **form)
            except comptes.ErreurCompte as e:
                erreur = str(e)
            else:
                LIMITEUR.echec("inscription:" + _ip())   # compte les inscriptions, pas seulement les erreurs
                _mail(form["email"], f"{SHOP['name']} — demande d'ouverture de compte reçue",
                      f"Bonjour,\n\nNous avons bien reçu la demande d'ouverture de compte pour "
                      f"{form['societe']}.\nL'atelier la vérifie sous 24 h ouvrées ; vous recevrez un "
                      f"e-mail dès que votre compte sera actif.\n\n{SHOP['name']}")
                atelier = _smtp().get("atelier")
                if atelier:
                    _mail(atelier, f"Nouvelle inscription fileservice : {form['societe']}",
                          f"Société : {form['societe']}\nSIRET : {form['siret']}\nTVA : {form['tva'] or '—'}\n"
                          f"Contact : {form['contact'] or '—'}\nE-mail : {form['email']}\nTél. : {form['tel'] or '—'}\n\n"
                          "À valider dans l'outil interne, onglet Clients.")
                return render_template("fs/login.html", mode="registered", societe=form["societe"])
    return render_template("fs/login.html", mode="register", erreur=erreur, form=form)


@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot():
    envoye = False
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not LIMITEUR.bloque("oubli:" + _ip()):
            LIMITEUR.echec("oubli:" + _ip())
            c = comptes.client_par_email(_db(), email)
            if c and c["statut"] != "bloque":
                lien = _url_publique("fs.reset", jeton=comptes.creer_jeton(_db(), c["id"]))
                _mail(c["email"], f"{SHOP['name']} — réinitialisation du mot de passe",
                      f"Bonjour,\n\nPour choisir un nouveau mot de passe, ouvrez ce lien (valable 1 heure) :\n"
                      f"{lien}\n\nSi vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail.\n\n{SHOP['name']}")
        envoye = True   # même réponse que le compte existe ou non
    return render_template("fs/login.html", mode="forgot", envoye=envoye)


@bp.route("/reinitialiser/<jeton>", methods=["GET", "POST"])
def reset(jeton):
    if not comptes.jeton_valide(_db(), jeton):
        return render_template("fs/login.html", mode="reset", invalide=True)
    erreur = None
    if request.method == "POST":
        if request.form.get("password") != request.form.get("password2"):
            erreur = "Les deux mots de passe ne correspondent pas."
        else:
            try:
                comptes.reinitialiser_mdp(_db(), jeton, request.form.get("password", ""))
            except comptes.ErreurCompte as e:
                erreur = str(e)
            else:
                flash("Mot de passe modifié. Vous pouvez vous connecter.")
                return redirect(url_for("fs.login"))
    return render_template("fs/login.html", mode="reset", erreur=erreur, jeton=jeton)


@bp.route("/deconnexion", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("fs.login"))


@bp.route("/")
def dashboard():
    counts = {k: sum(1 for f in DEMO_FILES if f["status"] == k) for k in STATUSES}
    return render_template("fs/dashboard.html", nav="dashboard", files=DEMO_FILES[:6],
                           counts=counts, hours=hours_table())


@bp.route("/nouveau", methods=["GET", "POST"])
def new_file():
    if request.method == "POST":
        flash("Maquette : le formulaire fonctionne, mais aucun fichier n'a été réellement envoyé.")
        return redirect(url_for("fs.file_detail", file_id="F-24728"))
    return render_template("fs/new.html", nav="new", categories=catalogue.CATEGORIES,
                           prestations=catalogue.PRESTATIONS, services=catalogue.SERVICES,
                           garanties=catalogue.GARANTIES, retours=catalogue.RETOURS, tools=TOOLS)


@bp.route("/tarif", methods=["POST"])
def tarif():
    """Calcul du tarif côté serveur : seule source de vérité pour les prix."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    codes = data.get("prestations") or []
    if not isinstance(codes, list):
        codes = []
    return jsonify(catalogue.devis(str(data.get("categorie", "")), [str(c) for c in codes][:20],
                                   siege=bool(data.get("siege")), garantie=str(data.get("garantie") or "")))


@bp.route("/fichiers")
def files():
    counts = {k: sum(1 for f in DEMO_FILES if f["status"] == k) for k in STATUSES}
    return render_template("fs/files.html", nav="files", files=DEMO_FILES, counts=counts)


@bp.route("/fichiers/<file_id>")
def file_detail(file_id):
    f = _file(file_id)
    order = ["recu", "en_cours", "pret"]
    if f["status"] == "attente":
        reached = 1
    elif f["status"] == "refuse":
        reached = 1
    else:
        reached = order.index(f["status"]) if f["status"] in order else 0
    if f["id"] == "F-24806":
        thread = DEMO_THREAD
    elif f["status"] == "pret":
        thread = [{"me": False, "who": "Atelier · Thomas", "initials": "TR", "time": f["date"],
                   "text": "Fichier prêt, checksum corrigé. Pensez à nous faire un retour après essai."}]
    else:
        thread = []
    return render_template("fs/file.html", nav="files", f=f, reached=reached, thread=thread)


@bp.route("/credits")
def credits():
    return render_template("fs/credits.html", nav="credits", packs=catalogue.PACKS_CREDITS,
                           prix_credit=catalogue.PRIX_CREDIT_EUR, tva=catalogue.TVA,
                           transactions=comptes.mouvements(_db(), g.client["id"]))
