"""
Portail client — application SÉPARÉE et VERROUILLÉE.

Le client dépose son fichier et reçoit UNIQUEMENT un verdict :
    - compatible   : une solution existe et est prête
    - à vérifier   : compatibilité très probable, confirmation par l'atelier
    - non trouvé   : pas encore de solution répertoriée

Ce que le portail NE FAIT JAMAIS :
    - lister la bibliothèque de solutions
    - renvoyer ou laisser télécharger un fichier de solution
    - révéler le véhicule/le client d'origine d'une solution
    - exposer un score, une raison technique ou une calibration

Il réutilise le même moteur de matching que l'outil interne (en lecture seule),
mais ne renvoie au client que des champs sûrs. Les fichiers déposés sont rangés
dans data/portal_inbox/ pour que l'atelier les traite ensuite dans l'outil interne.
"""
import json
import os
import re
import secrets
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from matcher import db, engine
import comptes
import demandes
import factures
import api
import fileservice
import push
import relances
import sante
import taches

APP_VERSION = "1.57.0"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 Mo max par dépôt
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

DB_PATH = os.environ.get("CARTO_DB", db.DEFAULT_DB)
DATA_DIR = os.path.dirname(os.path.abspath(DB_PATH))
CONFIG_PATH = os.path.join(DATA_DIR, "portal_config.json")
INBOX_DIR = os.path.join(DATA_DIR, "portal_inbox")

DEFAULT_CONFIG = {
    "shop_name": "E85-FRANCE",
    "intro": "Déposez votre fichier d'origine : nous vérifions instantanément "
             "si une solution est disponible pour votre calculateur.",
    "show_prices": False,          # False -> tarif "sur devis"
    "currency": "€",
    "default_price": None,         # prix unique éventuel (ex: 150)
    "prices": {},                  # ex: {"DPF off": 150, "AdBlue off": 200}
    "delays": {},                  # ex: {"E85 / Flexfuel": "24 h"}
    "default_delay": "",           # ex: "sous 48 h"
    "contact": "",                 # email/téléphone affiché en cas de "non trouvé"
    "ask_contact": True,           # demander email/tel au client (capture de lead)
    "access_password_hash": None,  # verrou OPTIONNEL — voir avertissement plus bas
}


def load_config():
    """Charge la config tarifs/portail ; la crée avec des valeurs par défaut si absente."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_CONFIG, fh, ensure_ascii=False, indent=2)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg or {})
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def _lookup(table, name):
    """Recherche insensible à la casse + alias (DPF off → DPF/FAP off)."""
    from matcher import metadata as md
    if not table or not name:
        return None
    if name in table:
        return table[name]
    canon = md.canonical_atom(name)
    if canon in table:
        return table[canon]
    low = {str(k).lower(): v for k, v in table.items()}
    if name.lower() in low:
        return low[name.lower()]
    if canon.lower() in low:
        return low[canon.lower()]
    for alias, target in md.TYPE_ALIASES.items():
        if target == canon and alias in low:
            return low[alias]
    return None


def price_for(solution_type, cfg):
    """Tarif affiché pour une prestation (ou 'sur devis')."""
    if not cfg.get("show_prices"):
        return "sur devis"
    hit = _lookup(cfg.get("prices") or {}, solution_type)
    if hit is not None:
        return f"{hit} {cfg.get('currency', '€')}"
    if cfg.get("default_price") is not None:
        return f"{cfg['default_price']} {cfg.get('currency', '€')}"
    return "sur devis"


def delay_for(solution_type, cfg):
    hit = _lookup(cfg.get("delays") or {}, solution_type)
    if hit:
        return str(hit)
    return (cfg.get("default_delay") or "").strip()


def get_secret_key():
    cfg = load_config()
    key = cfg.get("secret_key")
    if not key:
        key = secrets.token_hex(32)
        cfg["secret_key"] = key
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    return key


app.secret_key = get_secret_key()

# --- Espace client (fileservice) ---
app.config["FS_DB"] = os.environ.get("CARTO_FS_DB") or os.path.join(DATA_DIR, "fileservice.db")
app.config["FS_CONFIG"] = CONFIG_PATH
app.config["FS_DATA_DIR"] = DATA_DIR
app.config["FS_PUBLIC_URL"] = os.environ.get("CARTO_PUBLIC_URL", "")   # sinon public_url de portal_config.json
app.config["FS_CLIENT_IP"] = lambda: _client_ip()
app.config["APP_VERSION"] = APP_VERSION
app.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 3600   # « rester connecté » : 30 jours
if os.environ.get("CARTO_PROD") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True   # derrière HTTPS uniquement
app.config["FS_FILES"] = os.environ.get("CARTO_FS_FILES") or os.path.join(DATA_DIR, "fileservice_fichiers")


def _detecter(data, filename):
    """Calculateur détecté + verdict bibliothèque, pour l'atelier (jamais montré au client)."""
    result = engine.match(data, DB_PATH, path=filename)
    incoming = result.get("incoming", {})
    verdict, _ = engine.portal_verdict(result)
    return {"plateforme": incoming.get("platform"), "fabricant": incoming.get("manufacturer"),
            "verdict": verdict}


