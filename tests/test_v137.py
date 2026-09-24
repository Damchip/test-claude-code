"""Tests v1.37 — E85 REFERENCE + remap CARTOS."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import db, metadata


class E85ReferenceTests(unittest.TestCase):
    def test_e85france_filename_is_flexfuel(self):
        m = metadata.parse(
            r"D:\OneDrive E85\OneDrive\CARTOS\REFERENCE\4AF FIAT Panda\E85France-FIAT-PANDA-EX145JX.dec")
        self.assertIn("E85 / Flexfuel", m["solution_type"] or "")

    def test_percent_is_flexfuel(self):
        m = metadata.parse(
            r"D:\CARTOS\REFERENCE\CPEGD Kia\E85France 23% Hyundai_Accent.bin")
        self.assertIn("E85 / Flexfuel", m["solution_type"] or "")

    def test_pl_e85france_scr_is_not_flexfuel(self):
        m = metadata.parse(
            r"D:\OneDrive E85\OneDrive\CARTOS\Poids Lourds\ACM Claas\E85France NV SCR CLAAS.mpc.bin")
        self.assertIn("AdBlue/SCR", m["solution_type"] or "")
        self.assertNotIn("E85", m["solution_type"] or "")

    def test_onedrive_folder_still_ignored(self):
        m = metadata.parse(
            r"D:\OneDrive E85\OneDrive\CARTOS\Poids Lourds\EDC17CV42 MAN TGS\MAN-TGS.cod.mpc")
        self.assertNotIn("E85", m["solution_type"] or "")

    def test_cm2150_platform(self):
        m = metadata.parse(
            r"D:\CARTOS\Poids Lourds\CM2150E DAF LF55.250 - SCR P\DAF-LF55.cod.mpc")
        self.assertEqual(m["platform"], "CM2150E")
        self.assertEqual(m["manufacturer"], "Cummins")
        self.assertEqual(m["brand"], "DAF")


class RemapTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.dbp = os.path.join(self.td, "t.db")
        db.init_db(self.dbp)
        self.root = os.path.join(self.td, "CARTOS")
        a = os.path.join(self.root, "REFERENCE", "Panda")
        b = os.path.join(self.root, "Poids Lourds", "Claas")
        os.makedirs(a); os.makedirs(b)
        self.ori = os.path.join(a, "ORI.bin")
        self.sol = os.path.join(a, "E85.bin")
        self.pl = os.path.join(b, "SCR.bin")
        open(self.ori, "wb").write(b"ori")
        open(self.sol, "wb").write(b"sol")
        open(self.pl, "wb").write(b"pl")
        db.add_solution(self.dbp, vehicle_label="Panda", solution_type="E85 / Flexfuel",
                        original_file=r"D:\OneDrive E85\OneDrive\CARTOS\REFERENCE\Panda\ORI.bin",
                        solution_file=r"D:\OneDrive E85\OneDrive\CARTOS\REFERENCE\Panda\E85.bin",
                        stock_sha256="x", stock_size=3, minhash=[1])
        db.add_solution(self.dbp, vehicle_label="Claas", solution_type="AdBlue/SCR off",
                        original_file=r"D:\OneDrive E85\OneDrive\CARTOS\Poids Lourds\Claas\SCR.bin",
                        solution_file="",
                        stock_sha256="y", stock_size=2, minhash=[1])

    def test_dry_run_finds_files(self):
        root = self.root
        out = db.remap_roots(self.dbp, new_root=root, apply=False)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["old_prefix"].replace("/", "\\").endswith("CARTOS"))
        self.assertEqual(out["originals_found"], 2)
        self.assertEqual(out["solutions_found"], 1)
        self.assertFalse(out["applied"])
        row = db.get_solution(self.dbp, 1)
        self.assertTrue(row["original_file"].startswith("D:"))

    def test_apply_rewrites(self):
        root = self.root
        out = db.remap_roots(self.dbp, new_root=root, apply=True)
        self.assertTrue(out["applied"])
        row = db.get_solution(self.dbp, 1)
        self.assertTrue(os.path.isfile(row["original_file"]))
        self.assertTrue(os.path.isfile(row["solution_file"]))


if __name__ == "__main__":
    unittest.main()
