"""
Espace client du fileservice — maquette navigable.

Toutes les pages tournent sur des DONNÉES DE DÉMONSTRATION (DEMO_* ci-dessous) :
aucun compte, crédit ni fichier réel n'est créé. Elles servent à valider le
thème et les parcours avant de brancher la base (comptes, crédits, demandes).

Monté par portal.py sous /espace.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

bp = Blueprint("fs", __name__, url_prefix="/espace")

TZ = ZoneInfo("Europe/Paris")

SHOP = {"name": "E85FRANCE", "initials": "E85"}

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

PRESTATIONS = [
    {"code": "stage1", "nom": "Stage 1", "desc": "Optimisation puissance et couple", "credits": 8},
    {"code": "stage2", "nom": "Stage 2", "desc": "Pour véhicule avec pièces modifiées", "credits": 12},
    {"code": "e85", "nom": "E85 / Flexfuel", "desc": "Adaptation au bioéthanol", "credits": 10},
    {"code": "pops", "nom": "Pops & Bangs", "desc": "Crépitements à la décélération", "credits": 4},
    {"code": "vmax", "nom": "Limiteur de vitesse", "desc": "Réglage de la Vmax", "credits": 3},
    {"code": "dtc", "nom": "Désactivation DTC", "desc": "Codes défaut précisés en commentaire", "credits": 2},
    {"code": "startstop", "nom": "Start & Stop", "desc": "Désactivation par défaut", "credits": 2},
    {"code": "launch", "nom": "Launch control", "desc": "Aide au départ arrêté", "credits": 5},
]
URGENT_CREDITS = 4

TOOLS = ["KESS3", "Autotuner", "Flex", "CMD Flash", "MagicMotorsport", "KTAG", "PCMFlash", "Autre"]

DEMO_USER = {
    "company": "Garage Martin Performance",
    "contact": "Julien Martin",
    "initials": "JM",
    "credits": 42,
    "level": "Partenaire",
}

DEMO_FILES = [
    {"id": "F-24817", "vehicule": "Volkswagen Golf VII", "moteur": "2.0 TDI 150 ch", "annee": 2016,
     "ecu": "Bosch EDC17C64", "outil": "KESS3", "methode": "OBD", "prestas": ["Stage 1", "Start & Stop"],
     "credits": 10, "status": "en_cours", "date": "24/09 · 14:02", "vin": "WVWZZZAUZGW******", "km": 128400},
    {"id": "F-24812", "vehicule": "Audi A3 8V", "moteur": "1.4 TFSI 125 ch", "annee": 2015,
     "ecu": "Bosch MED17.5.25", "outil": "Autotuner", "methode": "OBD", "prestas": ["E85 / Flexfuel"],
     "credits": 10, "status": "pret", "date": "24/09 · 10:41", "vin": "WAUZZZ8V5FA******", "km": 96500},
    {"id": "F-24806", "vehicule": "BMW 320d F30", "moteur": "2.0d 184 ch", "annee": 2014,
     "ecu": "Bosch EDC17C50", "outil": "Flex", "methode": "Banc", "prestas": ["Stage 1", "Pops & Bangs"],
     "credits": 12, "status": "attente", "date": "23/09 · 17:25", "vin": "WBA3D31000F******", "km": 164200},
    {"id": "F-24791", "vehicule": "Peugeot 308 II", "moteur": "1.6 THP 205 ch", "annee": 2017,
     "ecu": "Bosch MED17.4.4", "outil": "KESS3", "methode": "Boot", "prestas": ["Stage 2", "Launch control"],
     "credits": 17, "status": "pret", "date": "23/09 · 09:12", "vin": "VF3LBHZTZHS******", "km": 58300},
    {"id": "F-24770", "vehicule": "Ford Focus III", "moteur": "1.0 EcoBoost 125 ch", "annee": 2016,
     "ecu": "Continental SID212", "outil": "CMD Flash", "methode": "OBD", "prestas": ["E85 / Flexfuel"],
     "credits": 10, "status": "pret", "date": "22/09 · 15:48", "vin": "WF0NXXGCHNG******", "km": 88100},
    {"id": "F-24752", "vehicule": "Renault Mégane IV RS", "moteur": "1.8 TCe 280 ch", "annee": 2019,
     "ecu": "Bosch MG1CS050", "outil": "Autotuner", "methode": "Banc", "prestas": ["Stage 1"],
     "credits": 8, "status": "refuse", "date": "21/09 · 11:30", "vin": "VF1RFB000K0******", "km": 41200,
     "motif": "Lecture incomplète : relire en mode banc complet."},
    {"id": "F-24733", "vehicule": "Seat Leon III Cupra", "moteur": "2.0 TSI 290 ch", "annee": 2016,
     "ecu": "Simos 18.1", "outil": "KESS3", "methode": "OBD", "prestas": ["Stage 1", "Pops & Bangs"],
     "credits": 12, "status": "pret", "date": "20/09 · 16:05", "vin": "VSSZZZ5FZGR******", "km": 72900},
    {"id": "F-24728", "vehicule": "Mercedes Classe A W176", "moteur": "A 180 CDI 109 ch", "annee": 2013,
     "ecu": "Delphi CRD3", "outil": "PCMFlash", "methode": "OBD", "prestas": ["Stage 1", "Désactivation DTC"],
     "credits": 10, "status": "recu", "date": "24/09 · 14:20", "vin": "WDD1760121J******", "km": 187600},
]

DEMO_THREAD = [
    {"me": False, "who": "Atelier · Thomas", "initials": "TR", "time": "Hier 17:40",
     "text": "Bonjour, la lecture est bien reçue. Pouvez-vous nous confirmer si l'échappement est d'origine ? "
             "On adapte les pops & bangs en conséquence."},
    {"me": True, "who": "Vous", "initials": "JM", "time": "Hier 18:02",
     "text": "Ligne complète inox depuis le turbo, catalyseur sport 200 cellules."},
    {"me": False, "who": "Atelier · Thomas", "initials": "TR", "time": "Aujourd'hui 09:15",
     "text": "Parfait, merci. Il nous manque encore la lecture EEPROM pour finaliser : "
             "vous pouvez la joindre ici directement."},
]

DEMO_TRANSACTIONS = [
    {"date": "24/09/2026", "libelle": "Fichier F-24817 · Stage 1 + Start & Stop", "montant": -10},
    {"date": "24/09/2026", "libelle": "Fichier F-24812 · E85 / Flexfuel", "montant": -10},
    {"date": "23/09/2026", "libelle": "Achat pack 50 crédits · facture FA-2026-0412", "montant": 50},
    {"date": "23/09/2026", "libelle": "Fichier F-24791 · Stage 2 + Launch control", "montant": -17},
    {"date": "21/09/2026", "libelle": "Remboursement F-24752 · lecture incomplète", "montant": 8},
    {"date": "21/09/2026", "libelle": "Fichier F-24752 · Stage 1", "montant": -8},
]

PACKS = [
    {"qty": 10, "price": 120, "featured": False},
    {"qty": 25, "price": 280, "featured": False},
    {"qty": 50, "price": 525, "featured": True},
    {"qty": 100, "price": 990, "featured": False},
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
    return render_template("fs/new.html", nav="new", prestations=PRESTATIONS, tools=TOOLS,
                           urgent=URGENT_CREDITS)


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
    return render_template("fs/credits.html", nav="credits", packs=PACKS,
                           transactions=DEMO_TRANSACTIONS)