app.config["FS_DETECT"] = _detecter
app.config["FS_SOLUTIONS_DB"] = DB_PATH   # bibliothèque (lecture) pour la livraison automatique
demandes.init_db(app.config["FS_DB"])
factures.init_db(app.config["FS_DB"])
relances.init_db(app.config["FS_DB"])
api.init_db(app.config["FS_DB"])
push.init_db(app.config["FS_DB"])
app.register_blueprint(fileservice.bp)
app.register_blueprint(api.bp)


def _safe_next(raw, fallback):
    raw = (raw or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or ":" in raw:
        return fallback
    return raw


@app.before_request
def _maybe_require_login():
    """Verrou OPTIONNEL et désactivé par défaut. Ne l'active que pour un usage
    restreint (tests, accès réservé à des clients connus via un lien privé) :
    un vrai portail public ne doit PAS avoir de mot de passe, sinon les clients
    ne peuvent plus déposer leur fichier."""
    cfg = load_config()
    if not cfg.get("access_password_hash"):
        return None
    if request.endpoint in ("portal_login", "static", "espace_legacy", "sante_publique") or (request.endpoint or "").startswith(("fs.", "api.")):
        return None   # l'espace client a ses propres comptes
    if session.get("authed"):
        return None
    if request.endpoint == "home":
        return redirect(url_for("portal_login", next=request.path))
    return jsonify({"error": "Accès restreint."}), 401


@app.route("/espace", defaults={"reste": ""})
@app.route("/espace/<path:reste>")
def espace_legacy(reste):
    """L'espace client était sous /espace : les anciens liens (e-mails, favoris) redirigent."""
    cible = "/" + reste
    if request.query_string:
        cible += "?" + request.query_string.decode("latin-1")
    return redirect(cible, code=308)


@app.route("/portal-login", methods=["GET", "POST"])
def portal_login():
    error = None
    cfg = load_config()
    if request.method == "POST":
        pw = request.form.get("password", "")
        h = cfg.get("access_password_hash")
        if h and check_password_hash(h, pw):
            session["authed"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next"), url_for("home")))
        error = "Mot de passe incorrect."
    return render_template("portal_login.html", error=error, cfg=cfg, version=APP_VERSION)


def _safe_name(name):
    name = os.path.basename(name or "fichier")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "fichier"


def store_lead(filename, data, verdict, info, contact):
    """Range le fichier déposé + journalise la demande pour l'atelier (lead)."""
    try:
        os.makedirs(INBOX_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = f"{stamp}_{_safe_name(filename)}"
        with open(os.path.join(INBOX_DIR, safe), "wb") as fh:
            fh.write(data)
        record = {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fichier": safe,
            "verdict": verdict,
            "plateforme": info.get("platform"),
            "fabricant": info.get("manufacturer"),
            "contact": contact,
            "prestas": info.get("wanted_prestas") or [],
            "ids": info.get("wanted_ids") or [],
            "token": safe,
        }
        with open(os.path.join(INBOX_DIR, "demandes.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return safe
    except Exception:
        return None


@app.route("/verifier")
def home():
    """Ancienne page publique : vérification anonyme d'un fichier (l'espace client est à la racine)."""
    cfg = load_config()
    return render_template("portal.html", cfg=cfg, version=APP_VERSION)


# --- Limitation anti-abus : nombre de dépôts par IP et par heure ---
_RATE = {}
RATE_WINDOW = 3600


def _client_ip():
    """IP du client. X-Forwarded-For n'est lu QUE si CARTO_TRUST_PROXY=1
    (derrière Cloudflare / reverse proxy). Sinon n'importe qui pourrait
    spoof l'en-tête et contourner le limiteur."""
    if os.environ.get("CARTO_TRUST_PROXY") == "1":
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip() or (request.remote_addr or "?")
    return request.remote_addr or "?"


def _rate_limited(cfg):
    limit = cfg.get("max_depots_heure", 10)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    if limit <= 0:
        return False
    now = time.time()
    ip = _client_ip()
    stamps = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(stamps) >= limit:
        _RATE[ip] = stamps
        return True
    stamps.append(now)
    _RATE[ip] = stamps
    if len(_RATE) > 10000:
        # Purge les IPs dont la fenêtre est expirée — ne vide PAS tout le limiteur.
        expired = [k for k, v in _RATE.items() if not v or now - v[-1] >= RATE_WINDOW]
        for k in expired:
            _RATE.pop(k, None)
    return False


@app.route("/check", methods=["POST"])
def check():
    cfg = load_config()
    if _rate_limited(cfg):
        return jsonify({"error": "Trop de dépôts depuis votre connexion. "
                                 "Réessayez dans une heure ou contactez-nous."}), 429
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Aucun fichier reçu."}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "Fichier vide."}), 400

    result = engine.match(data, DB_PATH, path=f.filename or "")
    incoming = result.get("incoming", {})
    verdict, relevant = engine.portal_verdict(result)
    atoms = engine.portal_offers(relevant)

    if verdict == "compatible":
        if len(atoms) > 1:
            titre = "Solutions disponibles"
            message = (f"{len(atoms)} prestations possibles pour ce fichier. "
                       "Indiquez celle que vous souhaitez.")
        else:
            titre = "Solution disponible"
            message = "Une solution est prête pour votre fichier."
    elif verdict == "a_verifier":
        titre = "Compatibilité très probable"
        message = ("Votre calculateur correspond à une solution connue. "
                   "Une vérification finale est faite par nos soins avant livraison.")
        if len(atoms) > 1:
            message += f" {len(atoms)} prestations possibles."
    else:
        titre = "Pas de solution répertoriée pour l'instant"
        message = ("Nous n'avons pas encore de solution prête pour ce fichier précis. "
                   "Contactez-nous : c'est souvent réalisable sur étude.")
        relevant = []
        atoms = []

    offer_rows = engine.portal_offer_rows(relevant)
    prestations = []
    for off in offer_rows:
        st = off["prestation"]
        row = {"prestation": st, "tarif": price_for(st, cfg), "ids": off.get("ids") or []}
        dly = delay_for(st, cfg)
        if dly:
            row["delai"] = dly
        prestations.append(row)
        if len(prestations) >= 8:
            break
    incoming["wanted_prestas"] = atoms
    incoming["wanted_ids"] = [i for row in prestations for i in (row.get("ids") or [])]

    contact = {
        "nom": (request.form.get("nom") or "").strip()[:80],
        "email": (request.form.get("email") or "").strip()[:120],
        "tel": (request.form.get("tel") or "").strip()[:40],
        "vehicule": (request.form.get("vehicule") or "").strip()[:120],
        "immat": (request.form.get("immat") or "").strip()[:16],
        "vin": (request.form.get("vin") or "").strip()[:20],
    }
    token = store_lead(f.filename, data, verdict, incoming, contact)

    return jsonify({
        "verdict": verdict,
        "titre": titre,
        "message": message,
        "prestations": prestations,
        "token": token,
        "contact_atelier": cfg.get("contact", ""),
    })


@app.route("/order", methods=["POST"])
def order():
    """Le partenaire coche les prestas voulues après le /check."""
    token = (request.form.get("token") or (request.json or {}).get("token") or "").strip()
    raw = request.form.get("prestas") or ""
    if request.is_json:
        raw = (request.json or {}).get("prestas") or raw
    if isinstance(raw, str):
        wanted = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    else:
        wanted = [str(p).strip() for p in (raw or []) if str(p).strip()]
    if not token or ".." in token or "/" in token:
        return jsonify({"error": "Demande introuvable."}), 400
    log_path = os.path.join(INBOX_DIR, "demandes.log")
    updated = False
    rows = []
    if os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("fichier") == token or rec.get("token") == token:
                    rec["prestas"] = wanted
                    rec["commande"] = True
                    updated = True
                rows.append(rec)
    if updated:
        with open(log_path, "w", encoding="utf-8") as fh:
            for rec in rows:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return jsonify({"ok": True, "prestas": wanted, "updated": updated})


@app.route("/sante")
def sante_publique():
    """Pour un service de surveillance externe (UptimeRobot…) : 200 si le portail et sa base répondent.
    Aucun détail n'est exposé ; le détail est dans l'outil atelier."""
    ok = sante.base_ok(app.config["FS_DB"])
    return jsonify({"ok": ok, "version": APP_VERSION}), (200 if ok else 503)


def _sauvegardes_quotidiennes():
    """Fil en arrière-plan (python portal.py) : tâches planifiées toutes les heures — sauvegarde
    quotidienne, sauvegarde externe, relances, alertes. Chez un hébergeur (Passenger), c'est une
    tâche cron qui les lance : python outils_prod.py taches."""
    import threading

    def boucle():
        while True:
            try:
                taches.executer(app, f"http://127.0.0.1:{os.environ.get('PORT', '5001')}")
            except Exception as e:   # une tâche ratée ne doit jamais arrêter le portail
                print(f"  ⚠ Tâches planifiées : {e}")
            time.sleep(3600)

    threading.Thread(target=boucle, name="taches-fileservice", daemon=True).start()


if __name__ == "__main__":
    host = os.environ.get("CARTO_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5001"))
    lan = host not in ("127.0.0.1", "localhost")
    cfg = load_config()
    db.init_db(DB_PATH)
    _sauvegardes_quotidiennes()
    print("=" * 60)
    print(f"  Portail client — Carto Matcher v{APP_VERSION}")
    print(f"  Atelier         : {cfg.get('shop_name')}")
    print(f"  Base (lecture)  : {os.path.abspath(DB_PATH)}")
    print(f"  Dépôts clients  : {INBOX_DIR}")
    print(f"  Tarifs          : {'affichés' if cfg.get('show_prices') else 'sur devis'}")
    if cfg.get("access_password_hash"):
        print("  Verrou          : ACTIF — mot de passe requis (⚠ bloque aussi les vrais clients)")
    else:
        print("  Verrou          : aucun — accès libre (usage public normal)")
    if lan:
        print(f"  Mode RÉSEAU — portail accessible sur le réseau local, port {port}")
    else:
        print(f"  Ouvre http://127.0.0.1:{port}")
    print("  Le portail ne révèle JAMAIS la bibliothèque de solutions.")
    prod = os.environ.get("CARTO_PROD", "") == "1"
    if prod:
        try:
            from waitress import serve
            print("  Serveur         : waitress (production)")
            print("=" * 60)
            serve(app, host=host, port=port, threads=8)
            raise SystemExit
        except ImportError:
            print("  ⚠ waitress n'est pas installé (pip install waitress) —")
            print("    démarrage avec le serveur de développement à la place.")
    print("=" * 60)
    app.run(host=host, port=port, debug=False, threaded=True)
