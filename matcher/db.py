"""
Base de solutions — SQLite local, jamais en ligne.

Chaque ligne = une solution fonctionnelle déjà traitée :
  - ecu_version    : clé de matching principale (numéro de calibration)
  - vehicle_label  : libellé lisible (marque / modèle / moteur)
  - solution_type  : Stage 1, DPF off, EGR off, E85 / Flexfuel, ...
  - tested_status  : a_confirmer | testee | validee_client
  - stock_sha256   : empreinte exacte du fichier d'origine (stock)
  - stock_size     : taille du stock
  - minhash        : signature de similarité (JSON)
  - solution_file  : chemin local vers le fichier solution (optionnel)
  - notes          : remarques techniciens

Unicité métier : (stock_sha256, solution_type) — le même original peut porter
plusieurs prestations (Stage 1 + FAP off + EGR off).
"""

import datetime
import json
from array import array
import os
import re
import shutil
import sqlite3
import time

DEFAULT_DB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "solutions.db"))


def backup_db(db_path=DEFAULT_DB, keep=15):
    """Copie horodatée de la base avant toute session, dans data/backups/.
    Ne sauvegarde pas une base vide ou inexistante. Garde les `keep` plus récentes.
    Renvoie le chemin de la sauvegarde, ou None."""
    if not os.path.isfile(db_path) or os.path.getsize(db_path) < 1024:
        return None
    try:
        if count(db_path) == 0:
            return None
    except Exception:
        return None
    bdir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = os.path.join(bdir, f"solutions-{stamp}.db")
    shutil.copy2(db_path, dest)
    backups = sorted(
        (os.path.join(bdir, f) for f in os.listdir(bdir)
         if f.startswith("solutions-") and f.endswith(".db")),
        key=os.path.getmtime, reverse=True)
    for old in backups[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dest

SCHEMA = """
CREATE TABLE IF NOT EXISTS solutions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ecu_version   TEXT,
    ecu_hw        TEXT,
    ecu_platform  TEXT,
    manufacturer  TEXT,
    vehicle_label TEXT,
    solution_type TEXT,
    tested_status TEXT DEFAULT 'a_confirmer',
    stock_sha256  TEXT,
    stock_size    INTEGER,
    minhash       TEXT,
    solution_file TEXT,
    original_file TEXT,
    notes         TEXT,
    created_at    REAL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_ecu_version ON solutions(ecu_version);
CREATE INDEX IF NOT EXISTS idx_stock_sha256 ON solutions(stock_sha256);
CREATE INDEX IF NOT EXISTS idx_platform ON solutions(ecu_platform);
CREATE INDEX IF NOT EXISTS idx_sha_type ON solutions(stock_sha256, solution_type);
"""


def _connect(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=8.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


EXPECTED_COLUMNS = {
    "ecu_version": "TEXT", "ecu_hw": "TEXT", "ecu_platform": "TEXT",
    "manufacturer": "TEXT", "vehicle_label": "TEXT", "solution_type": "TEXT",
    "tested_status": "TEXT", "stock_sha256": "TEXT", "stock_size": "INTEGER",
    "minhash": "TEXT", "solution_file": "TEXT", "original_file": "TEXT",
    "notes": "TEXT", "tags": "TEXT", "created_at": "REAL",
    "minhash_ver": "INTEGER",
}


def init_db(db_path=DEFAULT_DB):
    conn = _connect(db_path)
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(solutions)").fetchall()}
    for col, typ in EXPECTED_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE solutions ADD COLUMN {col} {typ}")
    conn.executescript(INDEXES)
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at REAL, client_name TEXT, solution_id INTEGER,
        solution_label TEXT, verdict TEXT, zones INTEGER, applied INTEGER, note TEXT)""")
    conn.commit()
    conn.close()
    from . import dossiers as dos
    dos.init(db_path)


def _ensure_job_cols(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "stock_sha256" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN stock_sha256 TEXT")
    if "ecu_version" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN ecu_version TEXT")


def add_job(db_path=DEFAULT_DB, *, client_name="", solution_id=None,
            solution_label="", verdict="", zones=0, applied=1, note="",
            dossier_id=None, stock_sha256="", ecu_version=""):
    conn = _connect(db_path)
    _ensure_job_cols(conn)
    if solution_id and not stock_sha256:
        row = conn.execute(
            "SELECT stock_sha256, ecu_version FROM solutions WHERE id=?",
            (solution_id,)).fetchone()
        if row:
            stock_sha256 = stock_sha256 or (row["stock_sha256"] or "")
            ecu_version = ecu_version or (row["ecu_version"] or "")
    cur = conn.execute(
        """INSERT INTO jobs (created_at, client_name, solution_id, solution_label,
                             verdict, zones, applied, note, dossier_id,
                             stock_sha256, ecu_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (time.time(), client_name, solution_id, solution_label, verdict,
         zones, 1 if applied else 0, note, dossier_id,
         stock_sha256 or "", ecu_version or ""))
    conn.commit()
    jid = cur.lastrowid
    conn.close()
    if dossier_id:
        from . import dossiers as dos
        dos.attach_job(db_path, jid, dossier_id)
        if solution_label:
            dos.add_prestas(db_path, dossier_id, solution_label)
    return jid


def list_jobs(db_path=DEFAULT_DB):
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_history(db_path=DEFAULT_DB, sha="", ecu=""):
    """Packs déjà livrés pour ce stock (sha) ou cette calibre."""
    sha = (sha or "").strip().lower()
    ecu = (ecu or "").strip()
    if not sha and not ecu:
        return []
    conn = _connect(db_path)
    _ensure_job_cols(conn)
    ids = set()
    if sha:
        for r in conn.execute(
                "SELECT id FROM solutions WHERE lower(stock_sha256)=?", (sha,)):
            ids.add(r["id"])
    if ecu:
        for r in conn.execute(
                "SELECT id FROM solutions WHERE ecu_version=?", (ecu,)):
            ids.add(r["id"])
    clauses, args = [], []
    if sha:
        clauses.append("lower(COALESCE(stock_sha256,''))=?")
        args.append(sha)
    if ecu:
        clauses.append("ecu_version=?")
        args.append(ecu)
    if ids:
        q = ",".join("?" * len(ids))
        clauses.append(f"solution_id IN ({q})")
        args.extend(ids)
    sql = "SELECT * FROM jobs WHERE " + " OR ".join(clauses) + " ORDER BY created_at DESC LIMIT 30"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    out = []
    seen = set()
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append(r)
    return out


def update_job(db_path=DEFAULT_DB, job_id=None, note=None):
    conn = _connect(db_path)
    if note is not None:
        conn.execute("UPDATE jobs SET note=? WHERE id=?", (note, job_id))
    conn.commit()
    conn.close()
    return True


def delete_job(db_path=DEFAULT_DB, job_id=None):
    conn = _connect(db_path)
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()
    conn.close()
    return True


def files_root(db_path=DEFAULT_DB):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "files")


