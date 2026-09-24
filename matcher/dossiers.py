"""
Dossiers client / véhicule — 1.48.

Un dossier = un véhicule (immat / VIN) + le client, les prestations et le
dump. Les patchs (jobs) s'y rattachent. Rien ne sort de la machine.
"""
import json
import os
import re
import shutil
import time

from . import db as dbmod
from . import metadata

STATUSES = ("ouvert", "en_cours", "livre", "archive")
STATUS_LABEL = {
    "ouvert": "Ouvert",
    "en_cours": "En cours",
    "livre": "Livré",
    "archive": "Archivé",
}

FIELDS = (
    "client_name", "phone", "email", "company",
    "plate", "vin", "vehicle_label",
    "ecu_version", "ecu_platform", "manufacturer",
    "notes", "source", "inbox_file", "dump_file",
    "status", "solution_id",
)


def dossiers_root(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "dossiers")


def init(db_path):
    conn = dbmod._connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dossiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            updated_at REAL,
            status TEXT DEFAULT 'ouvert',
            client_name TEXT,
            phone TEXT,
            email TEXT,
            company TEXT,
            plate TEXT,
            vin TEXT,
            vehicle_label TEXT,
            ecu_version TEXT,
            ecu_platform TEXT,
            manufacturer TEXT,
            prestas TEXT,
            notes TEXT,
            source TEXT,
            inbox_file TEXT,
            dump_file TEXT,
            solution_id INTEGER
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dos_plate ON dossiers(plate)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dos_status ON dossiers(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dos_name ON dossiers(client_name)")
    jobs_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "dossier_id" not in jobs_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN dossier_id INTEGER")
    conn.commit()
    conn.close()


def normalize_plate(raw):
    """AA-123-BB (SIV) ou 1234 AB 31 (FNI). Sinon texte nettoyé."""
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if re.fullmatch(r"[A-Z]{2}\d{3}[A-Z]{2}", s):
        return f"{s[:2]}-{s[2:5]}-{s[5:]}"
    m = re.fullmatch(r"(\d{1,4})([A-Z]{1,3})(\d{2})", s)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    return (raw or "").strip().upper()


def normalize_vin(raw):
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def vin_ok(vin):
    return bool(re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin or ""))


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _prestas(val):
    if val is None:
        return []
    if isinstance(val, list):
        return metadata.ordered_atoms(val)
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return metadata.ordered_atoms(parsed)
        except (TypeError, ValueError):
            pass
        return metadata.ordered_atoms(metadata.type_atoms(val) or [val])
    return []


def _row(r):
    d = dict(r)
    d["prestas"] = _prestas(d.get("prestas"))
    d["status"] = d.get("status") or "ouvert"
    d["status_label"] = STATUS_LABEL.get(d["status"], d["status"])
    vin = d.get("vin") or ""
    d["vin_ok"] = (not vin) or vin_ok(vin)
    return d


def _hydrate_dump(db_path, d):
    stored = d.get("dump_file") or ""
    d["dump_file"] = dbmod.resolve_file(db_path, stored) if stored else ""
    d["dump_present"] = bool(d["dump_file"] and os.path.isfile(d["dump_file"]))
    return d


