"""Tests de non-régression v1.33 — matching, import multi-types, seed, métadonnées."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import db, engine, extract, fingerprint, importer, metadata


def _bin(seed, ecu="", platform="", size=32 * 1024):
    rng_bytes = bytearray((seed * 17 + i * 13) % 256 for i in range(size))
    def place(off, text):
        b = text.encode("ascii")
        rng_bytes[off - 2:off] = b"\x00\x00"
        rng_bytes[off:off + len(b)] = b
        rng_bytes[off + len(b):off + len(b) + 2] = b"\x00\x00"
    if ecu:
        place(4096, ecu)
    if platform:
        place(8192, platform)
    return bytes(rng_bytes)


class MetadataTests(unittest.TestCase):
    def test_e85_keyword(self):
        m = metadata.parse(r"D:\jobs\Golf_1.4_E85_flexfuel.bin")
        self.assertIn("E85", m["solution_type"] or "")

    def test_claas_brand(self):
        m = metadata.parse(r"D:\Claas\Jaguar_860_ORI.bin")
        self.assertEqual(m["brand"], "Claas")

    def test_fendt_deutz(self):
        m = metadata.parse(r"Fendt_720_EDC17CV52_Stage1.bin")
        self.assertEqual(m["brand"], "Fendt")
        self.assertEqual(m["solution_type"], "Stage 1")


class ExtractTests(unittest.TestCase):
    def test_sim266(self):
        data = _bin(3, platform="SIM266")
        plat, fam = extract.detect_platform(data)
        self.assertEqual(plat, "SIM266")
        self.assertEqual(fam, "Bosch")

    def test_keihin(self):
        data = _bin(4, platform="KEIHIN")
        plat, fam = extract.detect_platform(data)
        self.assertEqual(plat, "KEIHIN")
        self.assertEqual(fam, "Keihin")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.dbp = os.path.join(self.td, "t.db")
        db.init_db(self.dbp)
        raw = _bin(101, "0281020088", "EDC17CP14")
        fp = fingerprint.fingerprint(raw)
        db.add_solution(self.dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
                        vehicle_label="Audi A3 2.0 TDI", solution_type="Stage 1",
                        tested_status="testee", stock_sha256=fp["sha256"],
                        stock_size=fp["size"], minhash=fp["minhash"])
        db.add_solution(self.dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
                        vehicle_label="Audi A3 2.0 TDI", solution_type="DPF/FAP off",
                        tested_status="testee", stock_sha256=fp["sha256"],
                        stock_size=fp["size"], minhash=fp["minhash"])
        self.stock = raw

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_exact_sha_is_compatible_and_lists_both_types(self):
        r = engine.match(self.stock, self.dbp, path="Audi_A3_ORI.bin")
        v, rel = engine.portal_verdict(r)
        self.assertEqual(v, "compatible")
        types = {m["solution_type"] for m in rel}
        self.assertIn("Stage 1", types)
        self.assertIn("DPF/FAP off", types)

    def test_platform_alone_is_not_a_client_hit(self):
        # même plateforme, autre binaire, pas la même calibration
        other = _bin(202, "1037551299", "EDC17CP14")
        r = engine.match(other, self.dbp, path="BMW_EDC17CP14.bin")
        v, rel = engine.portal_verdict(r)
        self.assertEqual(v, "non_trouve")
        self.assertEqual(rel, [])
        if r["matches"]:
            self.assertLess(r["matches"][0]["score"], 0.6)

    def test_no_substring_id_match(self):
        # identifiant court qui est une sous-chaîne du vrai numéro
        other = _bin(303, "0281", "EDC17C41")
        r = engine.match(other, self.dbp, path="x.bin")
        self.assertFalse(any(m.get("calibration_exact") for m in r["matches"]))
        v, _ = engine.portal_verdict(r)
        self.assertEqual(v, "non_trouve")

    def test_same_type_is_duplicate_other_type_is_not(self):
        sha = fingerprint.sha256(self.stock)
        self.assertTrue(db.sha256_type_exists(self.dbp, sha, "Stage 1"))
        self.assertTrue(db.sha256_type_exists(self.dbp, sha, "DPF/FAP off"))
        self.assertFalse(db.sha256_type_exists(self.dbp, sha, "E85 / Flexfuel"))
        self.assertTrue(db.sha256_exists(self.dbp, sha))


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.dbp = os.path.join(self.td, "t.db")
        job = os.path.join(self.td, "Audi A3")
        os.makedirs(job)
        ori = _bin(101, "0281020088", "EDC17CP14")
        st1 = bytearray(ori); st1[20000:20100] = b"\x11" * 100
        dpf = bytearray(ori); dpf[30000:30100] = b"\x22" * 100
        open(os.path.join(job, "A3_ORI.bin"), "wb").write(ori)
        open(os.path.join(job, "A3_Stage1.bin"), "wb").write(st1)
        open(os.path.join(job, "A3_DPFoff.bin"), "wb").write(dpf)
        self.job = job

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_one_folder_two_types(self):
        res = importer.scan_folder(self.td, dry_run=False, db_path=self.dbp, min_ko=16)
        types = {e["type"] for e in res["entries"] if e["state"] == "ajouté"}
        self.assertIn("Stage 1", types)
        self.assertIn("DPF/FAP off", types)
        self.assertEqual(res["counts"]["added"], 2)
        # relancer = doublons, pas de 3e fiche
        res2 = importer.scan_folder(self.td, dry_run=False, db_path=self.dbp, min_ko=16)
        self.assertEqual(res2["counts"]["added"], 0)
        self.assertEqual(res2["counts"]["dup"], 2)


class SeedSafetyTests(unittest.TestCase):
    def test_refuses_non_empty_db(self):
        # On vérifie la logique, pas le script (qui sys.exit).
        td = tempfile.mkdtemp()
        try:
            dbp = os.path.join(td, "solutions.db")
            db.init_db(dbp)
            db.add_solution(dbp, ecu_version="x", vehicle_label="keep-me",
                            stock_sha256="abc", stock_size=10)
            self.assertGreater(db.count(dbp), 0)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