def _store_path(db_path, path):
    """Chemin relatif au dossier de la base si possible (portable)."""
    if not path:
        return ""
    data_dir = os.path.dirname(os.path.abspath(db_path))
    try:
        rel = os.path.relpath(os.path.abspath(path), data_dir)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except ValueError:
        pass
    return path


def resolve_file(db_path, stored):
    """Retrouve un fichier archivé même si le dossier a été déplacé."""
    if not stored:
        return ""
    if os.path.isfile(stored):
        return stored
    data_dir = os.path.dirname(os.path.abspath(db_path))
    norm = stored.replace("\\", os.sep).replace("/", os.sep)
    for cand in (os.path.join(data_dir, stored), os.path.join(data_dir, norm),
                 os.path.join(data_dir, os.path.basename(os.path.dirname(norm)),
                              os.path.basename(norm))):
        if os.path.isfile(cand):
            return cand
    return stored


def _hydrate_paths(db_path, d):
    if not d:
        return d
    d["original_file"] = resolve_file(db_path, d.get("original_file") or "")
    d["solution_file"] = resolve_file(db_path, d.get("solution_file") or "")
    return d


def copie_active(db_path=DEFAULT_DB):
    """Réglage « Copier les fichiers dans l'application » (config.json à côté de la base). Désactivé par défaut :
    les fiches gardent le lien vers les fichiers d'origine (OneDrive…), sans double."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(db_path)), "config.json"), encoding="utf-8") as fh:
            return bool(json.load(fh).get("copier_fichiers", False))
    except (OSError, ValueError, AttributeError):
        return False


def archive_files(db_path, sol_id, original_path="", solution_path=""):
    """Enregistre les fichiers d'une fiche. Si la copie est activée, copie original + solution dans
    data/files/<id>/ (indépendant de OneDrive) ; sinon garde simplement les chemins d'origine.
    Met à jour les chemins en base. Si la copie échoue, les chemins d'origine restent."""
    if not copie_active(db_path):
        conn = _connect(db_path)
        conn.execute("UPDATE solutions SET original_file=?, solution_file=? WHERE id=?",
                     (_store_path(db_path, original_path or ""), _store_path(db_path, solution_path or ""), sol_id))
        conn.commit()
        conn.close()
        return original_path or "", solution_path or ""
    dest = os.path.join(files_root(db_path), f"{int(sol_id):06d}")
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError:
        return original_path or "", solution_path or ""
    out_ori, out_sol = original_path or "", solution_path or ""
    for src, name, slot in (
        (original_path, "original", "ori"),
        (solution_path, "solution", "sol"),
    ):
        if not src or not os.path.isfile(src):
            continue
        ext = os.path.splitext(src)[1] or ".bin"
        target = os.path.join(dest, name + ext)
        try:
            if os.path.abspath(src) != os.path.abspath(target):
                shutil.copy2(src, target)
            if slot == "ori":
                out_ori = target
            else:
                out_sol = target
        except OSError:
            pass
    conn = _connect(db_path)
    conn.execute("UPDATE solutions SET original_file=?, solution_file=? WHERE id=?",
                 (_store_path(db_path, out_ori), _store_path(db_path, out_sol), sol_id))
    conn.commit()
    conn.close()
    return out_ori, out_sol


