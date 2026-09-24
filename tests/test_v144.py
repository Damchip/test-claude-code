"""Tests v1.44 — matching par presta, familles ECU, devis portail."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import db, engine, extract, fingerprint, metadata
import portal as portalmod


SIZE = 32 * 1024


def _bin(seed, ecu="0281020088", platform="EDC17CP14", extra=""):
    data = bytearray((seed * 17 + i * 13) % 256 for i in range(SIZE))

    def place(off, text):
        b = text.encode("ascii")
        data[off - 2:off] = b"\x00\x00"
        data[off:off + len(b)] = b
        data[off + len(b):off + len(b) + 2] = b"\x00\x00"

    place(4096, ecu)
    place(8192, platform)
    if extra:
        place(12000, extra)
    return bytes(data)


class TypeAtomsTests(unittest.TestCase):
    def test_split_combo(self):
        self.assertEqual(
            metadata.type_atoms("Stage 1 + DPF/FAP off + AdBlue/SCR off"),
            ["Stage 1", "DPF/FAP off", "AdBlue/SCR off"])

    def test_alias_and_origine_dropped(self):
        atoms = metadata.ordered_atoms(["Origine (stock)", "DPF off", "Stage 1"])
        self.assertEqual(atoms, ["Stage 1", "DPF/FAP off"])
        self.assertNotIn("Origine (stock)", atoms)


class FamilyTests(unittest.TestCase):
    def test_cpegd_kefico(self):
        m = metadata.parse(r"D:\CARTOS\REFERENCE\CPEGD2.20.4 Hyundai Kona\Kona.bin")
        self.assertEqual(m["platform"], "CPEGD2.20.4")
        self.assertEqual(m["manufacturer"], "Kefico")

    def test_bem_hitachi(self):
        m = metadata.parse(r"D:\CARTOS\REFERENCE\BEM542 Renault Arkana\Arkana.bin")
        self.assertEqual(m["platform"], "BEM542")
        self.assertEqual(m["manufacturer"], "Hitachi")

    def test_e6t_marelli(self):
        m = metadata.parse(r"D:\CARTOS\REFERENCE\E6T78 Mitsubishi ASX\ASX.bin")
        self.assertEqual(m["platform"], "E6T78")
        self.assertEqual(m["manufacturer"], "Marelli")

    def test_binary_kefico_sig(self):
        data = _bin(9, "1234567890", "CPEGD2.20", extra="KEFICO")
        info = extract.extract(data)
        self.assertEqual(info["manufacturer"], "Kefico")
        self.assertTrue((info["platform"] or "").startswith("CPEGD"))

    def test_acdelco_sig(self):
        data = _bin(8, "ABCDEF12", "E78XXXX", extra="ACDELCO")
        info = extract.extract(data)
        self.assertEqual(info["manufacturer"], "ACDelco")


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.dbp = os.path.join(self.td, "t.db")
        db.init_db(self.dbp)
        self.raw = _bin(101)
        fp = fingerprint.fingerprint(self.raw)
        db.add_solution(self.dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
                        vehicle_label="Audi A3 2.0 TDI", solution_type="Stage 1",
                        tested_status="testee", stock_sha256=fp["sha256"],
                        stock_size=fp["size"], minhash=fp["minhash"])
        db.add_solution(self.dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
                        vehicle_label="Audi A3 2.0 TDI",
                        solution_type="DPF/FAP off + AdBlue/SCR off",
                        tested_status="testee", stock_sha256=fp["sha256"],
                        stock_size=fp["size"], minhash=fp["minhash"])
        other = _bin(202)
        fp2 = fingerprint.fingerprint(other)
        db.add_solution(self.dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
                        vehicle_label="Audi A3 2.0 TDI", solution_type="E85 / Flexfuel",
                        tested_status="testee", stock_sha256=fp2["sha256"],
                        stock_size=fp2["size"], minhash=fp2["minhash"])

    def test_fap_filename_ranks_fap_first(self):
        r = engine.match(self.raw, self.dbp, path="Audi_A3_FAP_off.bin")
        self.assertTrue(r["matches"])
        self.assertIn("DPF/FAP off", r["matches"][0]["type_atoms"])
        self.assertTrue(r["matches"][0]["type_match"])

    def test_same_calib_lists_other_presta(self):
        r = engine.match(self.raw, self.dbp, path="Audi_A3_ORI.bin")
        types = {m["solution_type"] for m in r["matches"]
                 if m.get("exact") or m.get("calibration_exact") or m.get("same_stock")}
        self.assertIn("Stage 1", types)
        self.assertIn("DPF/FAP off + AdBlue/SCR off", types)
        self.assertIn("E85 / Flexfuel", types)

    def test_portal_splits_atoms(self):
        r = engine.match(self.raw, self.dbp, path="x.bin")
        v, rel = engine.portal_verdict(r)
        self.assertEqual(v, "compatible")
        offers = engine.portal_offers(rel)
        self.assertIn("Stage 1", offers)
        self.assertIn("DPF/FAP off", offers)
        self.assertIn("AdBlue/SCR off", offers)
        self.assertIn("E85 / Flexfuel", offers)
        self.assertNotIn("Origine (stock)", offers)

    def test_price_alias(self):
        cfg = {"show_prices": True, "currency": "€", "prices": {"DPF off": 150}}
        self.assertEqual(portalmod.price_for("DPF/FAP off", cfg), "150 €")
        self.assertEqual(portalmod.price_for("Stage 1", cfg), "sur devis")


if __name__ == "__main__":
    unittest.main()
