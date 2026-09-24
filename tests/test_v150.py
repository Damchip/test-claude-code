"""Tests v1.50 — pack de sortie (nommage + rapport + zip)."""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import pack as packmod
from matcher import patch as pmod


class SlugTests(unittest.TestCase):
    def test_presta_slug(self):
        self.assertEqual(packmod.presta_slug("Stage 1 + DPF/FAP off + E85"), "E85_Stage1_FAP")
        self.assertEqual(packmod.presta_slug("Stage 1"), "Stage1")

    def test_basename_plate(self):
        name = packmod.pack_basename("aa-123-bb", "VF1ABCDEF23456789", "Stage 1 + FAP off", "x.bin")
        self.assertTrue(name.startswith("AA-123-BB_"))
        self.assertIn("Stage1", name)
        self.assertIn("FAP", name)

    def test_basename_vin_fallback(self):
        name = packmod.pack_basename("", "VF1ABCDEF23456789", "E85", "foo.bin")
        self.assertTrue(name.startswith("23456789_"))
        self.assertIn("E85", name)


class PackWriteTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    def test_write_and_zip(self):
        orig = bytearray(b"\x05" * 128)
        sol = bytearray(orig)
        sol[10:14] = b"\x11\x22\x33\x44"
        bundles = [{
            "fiche": {"id": 1, "solution_type": "Stage 1", "ecu_platform": ""},
            "orig": bytes(orig), "sol": bytes(sol),
        }]
        ev = pmod.build_combined(bytes(orig), bundles)
        self.assertIn("patched", ev)
        meta = packmod.meta_from_result(
            ev, client_name="lecture.bin", plate="AA-123-BB",
            label="Stage 1", ids=[1],
        )
        base = packmod.pack_basename("AA-123-BB", "", "Stage 1", "lecture.bin")
        paths = packmod.write_files(self.td, base, ev["patched"], meta)
        self.assertTrue(os.path.isfile(paths["bin"]))
        self.assertTrue(os.path.isfile(paths["txt"]))
        txt = open(paths["txt"], encoding="utf-8").read()
        self.assertIn("AA-123-BB", txt)
        self.assertIn("Stage 1", txt)
        buf, zname = packmod.zip_pack(paths, base)
        self.assertTrue(zname.endswith(".zip"))
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        self.assertEqual(len(names), 3)
        self.assertTrue(any(n.endswith(".bin") for n in names))


if __name__ == "__main__":
    unittest.main()