def add_solution(db_path=DEFAULT_DB, *, ecu_version="", ecu_hw="",
                 ecu_platform="", manufacturer="",
                 vehicle_label="", solution_type="", tested_status="a_confirmer",
                 stock_sha256="", stock_size=0, minhash=None,
                 solution_file="", original_file="", notes="", minhash_ver=2):
    conn = _connect(db_path)
    cur = conn.execute(
        """INSERT INTO solutions
           (ecu_version, ecu_hw, ecu_platform, manufacturer, vehicle_label,
            solution_type, tested_status, stock_sha256, stock_size, minhash,
            solution_file, original_file, notes, created_at, minhash_ver)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ecu_version, ecu_hw, ecu_platform, manufacturer, vehicle_label,
         solution_type, tested_status, stock_sha256, stock_size,
         json.dumps(minhash or []), solution_file, original_file, notes,
         time.time(), int(minhash_ver or 2)),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    if original_file or solution_file:
        archive_files(db_path, new_id, original_file, solution_file)
    return new_id


def all_solutions(db_path=DEFAULT_DB):
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM solutions").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["minhash"] = json.loads(d["minhash"]) if d["minhash"] else []
        d["minhash_ver"] = int(d["minhash_ver"] or 1) if d.get("minhash_ver") is not None else 1
        out.append(d)
    return out


_CACHE_RECHERCHE = {}


def _signature_base(db_path):
    sig = []
    for suffixe in ("", "-wal"):
        try:
            st = os.stat(db_path + suffixe)
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


def solutions_pour_recherche(db_path=DEFAULT_DB):
    """Toutes les fiches (avec MinHash) pour la reconnaissance, gardées en mémoire tant que la base ne change pas :
    avec des dizaines de milliers de fiches, relire et décoder la base à chaque fichier coûterait près d'une seconde.
    La liste renvoyée est partagée : ne pas la modifier."""
    cle = os.path.abspath(db_path)
    sig = _signature_base(cle)
    c = _CACHE_RECHERCHE.get(cle)
    if c and c[0] == sig:
        return c[1]
    sols = all_solutions(db_path)
    for d in sols:
        try:
            d["minhash"] = array("Q", d["minhash"])     # 8 octets par valeur au lieu d'un objet Python
        except (OverflowError, TypeError):
            d["minhash"] = tuple(d["minhash"])
    _CACHE_RECHERCHE[cle] = (sig, sols)
    return sols


def _norm_type(t):
    return (t or "").strip().casefold()


def sha256_exists(db_path=DEFAULT_DB, sha256=""):
    """True s'il existe AU MOINS une fiche pour ce stock (n'importe quel type)."""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT 1 FROM solutions WHERE stock_sha256=? LIMIT 1", (sha256,)
    ).fetchone()
    conn.close()
    return row is not None


def sha256_type_exists(db_path=DEFAULT_DB, sha256="", solution_type=""):
    """Unicité métier : même original + même type de prestation déjà en base."""
    want = _norm_type(solution_type)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT solution_type FROM solutions WHERE stock_sha256=?", (sha256,)
    ).fetchall()
    conn.close()
    return any(_norm_type(r["solution_type"]) == want for r in rows)


def count(db_path=DEFAULT_DB):
    conn = _connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM solutions").fetchone()[0]
    conn.close()
    return n


EDITABLE = ("ecu_version", "ecu_platform", "manufacturer", "vehicle_label",
            "solution_type", "tested_status", "notes", "tags")


def list_solutions(db_path=DEFAULT_DB, q="", hydrate=True):
    """Liste légère (sans minhash) pour l'affichage et l'édition.
    hydrate=False : chemins tels qu'enregistrés, sans vérifier le disque (liste de milliers de fiches instantanée)."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT id, ecu_version, ecu_platform, manufacturer, vehicle_label,
                  solution_type, tested_status, notes, tags, stock_size, solution_file,
                  original_file, created_at
           FROM solutions ORDER BY created_at DESC"""
    ).fetchall()
    conn.close()
    out = [_hydrate_paths(db_path, dict(r)) if hydrate else dict(r) for r in rows]
    if q:
        ql = q.lower()
        out = [d for d in out
               if ql in " ".join(str(v or "") for v in d.values()).lower()]
    return out


def get_solution(db_path=DEFAULT_DB, sol_id=None):
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM solutions WHERE id=?", (sol_id,)).fetchone()
    conn.close()
    return _hydrate_paths(db_path, dict(row)) if row else None


def update_solution(db_path=DEFAULT_DB, sol_id=None, **fields):
    sets = [(k, fields[k]) for k in EDITABLE if k in fields]
    if not sets or sol_id is None:
        return False
    cols = ", ".join(f"{k}=?" for k, _ in sets)
    vals = [v for _, v in sets] + [sol_id]
    conn = _connect(db_path)
    conn.execute(f"UPDATE solutions SET {cols} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return True


def delete_solution(db_path=DEFAULT_DB, sol_id=None):
    folder = os.path.join(files_root(db_path), f"{int(sol_id):06d}") if sol_id is not None else ""
    conn = _connect(db_path)
    conn.execute("DELETE FROM solutions WHERE id=?", (sol_id,))
    conn.commit()
    conn.close()
    if folder and os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    return True


def rebuild_fingerprints(db_path=DEFAULT_DB, limit=500):
    """Recalcule MinHash v2 (sans padding) + sha du corps pour les fiches
    dont l'original est encore sur le disque. Reprend lot par lot."""
    from . import atelier
    out = atelier.sync_batch(db_path, limit=limit)
    return {
        "updated": out.get("fingerprints", 0),
        "skipped": out.get("skipped", 0) + out.get("missing", 0),
        "errors": out.get("errors", 0),
        "remaining": out.get("remaining", 0),
        "ecu": out.get("ecu", 0),
        "archived": out.get("archived", 0),
    }


