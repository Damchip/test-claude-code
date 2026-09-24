"""Tests v1.35 — détection et décodage de cartes 1D / 2D."""
import os
import shutil
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import maps, patch as pmod


def _blank(size=128 * 1024, fill=0xFF):
    return bytearray([fill] * size)


class AxisQualityTests(unittest.TestCase):
    def test_linear_from_zero(self):
        x = [i * 800 for i in range(16)]
        self.assertGreaterEqual(maps.axis_quality(x), 0.7)

    def test_rejects_strict_plus_one_addresses(self):
        x = list(range(1000, 1016))
        self.assertEqual(maps.axis_quality(x), 0.0)

    def test_rejects_non_monotonic(self):
        self.assertEqual(maps.axis_quality([0, 10, 5, 20]), 0.0)

    def test_rejects_ffff(self):
        self.assertEqual(maps.axis_quality([0, 100, 200, 65535]), 0.0)


class FindMapsTests(unittest.TestCase):
    def _plant(self):
        buf = _blank()
        x = [i * 800 for i in range(16)]
        y = [i * 200 for i in range(12)]
        z = [[400 + c * 25 + r * 35 + (c * r) // 4 for c in range(16)]
             for r in range(12)]
        blob = maps.build_2d(x, y, z)
        off = 0x5000
        buf[off:off + len(blob)] = blob
        x1 = [i * 500 for i in range(8)]
        z1 = [100, 180, 250, 310, 360, 400, 430, 450]
        blob1 = maps.build_1d(x1, z1)
        buf[0x4800:0x4800 + len(blob1)] = blob1
        xd = [i * 400 for i in range(8)]
        yd = [i * 50 for i in range(8)]
        zd = [[800 + r * 20 + c * 15 for c in range(8)] for r in range(8)]
        blobd = maps.build_2d(xd, yd, zd)
        buf[0x7600:0x7600 + len(blobd)] = blobd
        return bytes(buf), off, x, y, z, 0x4800

    def test_finds_planted_2d(self):
        data, off, x, y, z, _ = self._plant()
        out = maps.find_maps(data)
        twod = [m for m in out["maps"] if m["kind"] == "2d"]
        self.assertTrue(twod, "aucune carte 2D détectée")
        hit = next((m for m in twod if m["offset"] == off), twod[0])
        self.assertEqual(hit["cols"], 16)
        self.assertEqual(hit["rows"], 12)
        self.assertEqual(hit["x"], x)
        self.assertEqual(hit["y"], y)
        self.assertEqual(hit["bits"], 16)
        self.assertEqual(hit["endian"], "le")

    def test_finds_8x8(self):
        data, *_ = self._plant()
        out = maps.find_maps(data)
        hit = [m for m in out["maps"] if m["kind"] == "2d" and m["offset"] == 0x7600]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["rows"], 8)
        self.assertEqual(hit[0]["cols"], 8)

    def test_finds_planted_1d(self):
        data, *_rest = self._plant()
        off1 = 0x4800
        out = maps.find_maps(data)
        oned = [m for m in out["maps"] if m["kind"] == "1d" and m["offset"] == off1]
        self.assertTrue(oned, "carte 1D plantée non détectée")
        self.assertEqual(oned[0]["cols"], 8)

    def test_padding_not_a_map(self):
        data = bytes(_blank(fill=0xFF))
        out = maps.find_maps(data)
        self.assertEqual(out["maps"], [])

    def test_random_does_not_flood(self):
        rng = os.urandom(64 * 1024)
        out = maps.find_maps(rng)
        self.assertLessEqual(len(out["maps"]), 5)
        self.assertFalse(any(m["kind"] == "2d" for m in out["maps"]))

    def test_header_offsets_shifted(self):
        data, off, x, y, z, _ = self._plant()
        # header KESS réel (magic + taille standard 128 Ko + 0x400)
        head = bytearray(0x400)
        head[:4] = b"KESS"
        mag = b"Alientech"
        head[16:16 + len(mag)] = mag
        dump = bytes(head) + data
        out = maps.find_maps(dump)
        twod = [m for m in out["maps"] if m["kind"] == "2d" and m["cols"] == 16]
        self.assertTrue(twod)
        self.assertEqual(twod[0]["offset"], off + 0x400)
        self.assertEqual(out["header_len"], 0x400)


