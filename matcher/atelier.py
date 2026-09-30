"""File atelier : archive BIN, empreintes v2, versions ECU, nettoyage.

Conçu pour reprendre si ça s'arrête (lot de N fiches). Sans fichier sur le
disque, tout est ignoré — pointer CARTOS d'abord.
"""

import hashlib
import os
import json
import re

from . import db, extract, fingerprint, headers, importer, metadata

JUNK_LABELS = {".cache", "backup merdasse", ".search cache", ".search"}


def ecu_empty(v):
    s = (v or "").strip()
    return (not s) or s in {"0", "0000000000", "FFFFFFFF", "ffffffff"}


def _has_archive(db_path, sol_id):
    folder = os.path.join(db.files_root(db_path), f"{int(sol_id):06d}")
    if not os.path.isdir(folder):
        return False
    try:
        return any(n.startswith("original") or n.startswith("solution")
                   for n in os.listdir(folder))
    except OSError:
        return False


def _is_junk_row(d):
    lab = (d.get("vehicle_label") or "").strip().lower()
    if lab in JUNK_LABELS:
        return True
    tags = (d.get("tags") or "").lower()
    if "suspect" in {t.strip() for t in tags.split(",")}:
        return True
    blob = ((d.get("original_file") or "") + " " + (d.get("solution_file") or "")).lower()
    return ".cache" in blob or "\\.search" in blob.replace("/", "\\") or "/.search" in blob.replace("\\", "/")


def status(db_path=None):
    db_path = db_path or db.DEFAULT_DB
    rows = db.all_solutions(db_path)
    fp2 = sum(1 for r in rows if int(r.get("minhash_ver") or 1) >= 2)
    archived = sum(1 for r in rows if _has_archive(db_path, r["id"]))
    ecu = sum(1 for r in rows if not ecu_empty(r.get("ecu_version")))
    junk = [r for r in rows if _is_junk_row(r)]
    no_sol = sum(1 for r in rows if not (r.get("solution_file") or "").strip())
    dupes = db.find_duplicates(db_path)
    pending = 0
    missing = 0
    for r in rows:
        ori = db.resolve_file(db_path, r.get("original_file") or "")
        sol = db.resolve_file(db_path, r.get("solution_file") or "")
        has = os.path.isfile(ori) or os.path.isfile(sol)
        if not has:
            missing += 1
            continue
        need = (
            not _has_archive(db_path, r["id"])
            or int(r.get("minhash_ver") or 1) < 2
            or ecu_empty(r.get("ecu_version"))
            or not (r.get("ecu_platform") or "").strip()
        )
        if need:
            pending += 1
    return {
        "total": len(rows),
        "fp_v2": fp2,
        "fp_v1": len(rows) - fp2,
        "archived": archived,
        "ecu": ecu,
        "ecu_empty": len(rows) - ecu,
        "junk": len(junk),
        "dup_groups": len(dupes),
        "dup_extra": sum(max(0, len(g["rows"]) - 1) for g in dupes),
        "no_solution": no_sol,
        "pending": pending,
        "missing_files": missing,
    }


def _needs_sync(db_path, d):
    ori = db.resolve_file(db_path, d.get("original_file") or "")
    sol = db.resolve_file(db_path, d.get("solution_file") or "")
    has = os.path.isfile(ori) or os.path.isfile(sol)
    if not has:
        return False, ori, sol
    need = (
        (db.copie_active(db_path) and not _has_archive(db_path, d["id"]))
        or int(d.get("minhash_ver") or 1) < 2
        or ecu_empty(d.get("ecu_version"))
        or not (d.get("ecu_platform") or "").strip()
    )
    return need, ori, sol