def backfill_originals(db_path=DEFAULT_DB):
    """Reconstruit original_file pour les fiches qui n'en ont pas, à partir du
    dossier du fichier solution + le nom d'origine mémorisé dans les notes
    (l'original et la solution sont dans le même dossier)."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT id, solution_file, notes FROM solutions
           WHERE (original_file IS NULL OR original_file='')
             AND solution_file IS NOT NULL AND solution_file!=''"""
    ).fetchall()
    checked = len(rows)
    fixed = missing = 0
    for r in rows:
        notes = r["notes"] or ""
        m = re.search(r"original:\s*(.*?)\s*\|\s*solution:", notes)
        if not m:
            continue
        ori_name = m.group(1).strip()
        if not ori_name or ori_name == "(aucun)":
            continue
        cand = os.path.join(os.path.dirname(r["solution_file"]), ori_name)
        if os.path.isfile(cand):
            conn.execute("UPDATE solutions SET original_file=? WHERE id=?", (cand, r["id"]))
            fixed += 1
        else:
            missing += 1
    conn.commit()
    conn.close()
    return {"checked": checked, "fixed": fixed, "missing": missing}


def backfill_metadata(db_path=DEFAULT_DB, force=False):
    """Relit type / plateforme / marque / libellé depuis les chemins (original +
    solution). Ne touche pas aux champs déjà remplis, sauf plateformes-bruit
    (dMe, me0m…) et libellés visiblement faux (Jaguar CLAAS, .cache, .mpc).
    `force=True` réécrit aussi les types déjà renseignés."""
    from . import metadata as md
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM solutions").fetchall()
    stats = {"checked": len(rows), "types": 0, "platforms": 0,
             "labels": 0, "manufacturers": 0, "junk": 0, "unchanged": 0}
    for r in rows:
        d = dict(r)
        cur_lab = (d.get("vehicle_label") or "").strip()
        path_blob = " ".join((d.get("original_file") or "",
                              d.get("solution_file") or "",
                              cur_lab)).lower()
        junk = ".cache" in path_blob or cur_lab.lower() in {".cache", "backup merdasse"}
        if junk:
            tags = [t.strip() for t in (d.get("tags") or "").split(",") if t.strip()]
            if "suspect" not in tags:
                conn.execute("UPDATE solutions SET tags=? WHERE id=?",
                             (", ".join(tags + ["suspect"]), d["id"]))
            stats["junk"] += 1
            continue

        meta = md.parse_record(d.get("original_file") or "",
                               d.get("solution_file") or "",
                               cur_lab)
        sets, vals = [], []

        cur_type = (d.get("solution_type") or "").strip()
        new_type = (meta.get("solution_type") or "").strip()
        if new_type and (force or not cur_type) and new_type != cur_type:
            sets.append("solution_type=?"); vals.append(new_type)
            stats["types"] += 1

        cur_plat = md.normalize_platform(d.get("ecu_platform"))
        new_plat = meta.get("platform") or cur_plat
        stored_plat = (d.get("ecu_platform") or "").strip()
        if new_plat and new_plat != stored_plat:
            sets.append("ecu_platform=?"); vals.append(new_plat)
            stats["platforms"] += 1
        elif stored_plat and cur_plat is None:
            # bruit binaire : on vide
            sets.append("ecu_platform=?"); vals.append("")
            stats["platforms"] += 1

        cur_man = (d.get("manufacturer") or "").strip()
        new_man = (meta.get("manufacturer") or "").strip()
        if new_man and not cur_man:
            sets.append("manufacturer=?"); vals.append(new_man)
            stats["manufacturers"] += 1

        new_lab = (meta.get("vehicle") or "").strip()
        if new_lab and _label_needs_refresh(cur_lab, meta.get("brand"), new_lab) and new_lab != cur_lab:
            sets.append("vehicle_label=?"); vals.append(new_lab)
            stats["labels"] += 1

        if sets:
            vals.append(d["id"])
            conn.execute(f"UPDATE solutions SET {', '.join(sets)} WHERE id=?", vals)
        else:
            stats["unchanged"] += 1
    conn.commit()
    conn.close()
    return stats