class GuessFromZoneTests(unittest.TestCase):
    def test_recovers_axes_before_z(self):
        buf = _blank()
        x = [i * 800 for i in range(16)]
        y = [i * 200 for i in range(12)]
        z = [[400 + c * 20 + r * 30 for c in range(16)] for r in range(12)]
        blob = maps.build_2d(x, y, z)
        off = 0x5000
        buf[off:off + len(blob)] = blob
        data_off = off + (16 + 12) * 2
        z_len = 16 * 12 * 2
        rec = maps.guess_from_zone(bytes(buf), data_off, z_len)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["cols"], 16)
        self.assertEqual(rec["rows"], 12)
        self.assertEqual(rec["x"], x)
        self.assertEqual(rec["y"], y)
        self.assertEqual(rec["data_off"], data_off)

    def test_odd_length_still_16bit(self):
        buf = _blank()
        x = [i * 800 for i in range(16)]
        y = [i * 200 for i in range(12)]
        z = [[400 + c * 20 + r * 30 for c in range(16)] for r in range(12)]
        blob = maps.build_2d(x, y, z)
        off = 0x5000
        buf[off:off + len(blob)] = blob
        data_off = off + (16 + 12) * 2
        rec = maps.guess_from_zone(bytes(buf), data_off, 16 * 12 * 2 - 1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["cols"], 16)
        self.assertEqual(rec["rows"], 12)


class DecodeTests(unittest.TestCase):
    def test_decode_roundtrip(self):
        x = [0, 10, 20, 30, 40, 50, 60, 70]
        y = [0, 100, 200, 300, 400, 500]
        z = [[i * 8 + j for j in range(8)] for i in range(6)]
        blob = maps.build_2d(x, y, z)
        buf = _blank()
        buf[100:100 + len(blob)] = blob
        spec = {
            "bits": 16, "endian": "le", "rows": 6, "cols": 8,
            "axis_x_off": 100, "axis_y_off": 100 + 16,
            "data_off": 100 + 16 + 12,
        }
        d = maps.decode_map(bytes(buf), spec)
        self.assertEqual(d["x"], x)
        self.assertEqual(d["y"], y)
        self.assertEqual(d["z"], z)


class PatchMapHintTests(unittest.TestCase):
    def test_zone_gets_2d_hint(self):
        buf = _blank()
        x = [i * 800 for i in range(16)]
        y = [i * 200 for i in range(12)]
        z0 = [[400 + c * 20 + r * 30 for c in range(16)] for r in range(12)]
        z1 = [[int(v * 1.08) for v in row] for row in z0]
        off = 0x5000
        raw = bytearray(buf)
        sol = bytearray(buf)
        b0 = maps.build_2d(x, y, z0)
        b1 = maps.build_2d(x, y, z1)
        raw[off:off + len(b0)] = b0
        sol[off:off + len(b1)] = b1
        ev = pmod.evaluate_patch(bytes(raw), bytes(raw), bytes(sol), fiche_type="Stage 1")
        self.assertEqual(ev["verdict"], "propre")
        hinted = [z for z in ev["zones"] if z.get("map")]
        self.assertTrue(hinted, "la table 2D modifiée devrait être reconnue")
        self.assertEqual(hinted[0]["map"]["cols"], 16)
        self.assertEqual(hinted[0]["map"]["rows"], 12)


class ImportDbTests(unittest.TestCase):
    def test_import_replaces_and_migrates(self):
        import tempfile
        from matcher import db, fingerprint
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        src = os.path.join(td, "old.db")
        dest = os.path.join(td, "solutions.db")
        db.init_db(src)
        db.add_solution(src, ecu_version="0281020088", vehicle_label="Audi test",
                        solution_type="Stage 1", stock_sha256="abc", stock_size=10,
                        minhash=[1, 2, 3], minhash_ver=1)
        db.init_db(dest)
        db.add_solution(dest, ecu_version="x", vehicle_label="old dest",
                        solution_type="Stage 1", stock_sha256="zzz", stock_size=1,
                        minhash=[0])
        out = db.import_db_file(dest, src)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["count"], 1)
        row = db.get_solution(dest, 1)
        self.assertEqual(row["vehicle_label"], "Audi test")
        # colonne minhash_ver ajoutée
        self.assertIn("minhash_ver", row)

    def test_rejects_non_db(self):
        import tempfile
        from matcher import db
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        junk = os.path.join(td, "x.bin")
        open(junk, "wb").write(b"not a sqlite" * 100)
        dest = os.path.join(td, "solutions.db")
        db.init_db(dest)
        out = db.import_db_file(dest, junk)
        self.assertFalse(out["ok"])