def sync_batch(db_path=None, limit=12):
    """Un lot : archive + MinHash v2 + version ECU / plateforme si vides."""
    db_path = db_path or db.DEFAULT_DB
    conn = db._connect(db_path)
    ids = [r["id"] for r in conn.execute("SELECT id FROM solutions ORDER BY id")]
    conn.close()
    stats = {
        "archived": 0, "fingerprints": 0, "ecu": 0, "platforms": 0,
        "manufacturers": 0, "skipped": 0, "missing": 0, "errors": 0,
        "processed": 0, "remaining": 0,
    }
    remaining = 0
    processed = 0
    for sid in ids:
        conn = db._connect(db_path)
        row = conn.execute("SELECT * FROM solutions WHERE id=?", (sid,)).fetchone()
        conn.close()
        if not row:
            continue
        d = dict(row)
        need, ori, sol = _needs_sync(db_path, d)
        if not (os.path.isfile(ori) or os.path.isfile(sol)):
            stats["missing"] += 1
            continue
        if not need:
            stats["skipped"] += 1
            continue
        remaining += 1
        if processed >= limit:
            continue
        processed += 1
        try:
            if db.copie_active(db_path) and not _has_archive(db_path, d["id"]):
                db.archive_files(db_path, d["id"], ori, sol)
                stats["archived"] += 1
                ori = db.resolve_file(db_path, ori)
                sol = db.resolve_file(db_path, sol)
            src = ori if os.path.isfile(ori) else sol
            with open(src, "rb") as fh:
                data = fh.read()
            body = headers.detect(data)["body"]
            sets, vals = [], []
            if int(d.get("minhash_ver") or 1) < 2:
                mh = fingerprint.minhash_signature(body, skip_padding=True)
                sha = fingerprint.sha256(body)
                sets += ["minhash=?", "minhash_ver=2", "stock_sha256=?", "stock_size=?"]
                vals += [json.dumps(mh), sha, len(body)]
                stats["fingerprints"] += 1
            info = extract.extract(body)
            if ecu_empty(d.get("ecu_version")):
                ver = extract.pick_ecu_version(info.get("typed_candidates") or [])
                if ver:
                    sets.append("ecu_version=?"); vals.append(ver)
                    stats["ecu"] += 1
            if not (d.get("ecu_platform") or "").strip():
                plat = metadata.normalize_platform(info.get("platform"))
                if plat:
                    sets.append("ecu_platform=?"); vals.append(plat)
                    stats["platforms"] += 1
            if not (d.get("manufacturer") or "").strip() and info.get("manufacturer"):
                sets.append("manufacturer=?"); vals.append(info["manufacturer"])
                stats["manufacturers"] += 1
            if sets:
                vals.append(d["id"])
                conn = db._connect(db_path)
                conn.execute(
                    f"UPDATE solutions SET {', '.join(sets)} WHERE id=?", vals)
                conn.commit()
                conn.close()
        except Exception:
            stats["errors"] += 1
    stats["processed"] = processed
    stats["remaining"] = remaining - processed
    return stats


def purge_junk(db_path=None, apply=False):
    db_path = db_path or db.DEFAULT_DB
    conn = db._connect(db_path)
    rows = [dict(r) for r in conn.execute("SELECT * FROM solutions").fetchall()]
    conn.close()
    junk = [r for r in rows if _is_junk_row(r)]
    deleted = 0
    if apply:
        for r in junk:
            db.delete_solution(db_path, r["id"])
            deleted += 1
    samples = [(r.get("vehicle_label") or r.get("original_file") or "")[:60]
               for r in junk[:8]]
    return {"count": len(junk), "deleted": deleted, "applied": apply,
            "samples": samples}


def _keeper_score(d):
    s = 0
    if (d.get("solution_file") or "").strip():
        s += 10
    if (d.get("original_file") or "").strip():
        s += 5
    if not ecu_empty(d.get("ecu_version")):
        s += 3
    if (d.get("vehicle_label") or "").strip():
        s += 2
    if (d.get("ecu_platform") or "").strip():
        s += 1
    return (s, -int(d.get("id") or 0))


def merge_duplicates(db_path=None, apply=False):
    """Garde la fiche la plus complète d'un groupe (même sha + même type)."""
    db_path = db_path or db.DEFAULT_DB
    groups = db.find_duplicates(db_path)
    removed = 0
    kept = 0
    for g in groups:
        rows = list(g["rows"])
        rows.sort(key=_keeper_score, reverse=True)
        keeper = rows[0]
        kept += 1
        others = rows[1:]
        if not apply:
            removed += len(others)
            continue
        conn = db._connect(db_path)
        krow = conn.execute("SELECT * FROM solutions WHERE id=?",
                            (keeper["id"],)).fetchone()
        k = dict(krow) if krow else {}
        conn.close()
        for other in others:
            conn = db._connect(db_path)
            orow = conn.execute("SELECT * FROM solutions WHERE id=?",
                                (other["id"],)).fetchone()
            o = dict(orow) if orow else {}
            sets, vals = [], []
            for field in ("solution_file", "original_file", "ecu_version",
                          "ecu_platform", "manufacturer", "notes", "ecu_hw"):
                if not (k.get(field) or "").strip() and (o.get(field) or "").strip():
                    sets.append(f"{field}=?")
                    vals.append(o[field])
                    k[field] = o[field]
            if sets:
                vals.append(keeper["id"])
                conn.execute(
                    f"UPDATE solutions SET {', '.join(sets)} WHERE id=?", vals)
                conn.commit()
            conn.close()
            db.delete_solution(db_path, other["id"])
            removed += 1
    return {"groups": len(groups), "kept": kept, "removed": removed,
            "applied": apply}


def link_missing_solutions(db_path=None, apply=False):
    """Si le dossier de l'original contient un autre BIN, c'est la solution."""
    db_path = db_path or db.DEFAULT_DB
    conn = db._connect(db_path)
    rows = conn.execute(
        """SELECT id, original_file, solution_file FROM solutions
           WHERE (solution_file IS NULL OR trim(solution_file)='')
             AND original_file IS NOT NULL AND original_file!=''"""
    ).fetchall()
    conn.close()
    linked = 0
    scanned = 0
    for r in rows:
        ori = db.resolve_file(db_path, r["original_file"] or "")
        folder = os.path.dirname(ori) if ori else ""
        if not folder or not os.path.isdir(folder):
            continue
        scanned += 1
        cands = []
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in importer.DEFAULT_EXT:
                continue
            low = name.lower()
            if low.startswith("."):
                continue
            cands.append((full, os.path.getmtime(full), ext))
        if len(cands) < 2:
            continue
        orig, sol = importer.pick_original_and_solution(cands)
        if not sol:
            continue
        new_ori, new_sol = orig[0], sol[0]
        # si la fiche pointe déjà vers le fichier « solution », on inverse
        cur = os.path.normcase(os.path.abspath(ori)) if os.path.isfile(ori) else ""
        if cur and os.path.normcase(os.path.abspath(new_sol)) == cur:
            new_ori, new_sol = new_sol, new_ori
        if apply:
            conn = db._connect(db_path)
            conn.execute(
                "UPDATE solutions SET original_file=?, solution_file=? WHERE id=?",
                (new_ori, new_sol, r["id"]))
            conn.commit()
            conn.close()
            db.archive_files(db_path, r["id"], new_ori, new_sol)
        linked += 1
    return {"scanned": scanned, "linked": linked, "applied": apply,
            "empty": len(rows)}


