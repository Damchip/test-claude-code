"""Tests v1.48 — dossiers client (immat, VIN, prestas, dump, jobs)."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import db, dossiers as dos


class PlateVinTests(unittest.TestCase):
    def test_siv(self):
        self.assertEqual(dos.normalize_plate("aa123bb"), "AA-123-BB")
        self.assertEqual(dos.normalize_plate("AA-123-BB"), "AA-123-BB")

    def test_fni(self):
        self.assertEqual(dos.normalize_plate("1234AB31"), "1234 AB 31")
        self.assertEqual(dos.normalize_plate("12 AB 31"), "12 AB 31")

    def test_vin(self):
        v = dos.normalize_vin("vf1 12345678901234")
        self.assertEqual(len(v), 17)
        self.assertTrue(dos.vin_ok("VF1ABCDEF23456789"))
        self.assertFalse(dos.vin_ok("VF1IOQ"))  # trop court + I O Q


class DossierCrudTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.dbp = os.path.join(self.td, "t.db")
        db.init_db(self.dbp)

    def test_create_search_plate(self):
        did = dos.create(self.dbp, client_name="Martin", plate="aa123bb",
                         vehicle_label="Clio 4 1.5 dCi",
                         prestas=["Stage 1", "DPF/FAP off"],
                         phone="06 12 34 56 78")
        rows = dos.list_dossiers(self.dbp, q="AA-123-BB")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], did)
        self.assertEqual(rows[0]["plate"], "AA-123-BB")
        self.assertIn("Stage 1", rows[0]["prestas"])
        self.assertIn("DPF/FAP off", rows[0]["prestas"])
        by_phone = dos.list_dossiers(self.dbp, q="0612345678")
        self.assertEqual(len(by_phone), 1)

    def test_suggest_same_plate(self):
        dos.create(self.dbp, client_name="A", plate="AA-123-BB")
        hits = dos.suggest(self.dbp, plate="aa123bb")
        self.assertEqual(len(hits), 1)
        self.assertIn("immat", hits[0]["why"])

    def test_dump_archive_and_delete(self):
        did = dos.create(self.dbp, client_name="B")
        path = dos.save_dump(self.dbp, did, b"ECU" * 100, "lecture.bin")
        self.assertTrue(os.path.isfile(path))
        d = dos.get(self.dbp, did)
        self.assertTrue(d["dump_present"])
        folder = os.path.join(dos.dossiers_root(self.dbp), f"{did:06d}")
        dos.delete(self.dbp, did)
        self.assertIsNone(dos.get(self.dbp, did))
        self.assertFalse(os.path.isdir(folder))

    def test_attach_job_and_presta(self):
        did = dos.create(self.dbp, client_name="C", status="ouvert")
        jid = db.add_job(self.dbp, client_name="clio.bin",
                         solution_label="Stage 1 + DPF/FAP off",
                         verdict="propre", zones=2, dossier_id=did)
        d = dos.get(self.dbp, did)
        self.assertEqual(d["status"], "en_cours")
        self.assertEqual(len(d["jobs"]), 1)
        self.assertEqual(d["jobs"][0]["id"], jid)
        self.assertIn("Stage 1", d["prestas"])
        self.assertIn("DPF/FAP off", d["prestas"])

    def test_from_inbox(self):
        rec = {
            "fichier": "20260915_clio.bin",
            "plateforme": "EDC17C84",
            "fabricant": "Bosch",
            "contact": {
                "nom": "Dupont", "tel": "0611223344",
                "immat": "BB-987-CC", "vehicule": "Golf 7",
                "vin": "WVWZZZ1KZAW123456",
            },
        }
        dump = b"\x00\x01" + b"X" * 64
        did = dos.from_inbox(self.dbp, rec, dump_bytes=dump)
        d = dos.get(self.dbp, did)
        self.assertEqual(d["client_name"], "Dupont")
        self.assertEqual(d["plate"], "BB-987-CC")
        self.assertEqual(d["source"], "portail")
        self.assertEqual(d["ecu_platform"], "EDC17C84")
        self.assertTrue(d["dump_present"])
        self.assertEqual(open(d["dump_file"], "rb").read(), dump)

    def test_status_filter(self):
        dos.create(self.dbp, client_name="Ouvert", status="ouvert")
        dos.create(self.dbp, client_name="Livre", status="livre")
        self.assertEqual(len(dos.list_dossiers(self.dbp, status="livre")), 1)
        c = dos.counts(self.dbp)
        self.assertEqual(c["total"], 2)
        self.assertEqual(c["ouverts"], 1)

    def test_unattached_jobs(self):
        db.add_job(self.dbp, client_name="orphan.bin", solution_label="Stage 1")
        ua = dos.unattached_jobs(self.dbp)
        self.assertEqual(len(ua), 1)
        self.assertEqual(ua[0]["client_name"], "orphan.bin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
