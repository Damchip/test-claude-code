"""Tests v1.64 — import complet d'une grande bibliothèque en tâche de fond, avec reprise."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

from matcher import db, import_complet
from test_v134 import _ecu


def _attendre(imp, delai=60):
    fin = time.time() + delai
    while imp.public()["actif"] and time.time() < fin:
        time.sleep(0.05)
    return imp.public()


class ImportCompletTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.data = os.path.join(t, "data")
        os.makedirs(self.data)
        self.dbp = os.path.join(self.data, "solutions.db")
        self.cartos = os.path.join(t, "CARTOS")
        for i in range(6):
            d = os.path.join(self.cartos, "Marque", f"Vehicule {i}")
            os.makedirs(d)
            raw = _ecu(40 + i)
            sol = bytearray(raw)
            sol[20000:20100] = bytes([i + 1]) * 100
            with open(os.path.join(d, f"ORI vehicule {i}.bin"), "wb") as fh:
                fh.write(raw)
            with open(os.path.join(d, f"Stage 1 vehicule {i}.bin"), "wb") as fh:
                fh.write(bytes(sol))
        self.etat = os.path.join(self.data, "import_complet.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_puis_reprise_sans_relire(self):
        imp = import_complet.ImportComplet(self.dbp, self.etat)
        imp.demarrer(self.cartos)
        e = _attendre(imp)
        self.assertEqual(e["etat"], "termine", e)
        self.assertEqual((e["total"], e["counts"]["added"]), (6, 6))
        self.assertEqual(db.count(self.dbp), 6)
        self.assertNotIn("faits", e)
        # nouvel import du même dossier : tout est reconnu par chemin, rien n'est relu ni ajouté
        imp2 = import_complet.ImportComplet(self.dbp, self.etat)
        imp2.demarrer(self.cartos)
        e2 = _attendre(imp2)
        self.assertEqual((e2["deja"], e2["traites"], e2["counts"]["added"]), (6, 0, 0))
        self.assertEqual(db.count(self.dbp), 6)

    def test_arret_puis_reprise(self):
        imp = import_complet.ImportComplet(self.dbp, self.etat)
        imp.demarrer(self.cartos)
        imp.arreter()
        e = _attendre(imp)
        self.assertIn(e["etat"], ("arrete", "termine"))
        n = db.count(self.dbp)
        # outil relancé : l'état « import » devient « interrompu », puis la reprise finit le travail
        imp2 = import_complet.ImportComplet(self.dbp, self.etat)
        imp2.demarrer(self.cartos)
        e2 = _attendre(imp2)
        self.assertEqual(e2["etat"], "termine")
        self.assertEqual(db.count(self.dbp), 6)
        self.assertEqual(e2["deja"], n)

    def test_etat_interrompu_et_erreurs(self):
        with open(self.etat, "w", encoding="utf-8") as fh:
            fh.write('{"etat": "import", "root": "x", "faits": []}')
        self.assertEqual(import_complet.ImportComplet(self.dbp, self.etat).public()["etat"], "interrompu")
        imp = import_complet.ImportComplet(self.dbp, self.etat)
        with self.assertRaises(RuntimeError):
            imp.demarrer(os.path.join(self.tmp.name, "absent"))

    def test_routes(self):
        import app as outil
        saved = (outil.DB_PATH, outil.DATA_DIR, outil._IMPORT_COMPLET)
        try:
            outil.DB_PATH, outil.DATA_DIR, outil._IMPORT_COMPLET = self.dbp, self.data, None
            c = outil.app.test_client()
            self.assertEqual(c.post("/import/complet", json={"path": "/absent"}).status_code, 400)
            self.assertTrue(c.post("/import/complet", json={"path": self.cartos}).get_json()["ok"])
            e = _attendre(outil._import_complet())
            self.assertEqual(c.get("/import/complet").get_json()["counts"]["added"], 6, e)
        finally:
            outil.DB_PATH, outil.DATA_DIR, outil._IMPORT_COMPLET = saved


if __name__ == "__main__":
    unittest.main()