def cleanup(db_path=None, apply=False):
    """Suspects + doublons + solutions manquantes. dry-run par défaut."""
    db_path = db_path or db.DEFAULT_DB
    if apply:
        db.backup_db(db_path)
    junk = purge_junk(db_path, apply=apply)
    dupes = merge_duplicates(db_path, apply=apply)
    linked = link_missing_solutions(db_path, apply=apply)
    return {"junk": junk, "duplicates": dupes, "linked": linked, "applied": apply}


_NOMS_NOTES = re.compile(r"original:\s*(.*?)\s*\|\s*solution:\s*(.*?)\s*$")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for bloc in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


def _dans(path, dossier):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(dossier)]) == os.path.abspath(dossier)
    except ValueError:
        return False


def liberer_espace(db_path=None, racine="", apply=False, max_candidats=25):
    """Supprime les copies de data/files/ quand le fichier d'origine est retrouvé dans `racine` (dossier CARTOS) :
    même contenu exact (SHA-256). La fiche pointe alors vers le fichier d'origine. Une copie sans original
    retrouvé est gardée. Aperçu par défaut ; `apply=True` écrit (base sauvegardée d'abord)."""
    db_path = db_path or db.DEFAULT_DB
    if not racine or not os.path.isdir(racine):
        return {"ok": False, "error": "Dossier CARTOS introuvable sur ce PC."}
    copies_dir = db.files_root(db_path)
    conn = db._connect(db_path)
    rows = [dict(r) for r in conn.execute("SELECT id, original_file, solution_file, notes FROM solutions")]
    conn.close()
    besoins = []
    for r in rows:
        m = _NOMS_NOTES.search(r.get("notes") or "")
        noms = {"original_file": m.group(1) if m else "", "solution_file": m.group(2) if m else ""}
        for champ in ("original_file", "solution_file"):
            p = db.resolve_file(db_path, r.get(champ) or "")
            if p and os.path.isfile(p) and _dans(p, copies_dir):
                besoins.append({"id": r["id"], "champ": champ, "copie": p, "taille": os.path.getsize(p),
                                "ext": os.path.splitext(p)[1].lower(), "nom": (noms[champ] or "").lower()})
    res = {"ok": True, "applied": apply, "copies": len(besoins), "retrouves": 0, "gardees": 0, "octets": 0}
    if not besoins:
        return res
    tailles = {b["taille"] for b in besoins}
    exts = {b["ext"] for b in besoins}
    index = {}
    for base, dossiers, fichiers in os.walk(racine):
        dossiers[:] = [d for d in dossiers if not d.startswith(".")]
        if _dans(base, copies_dir):
            continue
        for f in fichiers:
            if os.path.splitext(f)[1].lower() not in exts:
                continue
            full = os.path.join(base, f)
            try:
                taille = os.path.getsize(full)
            except OSError:
                continue
            if taille in tailles:
                index.setdefault(taille, []).append(full)
    trouves = []
    for b in besoins:
        cands = [c for c in index.get(b["taille"], []) if os.path.splitext(c)[1].lower() == b["ext"]]
        cands.sort(key=lambda c: os.path.basename(c).lower() != b["nom"])   # même nom d'abord
        empreinte = None
        for c in cands[:max_candidats]:
            try:
                empreinte = empreinte or _sha256(b["copie"])
                if _sha256(c) == empreinte:
                    trouves.append((b, c))
                    break
            except OSError:
                continue
    res["retrouves"] = len(trouves)
    res["gardees"] = len(besoins) - len(trouves)
    res["octets"] = sum(b["taille"] for b, _ in trouves)
    if apply and trouves:
        db.backup_db(db_path)
        conn = db._connect(db_path)
        for b, c in trouves:
            conn.execute(f"UPDATE solutions SET {b['champ']} = ? WHERE id = ?", (os.path.abspath(c), b["id"]))
        conn.commit()
        conn.close()
        for b, _ in trouves:
            try:
                os.remove(b["copie"])
            except OSError:
                pass
        for base, _, _ in sorted(os.walk(copies_dir), key=lambda x: -len(x[0])):
            if base != copies_dir:
                try:
                    os.rmdir(base)          # dossiers de fiches vidés
                except OSError:
                    pass
    return res