def _label_needs_refresh(label, brand, new_label):
    lab = (label or "").strip()
    if not lab:
        return True
    low = lab.lower()
    if low in {".cache", "backup merdasse"}:
        return True
    if re.search(r"\.(bin|mpc|cod|ori|mod|dec)(\.|$)", lab, re.I):
        return True
    if re.match(r"^(lecture|relecture|ori|winols)\b", lab, re.I):
        return True
    if (brand or "") == "Claas" and re.match(r"^jaguar\b", lab, re.I):
        return True
    # nom collé vs libellé de dossier
    if " " not in lab and " " in (new_label or "") and len(new_label) >= 8:
        return True
    return False


def common_file_prefix(db_path=DEFAULT_DB):
    """Plus long préfixe commun des chemins original/solution (coupé au dossier)."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT original_file, solution_file FROM solutions").fetchall()
    conn.close()
    paths = []
    for r in rows:
        for p in (r["original_file"], r["solution_file"]):
            if p:
                paths.append(p.replace("/", "\\"))
    if not paths:
        return ""
    prefix = paths[0]
    for p in paths[1:]:
        while prefix and not p.lower().startswith(prefix.lower()):
            prefix = prefix[:-1]
        if not prefix:
            break
    cut = max(prefix.rfind("\\"), prefix.rfind("/"))
    return prefix[:cut] if cut > 2 else prefix.rstrip("\\/")


def _swap_prefix(path, old_prefix, new_root):
    if not path:
        return path
    raw = path.replace("/", "\\")
    old = (old_prefix or "").replace("/", "\\").rstrip("\\")
    if not old or not raw.lower().startswith(old.lower()):
        return path
    rest = raw[len(old):].replace("\\", os.sep).replace("/", os.sep)
    return new_root.rstrip("\\/") + rest


def remap_roots(db_path=DEFAULT_DB, new_root="", apply=False):
    """Réécrit D:\\OneDrive…\\CARTOS vers un dossier local. dry-run par défaut."""
    new_root = (new_root or "").strip().strip('"').strip("'")
    old = common_file_prefix(db_path)
    if not new_root:
        return {"ok": False, "error": "Indique le dossier CARTOS de cette machine.",
                "old_prefix": old}
    if apply and not os.path.isdir(new_root):
        return {"ok": False, "error": f"Dossier introuvable : {new_root}",
                "old_prefix": old}
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, original_file, solution_file FROM solutions").fetchall()
    found_ori = found_sol = 0
    n_ori = n_sol = 0
    updates = []
    for r in rows:
        no = _swap_prefix(r["original_file"], old, new_root)
        ns = _swap_prefix(r["solution_file"], old, new_root)
        if r["original_file"]:
            n_ori += 1
            if os.path.isfile(no):
                found_ori += 1
        if r["solution_file"]:
            n_sol += 1
            if os.path.isfile(ns):
                found_sol += 1
        if apply and (no != (r["original_file"] or "") or ns != (r["solution_file"] or "")):
            updates.append((no, ns, r["id"]))
    if apply and updates:
        conn.close()
        backup_db(db_path)
        conn = _connect(db_path)
        conn.executemany(
            "UPDATE solutions SET original_file=?, solution_file=? WHERE id=?",
            updates)
        conn.commit()
    conn.close()
    return {
        "ok": True,
        "old_prefix": old,
        "new_root": new_root,
        "applied": bool(apply),
        "updated": len(updates) if apply else 0,
        "originals": n_ori, "originals_found": found_ori,
        "solutions": n_sol, "solutions_found": found_sol,
        "total": len(rows),
    }


def find_duplicates(db_path=DEFAULT_DB):
    """Groupes de fiches au binaire stock identique ET même type de prestation.
    Stage 1 + FAP off sur le même original ne sont PAS des doublons."""
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT id, vehicle_label, solution_type, ecu_platform, manufacturer,
                  tested_status, stock_size, created_at, stock_sha256
           FROM solutions
           WHERE stock_sha256 IS NOT NULL AND stock_sha256!=''
           ORDER BY stock_sha256, created_at"""
    ).fetchall()
    conn.close()
    buckets = {}
    for r in rows:
        d = dict(r)
        key = (d["stock_sha256"], _norm_type(d.get("solution_type")))
        buckets.setdefault(key, []).append(d)
    groups = []
    for (sha, _t), items in buckets.items():
        if len(items) > 1:
            groups.append({"sha256": sha, "rows": items})
    return groups


