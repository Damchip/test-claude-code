"""
Import par dossier, partagé CLI + interface, avec progression en flux.

Un dossier = un véhicule / un original :
  - ORIGINAL (clé)      = le .ori s'il existe, sinon le fichier le plus ancien
  - SOLUTIONS           = un fichier par TYPE de prestation détecté
                          (Stage 1, FAP off, EGR off, E85…). Si plusieurs fichiers
                          ont le même type, on garde la révision SAV la plus élevée.

Un même original peut donc produire plusieurs fiches. Le doublon n'est plus
« même sha256 » mais « même sha256 + même type ».

`iter_import` est un générateur qui émet des évènements de progression :
  {"event":"start","total":N}
  {"event":"item","index":i,"total":N,"entry":{...},"counts":{...}}
  {"event":"done","counts":{...},"db_size":N}

`scan_folder` draine ce générateur et renvoie le résultat complet d'un coup
(utilisé par le script en ligne de commande).
"""

import os
import re

from . import db, engine, metadata

DEFAULT_EXT = {".bin", ".ori", ".mpc", ".dec", ".mod", ".ecu",
               ".full", ".read", ".kp", ""}

# Marqueurs dans les noms (plus fiables que les dates, surtout via OneDrive
# qui réécrit souvent la date de modification à la synchro).
_ORI_RE = re.compile(r"(?<![a-z0-9])(ori|origin\w*|stock|virgin)(?![a-z0-9])", re.I)
_SAV_RE = re.compile(r"sav[\s_\-]*?(\d+)", re.I)


def _basename_low(path):
    return os.path.basename(path).lower()


def _is_original_name(full, ext):
    return ext == ".ori" or bool(_ORI_RE.search(_basename_low(full)))


def _sav_number(full):
    m = _SAV_RE.search(_basename_low(full))
    return int(m.group(1)) if m else -1


def pick_original_and_solution(candidates):
    """candidates = [(full, mtime, ext)] -> (original, solution|None).

    ORIGINAL = fichier marqué ORI / .ori (sinon le plus ancien).
    SOLUTION = la révision SAV la plus élevée, sinon le fichier le plus récent.
    Conservé pour compatibilité (CLI / tests) ; l'import réel passe par
    iter_folder_jobs().
    """
    originals = [c for c in candidates if _is_original_name(c[0], c[2])]
    if originals:
        original = min(originals, key=lambda c: c[1])
    else:
        original = min(candidates, key=lambda c: c[1])
    others = [c for c in candidates if c[0] != original[0]]
    if not others:
        return original, None
    solution = max(others, key=lambda c: (_sav_number(c[0]), c[1]))
    return original, solution


def iter_folder_jobs(candidates, type_override=None):
    """Yield (original, solution_or_None) pour chaque type distinct du dossier.

    - un original unique (ORI / plus ancien)
    - une solution par type de prestation détecté dans le nom
    - si --type est imposé, une seule solution (SAV le plus haut), comme avant
    """
    original, _fallback = pick_original_and_solution(candidates)
    others = [c for c in candidates if c[0] != original[0]]
    if not others:
        yield original, None
        return
    if type_override:
        best = max(others, key=lambda c: (_sav_number(c[0]), c[1]))
        yield original, best
        return
    groups = {}
    for c in others:
        st = (metadata.parse(c[0]).get("solution_type") or "").strip()
        groups.setdefault(st.casefold(), []).append(c)
    for lst in groups.values():
        best = max(lst, key=lambda c: (_sav_number(c[0]), c[1]))
        yield original, best


def _collect_folders(root, exts, min_size, max_size, since_ts=None, skip_roots=None):
    folders = []
    skip = [os.path.abspath(p) for p in (skip_roots or []) if p]
    for dirpath, dirnames, files in os.walk(root):
        ap = os.path.abspath(dirpath)
        if any(ap == s or ap.startswith(s + os.sep) for s in skip):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if d not in {".cache", "__pycache__", ".git", ".tmp"}
                       and not d.startswith(".")]
        if os.path.basename(dirpath) in {".cache", "__pycache__"}:
            continue
        cands = []
        for fname in files:
            full = os.path.join(dirpath, fname)
            if os.path.splitext(fname)[1].lower() not in exts:
                continue
            try:
                size = os.path.getsize(full)
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if min_size <= size <= max_size:
                cands.append((full, mtime, os.path.splitext(fname)[1].lower()))
        if cands:
            if since_ts is not None and max(c[1] for c in cands) <= since_ts:
                continue
            folders.append((dirpath, cands))
    return folders


