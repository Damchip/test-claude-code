"""
Traitement par lot : un dossier de fichiers clients -> pour chacun, on cherche
la meilleure solution connue, on calcule les zones de différence (même logique
que l'onglet Auto-patch), et on génère le fichier patché SI le verdict est
« propre » (le fichier client est identique au stock connu en dehors des zones
modifiées). Les fichiers ambigus sont signalés dans le rapport pour un
traitement manuel dans l'onglet Auto-patch — on ne patche jamais à l'aveugle.

Rien ne touche jamais aux fichiers sources. Les fichiers patchés sont écrits
dans un sous-dossier séparé (_traite par défaut).

`iter_batch` est un générateur qui émet des évènements de progression, sur le
même modèle que `importer.iter_import` :
    {"event":"start","total":N}
    {"event":"item","index":i,"total":N,"entry":{...}}
    {"event":"done","counts":{...},"report":[...]}
"""
import os
import time

from . import db, engine, patch as pmod
from .patch import diff_regions, evaluate_patch, apply_patch  # réexport

DEFAULT_EXT = {".bin", ".ori", ".mpc", ".dec", ".mod", ".ecu",
               ".full", ".read", ".kp", ""}

GAP = 16          # fusion des octets différents séparés de moins de 16 octets -> une seule zone
SEUIL_A_VERIFIER = 0.6


def _collect_files(root, exts, min_size, max_size):
    files = []
    for dirpath, _, names in os.walk(root):
        if os.path.basename(dirpath) == "_traite":
            continue  # ne jamais retraiter nos propres sorties
        for name in names:
            full = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if ext not in exts:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if min_size <= size <= max_size:
                files.append(full)
    return sorted(files)