def bulk_update(db_path=DEFAULT_DB, ids=None, set_status=None, add_tags=None):
    """Applique un statut et/ou ajoute des étiquettes à plusieurs fiches."""
    ids = ids or []
    add = [t.strip() for t in (add_tags or "").split(",") if t.strip()]
    conn = _connect(db_path)
    n = 0
    for sid in ids:
        row = conn.execute("SELECT tags FROM solutions WHERE id=?", (sid,)).fetchone()
        if not row:
            continue
        sets, vals = [], []
        if set_status:
            sets.append("tested_status=?"); vals.append(set_status)
        if add:
            cur = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
            for t in add:
                if t not in cur:
                    cur.append(t)
            sets.append("tags=?"); vals.append(", ".join(cur))
        if sets:
            vals.append(sid)
            conn.execute(f"UPDATE solutions SET {', '.join(sets)} WHERE id=?", vals)
            n += 1
    conn.commit()
    conn.close()
    return n


def list_backups(db_path=DEFAULT_DB):
    """Liste les sauvegardes (nom, taille, date, nombre de fiches)."""
    bdir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    if not os.path.isdir(bdir):
        return []
    out = []
    for f in os.listdir(bdir):
        if not (f.startswith("solutions-") and f.endswith(".db")):
            continue
        full = os.path.join(bdir, f)
        try:
            n = count(full)
        except Exception:
            n = None
        out.append({"name": f, "size": os.path.getsize(full),
                    "mtime": os.path.getmtime(full), "count": n})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def restore_backup(db_path=DEFAULT_DB, name=""):
    """Restaure une sauvegarde (sauvegarde d'abord la base courante)."""
    name = os.path.basename(name or "")   # anti-traversal
    if not (name.startswith("solutions-") and name.endswith(".db")):
        return {"ok": False, "error": "Nom de sauvegarde invalide."}
    bdir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    src = os.path.join(bdir, name)
    if not os.path.isfile(src):
        return {"ok": False, "error": "Sauvegarde introuvable."}
    return import_db_file(db_path, src)


