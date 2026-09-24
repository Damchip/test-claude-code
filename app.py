"""
Serveur local Carto Matcher.

Lance une petite interface web accessible UNIQUEMENT depuis ta machine
(127.0.0.1). Rien n'est exposé sur le réseau. Démarrage :

    python app.py

puis ouvre http://127.0.0.1:5000 dans ton navigateur.
"""

import json
import io
import os
import sys
import secrets
import threading
import time
from datetime import timedelta

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from matcher import ai, atelier, batch, db, dossiers as dos, engine, extract, importer, maps as mapsmod, pack as packmod, patch as pmod
import comptes
import mailer

app = Flask(__name__)
APP_VERSION = "1.52.0"
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
def _require_login():
    if not access_password_set():
        return None  # pas de mot de passe configuré -> accès libre (comportement d'origine)
    if request.endpoint in ("login", "static"):
        return None
    if session.get("authed"):
        return None
    if request.endpoint == "index":
        return redirect(url_for("login", next=request.path))
    return jsonify({"error": "Session expirée, reconnecte-toi."}), 401


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if check_access_password(pw):
            session["authed"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next"), url_for("index")))
        error = "Mot de passe incorrect."
    return render_template("login.html", error=error, version=APP_VERSION)


@app.route("/logout")
def logout():
    session.pop("authed", None)
    return redirect(url_for("login"))


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
    return render_template("index.html", db_size=db.count(DB_PATH), version=APP_VERSION)


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
    return jsonify({"solutions": db.list_solutions(DB_PATH, q),
                    "db_size": db.count(DB_PATH)})


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
                    "has_password": bool(cfg.get("access_password_hash"))})


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
    cfg.pop("smtp", None)   # géré par /clients/smtp, jamais renvoyé avec son mot de passe
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
    comptes.init_db(FS_DB)
    return jsonify({"clients": comptes.lister_clients(FS_DB)})


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
        acces = (f"Connectez-vous avec votre e-mail et le mot de passe choisi à l'inscription :\n"
                 f"{base}/espace/connexion" if base else
                 "Connectez-vous à votre espace client avec votre e-mail et le mot de passe choisi à l'inscription.")
        ok, err = _mail_client(
            avant["email"], f"{cfg.get('shop_name') or 'E85-FRANCE'} — votre compte est ouvert",
            f"Bonjour,\n\nVotre compte fileservice pour {avant['societe']} est maintenant actif.\n"
            f"{acces}\n\n{cfg.get('shop_name') or 'E85-FRANCE'}")
        mail = "E-mail d'activation envoyé." if ok else err
        if not base:
            mail += " (Adresse publique du portail non renseignée : l'e-mail ne contient pas de lien.)"
    return jsonify({"ok": True, "mail": mail})


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
    app.run(host=host, port=port, debug=False, threaded=True)