def iter_import(root, *, status="a_confirmer", type_override=None, exts=None,
                min_ko=16, max_mo=32, dry_run=False, db_path=None, since_ts=None):
    exts = exts or DEFAULT_EXT
    min_size, max_size = min_ko * 1024, max_mo * 1024 * 1024
    dbp = db_path or db.DEFAULT_DB
    db.init_db(dbp)

    folders = _collect_folders(
        root, exts, min_size, max_size, since_ts,
        skip_roots=[db.files_root(dbp)],
    )
    jobs = []
    for dirpath, cands in folders:
        for original, solution in iter_folder_jobs(cands, type_override=type_override):
            jobs.append((dirpath, original, solution))
    total = len(jobs)
    counts = {"added": 0, "new": 0, "dup": 0, "empty": 0, "errors": 0}
    yield {"event": "start", "total": total}

    # cache analyse de l'original (souvent partagé par N types du même dossier)
    analyzed = {}

    for i, (dirpath, original, solution) in enumerate(jobs, 1):
        folder_rel = os.path.relpath(dirpath, root)
        try:
            ori_path = original[0]
            if ori_path not in analyzed:
                with open(ori_path, "rb") as fh:
                    data = fh.read()
                rel = os.path.relpath(ori_path, root)
                analyzed[ori_path] = engine.analyze(data, rel, with_minhash=not dry_run)
            info = analyzed[ori_path]
            nm = info["name_meta"]
            vehicle = metadata.compose_label(nm.get("brand"), nm.get("vehicle"))
            sol_path = solution[0] if solution else ""
            sol_meta = metadata.parse(os.path.relpath(sol_path, root)) if sol_path else {}
            stype = (type_override or sol_meta.get("solution_type")
                     or nm.get("solution_type") or "")

            entry = {
                "folder": folder_rel,
                "original": os.path.basename(ori_path),
                "solution": os.path.basename(sol_path) if sol_path else "",
                "solution_path": sol_path,
                "vehicle": vehicle,
                "platform": info["platform"] or "",
                "ecu": info["best_ecu_version"] or "",
                "type": stype,
                "state": "nouveau",
            }

            if (db.sha256_type_exists(dbp, info["sha256"], stype)
                    or (info.get("sha256_body")
                        and info["sha256_body"] != info["sha256"]
                        and db.sha256_type_exists(dbp, info["sha256_body"], stype))):
                entry["state"] = "doublon"
                counts["dup"] += 1
            else:
                counts["new"] += 1
                if not dry_run:
                    db.add_solution(
                        dbp,
                        ecu_version=info["best_ecu_version"] or "",
                        ecu_platform=info["platform"] or "",
                        manufacturer=info["manufacturer"] or "",
                        vehicle_label=vehicle, solution_type=stype,
                        tested_status=status,
                        stock_sha256=info.get("sha256_body") or info["sha256"],
                        stock_size=info.get("body_size") or info["size"],
                        minhash=info["minhash"], minhash_ver=2,
                        solution_file=sol_path,
                        original_file=ori_path,
                        notes=f"original: {entry['original']} | solution: {entry['solution'] or '(aucun)'}",
                    )
                    counts["added"] += 1
                    entry["state"] = "ajouté"
        except Exception as e:
            counts["errors"] += 1
            entry = {"folder": folder_rel, "state": "erreur", "error": str(e)}

        yield {"event": "item", "index": i, "total": total,
               "entry": entry, "counts": dict(counts)}

    yield {"event": "done", "counts": dict(counts), "db_size": db.count(dbp)}


def scan_folder(root, *, progress=None, **kwargs):
    """Version synchrone : draine iter_import et renvoie le résultat complet."""
    entries, final = [], None
    for ev in iter_import(root, **kwargs):
        if ev["event"] == "item":
            entries.append(ev["entry"])
            if progress:
                progress(ev["entry"])
        elif ev["event"] == "done":
            final = ev
    return {"ok": True, "root": root, "entries": entries,
            "counts": final["counts"], "db_size": final["db_size"]}
