"""Tests v1.36 — relecture métadonnées depuis les chemins."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import db, metadata


class PathMetaTests(unittest.TestCase):
    def test_claas_jaguar_not_car(self):
        m = metadata.parse(
            r"D:\CARTOS\ACM+MCM Claas Jaguar 860 510cv - SCR NV\MCM\CLAAS-JAGUAR-860-MCM.cod.mpc")
        self.assertEqual(m["brand"], "Claas")
        self.assertIn("AdBlue/SCR", m["solution_type"] or "")
        self.assertEqual(m["platform"], "MCM")

    def test_liebherr_egr_from_solution(self):
        m = metadata.parse_record(
            r"D:\CARTOS\EDC17CV54 Liebherr 914 3.6 Dc Motors\LIEBHERR-914-ORI.bin",
            r"D:\CARTOS\EDC17CV54 Liebherr 914 3.6 Dc Motors\E85France NV SP51_LIEBHEER_BOSCH EDC17CV52_EGR.bin",
        )
        self.assertEqual(m["brand"], "Liebherr")
        self.assertIn("EGR off", m["solution_type"])
        self.assertNotIn("Origine", m["solution_type"])
        self.assertEqual(m["platform"], "EDC17CV54")

    def test_cat_not_decat(self):
        m = metadata.parse(
            r"D:\CARTOS\ADEM4 Claas Lexion 650 9.3 CAT - DT318JP - DPF OFF - DC\CLAASLEXION650.bin")
        self.assertEqual(m["brand"], "Claas")
        self.assertEqual(m["solution_type"], "DPF/FAP off")
        self.assertEqual(m["platform"], "ADEM4")

    def test_newholland_scr_off_underscore(self):
        m = metadata.parse(
            r"D:\CARTOS\MD1CE101 New Holland T7.270\NEW-HOLLAND-T7.270_SCR_OFF.MOD")
        self.assertEqual(m["brand"], "New Holland")
        self.assertIn("AdBlue/SCR", m["solution_type"] or "")
        self.assertEqual(m["platform"], "MD1CE101")

    def test_onedrive_e85_is_not_flexfuel(self):
        m = metadata.parse(
            r"D:\OneDrive E85\OneDrive\CARTOS\Poids Lourds\EDC17CV42 MAN TGS\MAN-TGS.cod.mpc")
        self.assertEqual(m["brand"], "MAN")
        self.assertNotIn("E85", m["solution_type"] or "")
        self.assertEqual(m["platform"], "EDC17CV42")

    def test_normalize_garbage(self):
        self.assertIsNone(metadata.normalize_platform("dMe"))
        self.assertIsNone(metadata.normalize_platform("me0m"))
        self.assertEqual(metadata.normalize_platform("EDC17CV41-EP-"), "EDC17CV41")
        self.assertEqual(metadata.normalize_platform("EDC16.A000"), "EDC16")
        self.assertEqual(metadata.normalize_platform("EDC17C60"), "EDC17C60")


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.dbp = os.path.join(self.td, "t.db")
        db.init_db(self.dbp)
        db.add_solution(
            self.dbp, ecu_version="", ecu_platform="dMe",
            vehicle_label="Jaguar CLAAS JAGUAR 860 GUILBAUD.cod",
            solution_type="", manufacturer="",
            original_file=r"D:\CARTOS\ACM+MCM Claas Jaguar 860 - SCR NV\MCM\CLAAS-JAGUAR-860-MCM.cod.mpc",
            solution_file=r"D:\CARTOS\ACM+MCM Claas Jaguar 860 - SCR NV\MCM\E85France NV SCR CLAAS.mpc.bin",
            stock_sha256="aa", stock_size=100, minhash=[1])

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_fills_empty_and_fixes_jaguar(self):
        out = db.backfill_metadata(self.dbp)
        self.assertGreaterEqual(out["types"], 1)
        self.assertGreaterEqual(out["platforms"], 1)
        self.assertGreaterEqual(out["labels"], 1)
        row = db.get_solution(self.dbp, 1)
        self.assertIn("AdBlue/SCR", row["solution_type"])
        self.assertEqual(row["ecu_platform"], "MCM")
        self.assertTrue(row["vehicle_label"].lower().startswith("claas"))
        self.assertNotEqual(row["ecu_platform"], "dMe")


if __name__ == "__main__":
    unittest.main()
