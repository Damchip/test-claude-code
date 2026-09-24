"""
Espace client du fileservice — maquette navigable.

Toutes les pages tournent sur des DONNÉES DE DÉMONSTRATION (DEMO_* ci-dessous) :
aucun compte, crédit ni fichier réel n'est créé. Elles servent à valider le
thème et les parcours avant de brancher la base (comptes, crédits, demandes).

Monté par portal.py sous /espace.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import catalogue

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

DEMO_USER = {
    "company": "Garage Martin Performance",
    "contact": "Julien Martin",
    "initials": "JM",
    "credits": 412,
    "level": "Partenaire",
}

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

DEMO_TRANSACTIONS = [
    {"date": "24/09/2026", "libelle": "Fichier F-24817 · Stage 1 + Start & Stop", "montant": -88},
    {"date": "24/09/2026", "libelle": "Fichier F-24812 · Adaptation E85", "montant": -59},
    {"date": "23/09/2026", "libelle": "Achat pack 400 + 40 crédits offerts · facture FA-2026-0412", "montant": 440},
    {"date": "23/09/2026", "libelle": "Fichier F-24806 · Pack E85 + débridage moteur", "montant": -99},
    {"date": "21/09/2026", "libelle": "Remboursement F-24752 · lecture incomplète", "montant": 59},
    {"date": "21/09/2026", "libelle": "Fichier F-24752 · Stage 1", "montant": -59},
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


@bp.app_template_filter("credits")
def _fmt_credits(n):
    return f"{n:+d}" if isinstance(n, int) else n


@bp.context_processor
def _inject():
    user = dict(DEMO_USER)
    user["open_count"] = sum(1 for f in DEMO_FILES if f["status"] in ("recu", "en_cours", "attente"))
    return {"shop": SHOP, "user": user, "service": service_status(), "demo": True,
            "statuses": STATUSES}


def _file(file_id):
    for f in DEMO_FILES:
        if f["id"] == file_id:
            return f
    abort(404)


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        return redirect(url_for("fs.dashboard"))
    return render_template("fs/login.html", mode=request.args.get("mode", "login"))


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
                           prix_credit=catalogue.PRIX_CREDIT_EUR,
                           transactions=DEMO_TRANSACTIONS)