def iter_batch(root, *, db_path=None, min_ko=16, max_mo=64, min_score=SEUIL_A_VERIFIER,
                apply_clean=True, out_dir=None, exts=None):
    exts = exts or DEFAULT_EXT
    min_size, max_size = min_ko * 1024, max_mo * 1024 * 1024
    dbp = db_path or db.DEFAULT_DB
    out_dir = out_dir or os.path.join(root, "_traite")

    files = _collect_files(root, exts, min_size, max_size)
    total = len(files)
    yield {"event": "start", "total": total}

    counts = {"patche": 0, "a_verifier": 0, "incompatible": 0, "sans_solution": 0, "erreur": 0}
    report = []

    for i, path in enumerate(files):
        rel = os.path.relpath(path, root)
        entry = {"fichier": rel, "statut": None, "solution": None, "verdict": None,
                  "zones": 0, "octets_modifies": 0, "patch_genere": False, "detail": ""}
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            result = engine.match(data, dbp, path=rel)
            matches = result.get("matches", [])
            incoming = result.get("incoming", {})
            entry["plateforme"] = incoming.get("platform")
            entry["fabricant"] = incoming.get("manufacturer")

            best = matches[0] if matches else None
            if not best or best.get("score", 0) < min_score:
                entry["statut"] = "sans_solution"
                entry["detail"] = "Aucune solution connue pour ce fichier."
                counts["sans_solution"] += 1
                report.append(entry)
                yield {"event": "item", "index": i, "total": total, "entry": entry, "counts": dict(counts)}
                continue

            entry["solution"] = best.get("vehicle_label") or f"#{best.get('id')}"
            sol_row = db.get_solution(dbp, best["id"])
            orig_path = (sol_row or {}).get("original_file") or ""
            sol_path = (sol_row or {}).get("solution_file") or ""
            if not orig_path or not os.path.isfile(orig_path) or not sol_path or not os.path.isfile(sol_path):
                entry["statut"] = "a_verifier"
                entry["detail"] = "Fichiers original/solution de la fiche introuvables sur le disque " \
                                   "— à traiter manuellement dans Auto-patch."
                counts["a_verifier"] += 1
                report.append(entry)
                yield {"event": "item", "index": i, "total": total, "entry": entry, "counts": dict(counts)}
                continue

            with open(orig_path, "rb") as fh:
                orig = fh.read()
            with open(sol_path, "rb") as fh:
                sol = fh.read()

            ev = pmod.build_patched(
                data, orig, sol,
                fiche_type=best.get("solution_type") or "",
                platform=(sol_row or {}).get("ecu_platform") or "",
                partial=False,
            )
            if ev.get("error") and ev.get("verdict") != "propre":
                err = ev.get("error")
                if err == "zones_incompatibles":
                    entry["statut"] = "incompatible"
                    entry["detail"] = "Zones incompatibles — à traiter manuellement."
                    counts["incompatible"] += 1
                else:
                    entry["statut"] = "a_verifier"
                    entry["detail"] = {
                        "taille_incompatible_orig_sol": "Original/solution de tailles différentes.",
                        "taille_incompatible_client": "Le fichier client n'a pas la taille attendue (header programmeur ?).",
                        "solution_identique_original": "Solution identique à l'original (rien à patcher).",
                    }.get(err, ev.get("detail") or err or "")
                    counts["a_verifier"] += 1
                report.append(entry)
                yield {"event": "item", "index": i, "total": total, "entry": entry, "counts": dict(counts)}
                continue

            entry["verdict"] = ev["verdict"]
            entry["zones"] = len(ev.get("zones") or [])
            entry["octets_modifies"] = ev.get("changed_bytes") or 0
            ck = ev.get("checksum") or {}

            if ev["verdict"] == "propre" and apply_clean:
                patched = ev["patched"]
                name = os.path.basename(path)
                dot = name.rfind(".")
                base, ext = (name[:dot], name[dot:]) if dot > 0 else (name, ".bin")
                out_sub = os.path.join(out_dir, os.path.dirname(rel))
                os.makedirs(out_sub, exist_ok=True)
                out_path = os.path.join(out_sub, f"{base}_PATCHED{ext}")
                with open(out_path, "wb") as fh:
                    fh.write(patched)
                ck_note = ck.get("note") or "Vérifie les checksums avant de flasher."
                entry["statut"] = "patche"
                entry["patch_genere"] = True
                entry["sortie"] = os.path.relpath(out_path, root)
                entry["detail"] = f"{ev.get('applied', 0)} zone(s) appliquée(s). {ck_note}"
                counts["patche"] += 1
                db.add_job(dbp, client_name=rel, solution_id=best.get("id"),
                          solution_label=entry["solution"],
                          verdict="propre (lot) · " + (ck.get("status") or ""),
                          zones=len(ev.get("zones") or []), applied=1,
                          note="Traitement par lot")
            elif ev["verdict"] == "propre":
                entry["statut"] = "a_verifier"
                entry["detail"] = "Correspondance propre trouvée mais application automatique désactivée."
                counts["a_verifier"] += 1
            elif ev["verdict"] == "checksum_a_verifier":
                entry["statut"] = "a_verifier"
                entry["detail"] = f"Zones compatibles mais le fichier diffère du stock ailleurs " \
                                   f"({ev['diff_outside']} octets) — checksum à revoir manuellement."
                counts["a_verifier"] += 1
            else:
                entry["statut"] = "incompatible"
                entry["detail"] = f"{ev['mismatched']} zone(s) sur {len(ev['zones'])} ne correspondent pas " \
                                   f"au stock connu — ne pas patcher automatiquement."
                counts["incompatible"] += 1

        except Exception as e:
            entry["statut"] = "erreur"
            entry["detail"] = str(e)
            counts["erreur"] += 1

        report.append(entry)
        yield {"event": "item", "index": i, "total": total, "entry": entry, "counts": dict(counts)}

    yield {"event": "done", "counts": dict(counts), "report": report, "out_dir": out_dir}


def scan_batch(root, **kw):
    """Draine iter_batch et renvoie le résultat complet (usage script / test)."""
    result = {"counts": {}, "report": [], "out_dir": None}
    for ev in iter_batch(root, **kw):
        if ev["event"] == "done":
            result["counts"] = ev["counts"]
            result["report"] = ev["report"]
            result["out_dir"] = ev["out_dir"]
    return result
