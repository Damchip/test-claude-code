"""Tests v1.62 — pas de double des fichiers : liens directs vers OneDrive, libération des copies existantes."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import atelier, db


class CopiesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.data = os.path.join(t, "data")
        os.makedirs(self.data)
        self.dbp = os.path.join(self.data, "solutions.db")
        db.init_db(self.dbp)
        self.cartos = os.path.join(t, "OneDrive", "CARTOS", "Citroen C4")
        os.makedirs(self.cartos)
        self.ori = os.path.join(self.cartos, "ORIGINE C4.mpc")
        self.sol = os.path.join(self.cartos, "E85France C4.mpc")
        with open(self.ori, "wb") as fh:
            fh.write(b"\x01" * 4096)
        with open(self.sol, "wb") as fh:
            fh.write(b"\x02" * 4096)
        # fichier de même taille ailleurs : ne doit pas être pris pour l'original
        autre = os.path.join(t, "OneDrive", "CARTOS", "Autre")
        os.makedirs(autre)
        with open(os.path.join(autre, "ORIGINE C4.mpc"), "wb") as fh:
            fh.write(b"\x03" * 4096)

    def tearDown(self):
        self.tmp.cleanup()

    def _copie(self, active):
        with open(os.path.join(self.data, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"copier_fichiers": active}, fh)

    def _fiche(self):
        return db.add_solution(self.dbp, vehicle_label="C4", solution_type="E85 / Flexfuel", original_file=self.ori,
                               solution_file=self.sol, notes="original: ORIGINE C4.mpc | solution: E85France C4.mpc")

    def test_pas_de_copie_par_defaut(self):
        sid = self._fiche()
        s = db.get_solution(self.dbp, sid)
        self.assertEqual(s["original_file"], self.ori)
        self.assertEqual(s["solution_file"], self.sol)
        self.assertFalse(os.path.isdir(os.path.join(db.files_root(self.dbp), f"{sid:06d}")))

    def test_liberer_l_espace(self):
        self._copie(True)
        sid = self._fiche()
        copie = db.get_solution(self.dbp, sid)["solution_file"]
        self.assertTrue(copie.startswith(db.files_root(self.dbp)))
        self._copie(False)
        cartos = os.path.dirname(self.cartos)
        apercu = atelier.liberer_espace(self.dbp, cartos)
        self.assertEqual((apercu["copies"], apercu["retrouves"], apercu["octets"]), (2, 2, 8192))
        self.assertTrue(os.path.isfile(copie))                                  # aperçu : rien de touché
        r = atelier.liberer_espace(self.dbp, cartos, apply=True)
        self.assertEqual(r["retrouves"], 2)
        s = db.get_solution(self.dbp, sid)
        self.assertEqual(os.path.abspath(s["original_file"]), os.path.abspath(self.ori))   # le vrai original, pas l'homonyme
        self.assertEqual(os.path.abspath(s["solution_file"]), os.path.abspath(self.sol))
        self.assertFalse(os.path.exists(os.path.join(db.files_root(self.dbp), f"{sid:06d}")))
        self.assertEqual(atelier.liberer_espace(self.dbp, cartos)["copies"], 0)

    def test_copie_gardee_si_original_introuvable(self):
        self._copie(True)
        sid = self._fiche()
        os.remove(self.sol)
        r = atelier.liberer_espace(self.dbp, os.path.dirname(self.cartos), apply=True)
        self.assertEqual((r["retrouves"], r["gardees"]), (1, 1))
        s = db.get_solution(self.dbp, sid)
        self.assertTrue(os.path.isfile(s["solution_file"]))                    # la copie reste la seule version
        self.assertFalse(atelier.liberer_espace(self.dbp, os.path.join(self.tmp.name, "absent"))["ok"])


if __name__ == "__main__":
    unittest.main()
