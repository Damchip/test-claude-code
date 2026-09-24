"""Tests v1.49 — auto-patch multi-prestas (Stage + FAP + E85)."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import patch as pmod


def _fiche(i, typ):
    return {"id": i, "solution_type": typ, "ecu_platform": "", "vehicle_label": "Demo"}


class CombineTests(unittest.TestCase):
    def test_parse_ids(self):
        self.assertEqual(pmod._parse_ids("3,1,3,2"), [3, 1, 2])
        self.assertEqual(pmod._parse_ids("7"), [7])

    def test_disjoint_prestas_stack(self):
        orig = bytearray(b'\x05'*256)
        stage = bytearray(orig)
        fap = bytearray(orig)
        stage[10:14] = b"\x11\x22\x33\x44"
        fap[80:84] = b"\x00\x00\x00\x00"
        client = bytes(orig)
        bundles = [
            {"fiche": _fiche(1, "Stage 1"), "orig": bytes(orig), "sol": bytes(stage)},
            {"fiche": _fiche(2, "DPF/FAP off"), "orig": bytes(orig), "sol": bytes(fap)},
        ]
        ev = pmod.combine_evaluations(client, bundles)
        self.assertEqual(ev["verdict"], "propre")
        self.assertFalse(ev["conflicts"])
        self.assertGreaterEqual(ev["applied_bytes"], 8)
        body, wrap, _plat = ev["_work"]
        self.assertEqual(body[10:14], b"\x11\x22\x33\x44")
        self.assertEqual(body[80:84], b"\x00\x00\x00\x00")

    def test_conflict_same_offset_different_values(self):
        orig = bytearray(128)
        a = bytearray(orig); a[20] = 1
        b = bytearray(orig); b[20] = 2
        bundles = [
            {"fiche": _fiche(1, "Stage 1"), "orig": bytes(orig), "sol": bytes(a)},
            {"fiche": _fiche(2, "E85"), "orig": bytes(orig), "sol": bytes(b)},
        ]
        ev = pmod.combine_evaluations(bytes(orig), bundles)
        self.assertEqual(ev["verdict"], "conflit")
        self.assertTrue(ev["conflicts"] or ev["conflict_bytes"])
        body, _, _ = ev["_work"]
        self.assertEqual(body[20], 0)  # octet en conflit non écrit

    def test_same_value_overlap_is_not_conflict(self):
        orig = bytearray(64)
        a = bytearray(orig); a[8:12] = b"\xaa\xaa\xaa\xaa"
        b = bytearray(orig); b[8:12] = b"\xaa\xaa\xaa\xaa"
        bundles = [
            {"fiche": _fiche(1, "Stage 1"), "orig": bytes(orig), "sol": bytes(a)},
            {"fiche": _fiche(2, "Stage 1"), "orig": bytes(orig), "sol": bytes(b)},
        ]
        ev = pmod.combine_evaluations(bytes(orig), bundles)
        self.assertEqual(ev["verdict"], "propre")
        self.assertEqual(ev["conflicts"], [])


if __name__ == "__main__":
    unittest.main()
