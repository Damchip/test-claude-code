"""Tests v1.65 — reconnaissance et listes rapides avec des dizaines de milliers de fiches."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

from matcher import db, engine, fingerprint
from test_v134 import _ecu


class RechercheRapideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = os.path.join(self.tmp.name, "solutions.db")
        db.init_db(self.dbp)
        self.raw = _ecu(5)
        fp = fingerprint.fingerprint(self.raw)
        self.sid = db.add_solution(self.dbp, vehicle_label="Audi A3", solution_type="Stage 1", stock_sha256=fp["sha256"],
                                   stock_size=fp["size"], minhash=fp["minhash"], solution_file="C:/absent/sol.bin")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cache_suit_les_changements_de_la_base(self):
        a = db.solutions_pour_recherche(self.dbp)
        self.assertIs(a, db.solutions_pour_recherche(self.dbp))            # base inchangée : pas de relecture
        self.assertEqual(engine.match(self.raw, self.dbp)["matches"][0]["id"], self.sid)
        time.sleep(0.01)
        autre = _ecu(9)
        fp = fingerprint.fingerprint(autre)
        sid2 = db.add_solution(self.dbp, vehicle_label="Golf", solution_type="E85 / Flexfuel", stock_sha256=fp["sha256"],
                               stock_size=fp["size"], minhash=fp["minhash"])
        b = db.solutions_pour_recherche(self.dbp)
        self.assertIsNot(a, b)                                              # nouvelle fiche : relue
        self.assertEqual(len(b), 2)
        self.assertEqual(engine.match(autre, self.dbp)["matches"][0]["id"], sid2)

    def test_liste_sans_verifier_le_disque(self):
        brut = db.list_solutions(self.dbp, hydrate=False)[0]
        self.assertEqual(brut["solution_file"], "C:/absent/sol.bin")
        self.assertNotIn("minhash", brut)

    def test_route_solutions(self):
        import app as outil
        saved = outil.DB_PATH
        try:
            outil.DB_PATH = self.dbp
            d = outil.app.test_client().get("/solutions").get_json()
            self.assertEqual(d["db_size"], 1)
            self.assertEqual(d["solutions"][0]["solution_file"], "C:/absent/sol.bin")
        finally:
            outil.DB_PATH = saved


if __name__ == "__main__":
    unittest.main()