def _looks_like_carto_db(path):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "solutions" not in names:
            conn.close()
            return False, "Pas une base Carto Matcher (table solutions absente)."
        n = conn.execute("SELECT COUNT(*) FROM solutions").fetchone()[0]
        conn.close()
        return True, n
    except sqlite3.Error as e:
        return False, f"Fichier SQLite illisible ({e})."


def import_db_file(db_path, src_path):
    """Remplace la base courante par un fichier .db (sauvegarde d'abord)."""
    if not src_path or not os.path.isfile(src_path):
        return {"ok": False, "error": "Fichier introuvable."}
    if os.path.getsize(src_path) < 1024:
        return {"ok": False, "error": "Fichier trop petit pour une base."}
    ok, info = _looks_like_carto_db(src_path)
    if not ok:
        return {"ok": False, "error": info}
    n_src = info
    if n_src == 0:
        return {"ok": False, "error": "Cette base ne contient aucune fiche."}
    backup_db(db_path)
    dest = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".importing"
    shutil.copy2(src_path, tmp)
    # WAL/SHM de l'ancienne base : ils feraient relire l'ancien journal
    for s in ("-wal", "-shm"):
        p = dest + s
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    os.replace(tmp, dest)
    init_db(dest)
    return {"ok": True, "count": count(dest)}