def create(db_path, **fields):
    now = time.time()
    prestas = _prestas(fields.get("prestas"))
    status = fields.get("status") or "ouvert"
    if status not in STATUSES:
        status = "ouvert"
    plate = normalize_plate(fields.get("plate") or "")
    vin = normalize_vin(fields.get("vin") or "")
    conn = dbmod._connect(db_path)
    cur = conn.execute(
        """INSERT INTO dossiers (
            created_at, updated_at, status, client_name, phone, email, company,
            plate, vin, vehicle_label, ecu_version, ecu_platform, manufacturer,
            prestas, notes, source, inbox_file, dump_file, solution_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now, now, status,
         (fields.get("client_name") or "").strip(),
         (fields.get("phone") or "").strip(),
         (fields.get("email") or "").strip(),
         (fields.get("company") or "").strip(),
         plate, vin,
         (fields.get("vehicle_label") or "").strip(),
         (fields.get("ecu_version") or "").strip(),
         (fields.get("ecu_platform") or "").strip(),
         (fields.get("manufacturer") or "").strip(),
         json.dumps(prestas, ensure_ascii=False),
         (fields.get("notes") or "").strip(),
         (fields.get("source") or "atelier").strip() or "atelier",
         (fields.get("inbox_file") or "").strip(),
         "",
         fields.get("solution_id")),
    )
    conn.commit()
    did = cur.lastrowid
    conn.close()
    return did


def get(db_path, dossier_id):
    conn = dbmod._connect(db_path)
    row = conn.execute("SELECT * FROM dossiers WHERE id=?", (dossier_id,)).fetchone()
    jobs = []
    if row:
        jobs = [dict(j) for j in conn.execute(
            "SELECT * FROM jobs WHERE dossier_id=? ORDER BY created_at DESC",
            (dossier_id,)).fetchall()]
    conn.close()
    if not row:
        return None
    d = _hydrate_dump(db_path, _row(row))
    d["jobs"] = jobs
    d["duplicates"] = suggest(db_path, plate=d.get("plate"), vin=d.get("vin"),
                              exclude_id=d["id"])
    return d


def update(db_path, dossier_id, **fields):
    if not dossier_id:
        return False
    sets, vals = ["updated_at=?"], [time.time()]
    if "prestas" in fields:
        sets.append("prestas=?")
        vals.append(json.dumps(_prestas(fields["prestas"]), ensure_ascii=False))
    if "plate" in fields:
        sets.append("plate=?")
        vals.append(normalize_plate(fields.get("plate") or ""))
    if "vin" in fields:
        sets.append("vin=?")
        vals.append(normalize_vin(fields.get("vin") or ""))
    if "status" in fields:
        st = fields.get("status") or "ouvert"
        if st not in STATUSES:
            st = "ouvert"
        sets.append("status=?")
        vals.append(st)
    skip = {"prestas", "plate", "vin", "status", "id", "created_at", "updated_at",
            "jobs", "duplicates", "dump_present", "status_label", "vin_ok"}
    for k in FIELDS:
        if k in skip or k not in fields:
            continue
        if k == "dump_file":
            continue
        sets.append(f"{k}=?")
        v = fields[k]
        vals.append(("" if v is None else str(v)).strip() if k != "solution_id" else v)
    if len(vals) == 1:
        return False
    vals.append(dossier_id)
    conn = dbmod._connect(db_path)
    conn.execute(f"UPDATE dossiers SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    n = conn.total_changes
    conn.close()
    return n > 0


def delete(db_path, dossier_id):
    folder = os.path.join(dossiers_root(db_path), f"{int(dossier_id):06d}")
    conn = dbmod._connect(db_path)
    conn.execute("UPDATE jobs SET dossier_id=NULL WHERE dossier_id=?", (dossier_id,))
    conn.execute("DELETE FROM dossiers WHERE id=?", (dossier_id,))
    conn.commit()
    conn.close()
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    return True


def list_dossiers(db_path, q="", status=""):
    conn = dbmod._connect(db_path)
    rows = conn.execute(
        "SELECT * FROM dossiers ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    job_counts = {}
    for r in conn.execute(
        "SELECT dossier_id, COUNT(*) n FROM jobs WHERE dossier_id IS NOT NULL GROUP BY dossier_id"
    ):
        job_counts[r["dossier_id"]] = r["n"]
    conn.close()
    out = []
    ql = (q or "").strip().lower()
    qdigits = _digits(q)
    qplate = normalize_plate(q).lower() if q else ""
    qvin = normalize_vin(q)
    for r in rows:
        d = _hydrate_dump(db_path, _row(r))
        d["jobs_n"] = job_counts.get(d["id"], 0)
        if status and d["status"] != status:
            continue
        if ql:
            blob = " ".join(str(d.get(k) or "") for k in (
                "client_name", "phone", "email", "company", "plate", "vin",
                "vehicle_label", "ecu_version", "ecu_platform", "notes",
                "inbox_file")).lower()
            hit = ql in blob or (qplate and qplate == (d.get("plate") or "").lower())
            if qdigits and qdigits in _digits(d.get("phone") or ""):
                hit = True
            if qvin and qvin == (d.get("vin") or ""):
                hit = True
            if not hit:
                continue
        out.append(d)
    return out


def counts(db_path):
    conn = dbmod._connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM dossiers").fetchone()[0]
    by = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) n FROM dossiers GROUP BY status"):
        by[r["status"] or "ouvert"] = r["n"]
    conn.close()
    return {"total": total, "by_status": by,
            "ouverts": by.get("ouvert", 0) + by.get("en_cours", 0)}


def suggest(db_path, plate="", vin="", name="", exclude_id=None):
    """Dossiers déjà ouverts pour la même plaque / VIN / nom."""
    plate = normalize_plate(plate or "")
    vin = normalize_vin(vin or "")
    name = (name or "").strip()
    if not plate and not vin and len(name) < 3:
        return []
    conn = dbmod._connect(db_path)
    rows = conn.execute("SELECT * FROM dossiers").fetchall()
    conn.close()
    hits = []
    nl = name.lower()
    for r in rows:
        if exclude_id and r["id"] == exclude_id:
            continue
        reasons = []
        if plate and (r["plate"] or "") == plate:
            reasons.append("immat")
        if vin and len(vin) >= 11 and (r["vin"] or "") == vin:
            reasons.append("VIN")
        if nl and len(nl) >= 3 and (r["client_name"] or "").lower() == nl:
            reasons.append("nom")
        if reasons:
            d = _row(r)
            d["why"] = reasons
            hits.append(d)
    hits.sort(key=lambda x: -(x.get("updated_at") or 0))
    return hits[:8]


def attach_job(db_path, job_id, dossier_id):
    conn = dbmod._connect(db_path)
    conn.execute("UPDATE jobs SET dossier_id=? WHERE id=?", (dossier_id, job_id))
    conn.execute("UPDATE dossiers SET updated_at=? WHERE id=?", (time.time(), dossier_id))
    conn.commit()
    conn.close()
    bump_status(db_path, dossier_id, "en_cours")
    return True


def unattached_jobs(db_path):
    conn = dbmod._connect(db_path)
    rows = conn.execute(
        """SELECT * FROM jobs WHERE dossier_id IS NULL OR dossier_id=0
           ORDER BY created_at DESC LIMIT 50"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def bump_status(db_path, dossier_id, want="en_cours"):
    """ouvert → en_cours (ne recule pas un dossier déjà livré / archivé)."""
    d = get(db_path, dossier_id)
    if not d:
        return
    cur = d.get("status") or "ouvert"
    if want == "en_cours" and cur == "ouvert":
        update(db_path, dossier_id, status="en_cours")


def add_prestas(db_path, dossier_id, extra):
    d = get(db_path, dossier_id)
    if not d:
        return []
    merged = metadata.ordered_atoms(list(d.get("prestas") or []) + _prestas(extra))
    update(db_path, dossier_id, prestas=merged)
    return merged


def save_dump(db_path, dossier_id, data, filename="client.bin"):
    if not data or not dossier_id:
        return ""
    dest_dir = os.path.join(dossiers_root(db_path), f"{int(dossier_id):06d}")
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(filename or "")[1] or ".bin"
    if ext.lower() not in (".bin", ".ori", ".mod", ".hex", ".ecu"):
        ext = ".bin"
    target = os.path.join(dest_dir, "client" + ext)
    with open(target, "wb") as fh:
        fh.write(data)
    rel = dbmod._store_path(db_path, target)
    conn = dbmod._connect(db_path)
    conn.execute(
        "UPDATE dossiers SET dump_file=?, updated_at=? WHERE id=?",
        (rel, time.time(), dossier_id))
    conn.commit()
    conn.close()
    return target


def from_inbox(db_path, rec, dump_bytes=None):
    """Crée un dossier à partir d'une demande portail (contact + fichier)."""
    rec = rec or {}
    contact = rec.get("contact") or {}
    prestas = rec.get("prestas") or []
    did = create(
        db_path,
        client_name=contact.get("nom") or contact.get("name") or "",
        phone=contact.get("tel") or "",
        email=contact.get("email") or "",
        plate=contact.get("immat") or contact.get("plate") or "",
        vin=contact.get("vin") or "",
        vehicle_label=contact.get("vehicule") or "",
        ecu_version=rec.get("ecu_version") or "",
        ecu_platform=rec.get("plateforme") or rec.get("platform") or "",
        manufacturer=rec.get("fabricant") or rec.get("manufacturer") or "",
        prestas=prestas,
        notes=(rec.get("notes") or "").strip(),
        source="portail",
        inbox_file=rec.get("fichier") or "",
        status="ouvert",
    )
    if dump_bytes:
        save_dump(db_path, did, dump_bytes, rec.get("fichier") or "client.bin")
    return did
