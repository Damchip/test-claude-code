"""
Import complet d'une grande bibliothèque (dossier CARTOS entier, des dizaines de milliers de fichiers).

Tourne en tâche de fond sur le PC : l'import continue même si la page est fermée, et reprend là où il
s'était arrêté (PC éteint, outil relancé, bouton Arrêter).
  - les couples original / solution déjà en base sont reconnus par leur chemin, SANS relire le fichier ;
  - chaque couple traité est noté (empreinte courte du chemin) dans data/import_complet.json ;
  - aucun fichier n'est copié ni modifié (voir « Copier les fichiers dans l'application »).
L'état (progression, vitesse, temps restant, dernières erreurs) est lu par l'interface toutes les secondes.
"""
import hashlib
import json
import os
import threading
import time

from . import db, importer

ERREURS_MAX = 200


def _cle(original, solution):
    brut = os.path.normcase(os.path.abspath(original)) + "|" + (os.path.normcase(os.path.abspath(solution)) if solution else "")
    return hashlib.sha1(brut.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _chemins_connus(db_path):
    """Couples (original, solution) déjà en base, par chemin : un fichier déjà importé n'est jamais relu."""
    conn = db._connect(db_path)
    rows = conn.execute("SELECT original_file, solution_file FROM solutions").fetchall()
    conn.close()
    connus = set()
    for r in rows:
        ori = db.resolve_file(db_path, r["original_file"] or "")
        if ori:
            sol = db.resolve_file(db_path, r["solution_file"] or "")
            connus.add(_cle(ori, sol))
    return connus


class ImportComplet:
    def __init__(self, db_path, etat_path):
        self.db_path, self.etat_path = db_path, etat_path
        self._verrou = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.etat = self._lire()
        if self.etat.get("etat") in ("exploration", "import"):
            self.etat["etat"] = "interrompu"          # l'outil a été fermé pendant l'import : à reprendre

    # --- état persistant -------------------------------------------------------------
    def _lire(self):
        try:
            with open(self.etat_path, encoding="utf-8") as fh:
                e = json.load(fh)
            return e if isinstance(e, dict) else {}
        except (OSError, ValueError):
            return {}

    def _sauver(self):
        tmp = self.etat_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.etat, fh, ensure_ascii=False)
        os.replace(tmp, self.etat_path)

    def public(self):
        """État pour l'interface (sans la liste des couples déjà traités)."""
        with self._verrou:
            e = {k: v for k, v in self.etat.items() if k != "faits"}
        e["actif"] = bool(self._thread and self._thread.is_alive())
        try:
            e["base"] = db.count(self.db_path)
        except Exception:
            e["base"] = None
        return e

    # --- commandes ------------------------------------------------------------------
    def demarrer(self, root, status="a_confirmer", reprendre=True):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Un import est déjà en cours.")
        if not root or not os.path.isdir(root):
            raise RuntimeError(f"Dossier introuvable : {root}")
        meme = os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(self.etat.get("root") or ""))
        faits = self.etat.get("faits", []) if (reprendre and meme) else []
        self.etat = {"root": root, "status": status, "etat": "exploration", "debut": time.time(), "fin": None,
                     "total": 0, "index": 0, "traites": 0, "deja": 0, "reste_s": None, "vitesse": None,
                     "dossiers": 0, "message": "Exploration du dossier…", "erreurs": [], "faits": faits,
                     "counts": {"added": 0, "new": 0, "dup": 0, "empty": 0, "errors": 0}}
        self._stop.clear()
        self._thread = threading.Thread(target=self._executer, name="import-complet", daemon=True)
        self._thread.start()

    def arreter(self):
        self._stop.set()

    # --- travail ----------------------------------------------------------------------
    def _executer(self):
        e = self.etat
        root, dbp = e["root"], self.db_path
        try:
            db.init_db(dbp)
            dossiers = importer._collect_folders(root, importer.DEFAULT_EXT, 16 * 1024, 32 * 1024 * 1024,
                                                 skip_roots=[db.files_root(dbp)])
            jobs = []
            for dirpath, cands in dossiers:
                for original, solution in importer.iter_folder_jobs(cands):
                    jobs.append((dirpath, original, solution))
            connus = _chemins_connus(dbp)
            faits = set(e["faits"])
            cles = [_cle(o[0], s[0] if s else "") for _, o, s in jobs]
            a_faire = sum(1 for c in cles if c not in connus and c not in faits)
            with self._verrou:
                e.update(etat="import", total=len(jobs), dossiers=len(dossiers), a_faire=a_faire,
                         message=f"{len(jobs)} couple(s) original / solution dans {len(dossiers)} dossier(s)")
            analyzed, dossier_courant = {}, None
            t_travail, derniere_sauvegarde = 0.0, time.time()
            for i, (dirpath, original, solution) in enumerate(jobs, 1):
                if self._stop.is_set():
                    with self._verrou:
                        e.update(etat="arrete", message="Arrêté : relance « Importer » pour reprendre.")
                    break
                cle = cles[i - 1]
                if cle in connus or cle in faits:
                    with self._verrou:
                        e["index"], e["deja"] = i, e["deja"] + 1
                    continue
                if dirpath != dossier_courant:          # le cache ne sert qu'aux types d'un même dossier
                    analyzed, dossier_courant = {}, dirpath
                t0 = time.time()
                entry = importer.traiter_job(root, dirpath, original, solution, e["counts"], analyzed,
                                             dbp=dbp, status=e["status"])
                t_travail += time.time() - t0
                faits.add(cle)
                with self._verrou:
                    e["index"], e["traites"] = i, e["traites"] + 1
                    e["faits"].append(cle)
                    if entry.get("state") == "erreur":
                        e["erreurs"] = (e["erreurs"] + [{"dossier": entry.get("folder"), "erreur": entry.get("error")}])[-ERREURS_MAX:]
                    vitesse = e["traites"] / t_travail if t_travail else None
                    e["vitesse"] = round(vitesse, 2) if vitesse else None
                    e["reste_s"] = int(max(0, a_faire - e["traites"]) / vitesse) if vitesse else None
                    e["message"] = f"{entry.get('folder', '')}"[:200]
                if time.time() - derniere_sauvegarde > 5:
                    with self._verrou:
                        self._sauver()
                    derniere_sauvegarde = time.time()
            else:
                with self._verrou:
                    e.update(etat="termine", reste_s=0, message="Import terminé.")
        except Exception as ex:   # dossier devenu inaccessible, disque plein…
            with self._verrou:
                e.update(etat="erreur", message=f"Erreur : {ex}"[:300])
        finally:
            with self._verrou:
                e["fin"] = time.time()
                try:
                    self._sauver()
                except OSError:
                    pass
