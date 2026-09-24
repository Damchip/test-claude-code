"""Tests v1.51 — garde-fou calibre, offres portail."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import engine, patch as pmod


class StockGuardTests(unittest.TestCase):
    def test_mix_refused(self):
        orig = bytearray(b"\x05" * 64)
        a = bytearray(orig); a[2] = 1
        b = bytearray(orig); b[8] = 2
        bundles = [
            {"fiche": {"id": 1, "solution_type": "Stage 1",
                       "stock_sha256": "aaa", "ecu_version": "111"},
             "orig": bytes(orig), "sol": bytes(a)},
            {"fiche": {"id": 2, "solution_type": "DPF/FAP off",
                       "stock_sha256": "bbb", "ecu_version": "222"},
             "orig": bytes(orig), "sol": bytes(b)},
        ]
        ev = pmod.combine_evaluations(bytes(orig), bundles)
        self.assertEqual(ev.get("error"), "stocks_melanges")
        ev2 = pmod.combine_evaluations(bytes(orig), bundles, force_mix=True)
        self.assertNotEqual(ev2.get("error"), "stocks_melanges")

    def test_same_sha_ok(self):
        orig = bytearray(b"\x05" * 64)
        a = bytearray(orig); a[2] = 1
        b = bytearray(orig); b[8] = 2
        bundles = [
            {"fiche": {"id": 1, "solution_type": "Stage 1",
                       "stock_sha256": "abc", "ecu_version": "111"},
             "orig": bytes(orig), "sol": bytes(a)},
            {"fiche": {"id": 2, "solution_type": "E85",
                       "stock_sha256": "ABC", "ecu_version": "111"},
             "orig": bytes(orig), "sol": bytes(b)},
        ]
        ev = pmod.combine_evaluations(bytes(orig), bundles)
        self.assertNotEqual(ev.get("error"), "stocks_melanges")


class OfferRowsTests(unittest.TestCase):
    def test_rows(self):
        rel = [
            {"id": 10, "solution_type": "Stage 1"},
            {"id": 11, "solution_type": "Stage 1 + DPF/FAP off"},
            {"id": 12, "solution_type": "E85 / Flexfuel"},
        ]
        rows = engine.portal_offer_rows(rel)
        names = [r["prestation"] for r in rows]
        self.assertIn("Stage 1", names)
        self.assertIn("DPF/FAP off", names)
        self.assertIn("E85 / Flexfuel", names)


if __name__ == "__main__":
    unittest.main()
