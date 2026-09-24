"""Tests v1.47 — zones nommées, refus RSA, CSV/facteur 2D, checksums étendus."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import checksum, maps, patch as pmod, zones


SIZE = 128 * 1024


def _ecu(seed, ecu="0281020088", platform="EDC17CP14", size=SIZE):
    data = bytearray((seed * 17 + i * 13) % 256 for i in range(size))

    def place(off, text):
        b = text.encode("ascii")
        data[off - 2:off] = b"\x00\x00"
        data[off:off + len(b)] = b
        data[off + len(b):off + len(b) + 2] = b"\x00\x00"

    place(4096, ecu)
    place(8192, platform)
    return data


def _with_ck(data, block=0x4000, mode="complement", where="end"):
    out = bytearray(data)
    n = len(out) // block
    for b in range(n):
        start = b * block
        end = start + block
        if where == "start":
            payload = (start + 2, end)
            stored_off = start
        else:
            payload = (start, end - 2)
            stored_off = end - 2
        s = 0
        ps, pe = payload
        if (pe - ps) & 1:
            pe -= 1
        for i in range(ps, pe, 2):
            s += out[i] | (out[i + 1] << 8)
        s &= 0xFFFF
        want = ((0x10000 - s) & 0xFFFF) if "comp" in mode else s
        out[stored_off] = want & 0xFF
        out[stored_off + 1] = (want >> 8) & 0xFF
    return bytes(out)


def _pkcs1(n=256, ff=24):
    buf = bytearray(n)
    buf[0], buf[1] = 0x00, 0x01
    for i in range(ff):
        buf[2 + i] = 0xFF
    buf[2 + ff] = 0x00
    for i in range(3 + ff, n):
        buf[i] = (i * 19 + 7) & 0xFF
    return bytes(buf)


def _crc16_ccitt(data, start, end):
    crc = 0xFFFF
    for i in range(start, end):
        crc ^= data[i] << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _with_crc_ccitt(data, block=0x2000):
    out = bytearray(data)
    n = len(out) // block
    for b in range(n):
        start = b * block
        end = start + block
        c = _crc16_ccitt(out, start, end - 2)
        out[end - 2] = c & 0xFF
        out[end - 1] = (c >> 8) & 0xFF
    return bytes(out)


class NamedZoneTests(unittest.TestCase):
    def test_fap_zeroed_table(self):
        feat = {"shape": "smooth", "change": "zeroed"}
        z = zones.name_zone(feat, "DPF/FAP off", 128)
        self.assertEqual(z["code"], "fap_off")
        self.assertIn("FAP", z["label"])
        self.assertTrue(z["apply"])
        self.assertEqual(z["confidence"], "haute")

    def test_egr_ff(self):
        feat = {"shape": "constant", "change": "ff"}
        z = zones.name_zone(feat, "EGR off", 64)
        self.assertEqual(z["code"], "egr_off")

    def test_scr_combo(self):
        feat = {"shape": "smooth", "change": "zeroed"}
        z = zones.name_zone(feat, "Stage 1 + AdBlue/SCR off", 256)
        self.assertEqual(z["code"], "scr_off")

    def test_stage_table(self):
        feat = {"shape": "smooth", "change": "mixed"}
        z = zones.name_zone(feat, "Stage 1", 192)
        self.assertEqual(z["code"], "torque")

    def test_dtc_small(self):
        feat = {"shape": "constant", "change": "constant"}
        z = zones.name_zone(feat, "DTC off", 4)
        self.assertEqual(z["code"], "dtc_mask")

    def test_flexfuel(self):
        feat = {"shape": "smooth", "change": "mixed"}
        z = zones.name_zone(feat, "E85 / Flexfuel", 64)
        self.assertEqual(z["code"], "flexfuel")

    def test_rsa_never_applied(self):
        feat = {"shape": "noisy", "change": "mixed"}
        z = zones.name_zone(feat, "Stage 1", 256, rsa=True)
        self.assertEqual(z["code"], "rsa")
        self.assertFalse(z["apply"])

    def test_combo_fap_beats_stage_on_zeroed(self):
        feat = {"shape": "smooth", "change": "zeroed"}
        z = zones.name_zone(feat, "Stage 1 + DPF/FAP off", 200)
        self.assertEqual(z["code"], "fap_off")


class RsaDetectTests(unittest.TestCase):
    def test_pkcs1_detected(self):
        buf = _ecu(3)
        blob = _pkcs1()
        buf[0x3000:0x3000 + len(blob)] = blob
        blocks = checksum.detect_rsa(bytes(buf))
        pkcs = [b for b in blocks if b["kind"] == "pkcs1"]
        self.assertTrue(pkcs)
        self.assertEqual(pkcs[0]["off"], 0x3000)
        self.assertTrue(checksum.rsa_present(bytes(buf)))

    def test_short_ff_not_rsa(self):
        buf = bytearray(4096)
        buf[100:108] = b"\x00\x01\xff\xff\xff\xff\xff\x00"  # 5 FF < 16
        self.assertFalse(checksum.rsa_present(bytes(buf)))

    def test_random_not_rsa(self):
        self.assertFalse(checksum.rsa_present(bytes(_ecu(9))))


class ChecksumExtTests(unittest.TestCase):
    def test_additive_end_still_works(self):
        raw = _with_ck(_ecu(21), mode="complement")
        broken = bytearray(raw)
        broken[-2] ^= 0xFF
        res = checksum.apply(bytes(broken), platform="EDC17CP14")
        self.assertEqual(res["status"], "corrige")
        self.assertTrue(res["ready"])
        self.assertFalse(res["rsa"])

    def test_additive_start(self):
        # complément tête ≡ complément fin (somme du bloc = 0) — on plante
        # le mode *direct* en tête, qui est distinguable.
        raw = _with_ck(os.urandom(SIZE), mode="direct", where="start")
        res = checksum.apply(raw, platform="EDC17CP14")
        self.assertIn(res["status"], ("ok", "corrige"))
        self.assertTrue(res["ready"])
        self.assertIn("tete", res["method"])
        broken = bytearray(raw)
        broken[0] ^= 0xFF
        res2 = checksum.apply(bytes(broken), platform="EDC17CP14")
        self.assertEqual(res2["status"], "corrige")
        self.assertGreaterEqual(res2["blocks_corrected"], 1)



    def test_crc16_ccitt(self):
        raw = _with_crc_ccitt(os.urandom(SIZE), block=0x2000)
        broken = bytearray(raw)
        broken[0x1FFE] ^= 0xFF
        res = checksum.apply(bytes(broken), platform="EDC17CP14")
        self.assertEqual(res["status"], "corrige")
        self.assertIn("crc16_ccitt", res["method"])
        again = checksum.apply(res["data"], platform="EDC17CP14")
        self.assertEqual(again["status"], "ok")


    def test_rsa_blocks_ready(self):
        buf = bytearray(_with_ck(_ecu(24), mode="complement"))
        blob = _pkcs1()
        buf[0x2800:0x2800 + len(blob)] = blob
        # recaler le checksum après insertion
        buf = bytearray(_with_ck(buf, mode="complement"))
        res = checksum.apply(bytes(buf), platform="EDC17CP14")
        self.assertTrue(res["rsa"])
        self.assertFalse(res["ready"])
        self.assertIn("RSA", res["note"])

    def test_md1_prefix_still_bosch_like_unknown(self):
        raw = os.urandom(SIZE)
        res = checksum.apply(raw, platform="MD1CS003")
        self.assertEqual(res["status"], "inconnu")
        self.assertFalse(res["ready"])



class PatchRsaSkipTests(unittest.TestCase):
    def test_rsa_zone_not_copied(self):
        orig = _ecu(41)
        blob = _pkcs1()
        orig[0x5000:0x5000 + len(blob)] = blob
        orig = bytes(orig)
        sol = bytearray(orig)
        sol[0x5000 + 40:0x5000 + 80] = b"\x5a" * 40  # dans le PKCS
        sol[0x18000:0x18100] = b"\x11" * 0x100       # zone métier
        client = orig
        ev = pmod.evaluate_patch(client, orig, bytes(sol), "Stage 1")
        self.assertTrue(ev.get("rsa") or any(z.get("rsa") for z in ev["zones"]))
        rsa_zones = [z for z in ev["zones"] if z.get("rsa")]
        self.assertTrue(rsa_zones)
        res = pmod.build_patched(client, orig, bytes(sol),
                                 fiche_type="Stage 1", platform="EDC17CP14")
        self.assertFalse(res.get("ready_to_flash"))
        # PKCS d'origine conservé
        self.assertEqual(res["patched"][0x5000:0x5000 + 40], orig[0x5000:0x5000 + 40])
        # zone métier appliquée
        self.assertEqual(res["patched"][0x18000:0x18100], b"\x11" * 0x100)


    def test_fap_zone_named_in_report(self):
        orig = bytes(_ecu(42))
        sol = bytearray(orig)
        sol[0x6000:0x6100] = b"\x00" * 0x100
        ev = pmod.evaluate_patch(orig, orig, bytes(sol), "DPF/FAP off")
        self.assertEqual(ev["verdict"], "propre")
        codes = [z["zone"]["code"] for z in ev["zones"]]
        self.assertIn("fap_off", codes)


class MapsCsvTests(unittest.TestCase):
    def test_factor_and_csv(self):
        x = [0, 800, 1600, 2400, 3200, 4000, 4800, 5600]
        y = [0, 200, 400, 600, 800, 1000]
        z = [[400 + c * 10 + r * 20 for c in range(8)] for r in range(6)]
        blob = maps.build_2d(x, y, z)
        buf = bytearray(4096)
        buf[100:100 + len(blob)] = blob
        spec = {
            "bits": 16, "endian": "le", "rows": 6, "cols": 8,
            "axis_x_off": 100, "axis_y_off": 100 + 16,
            "data_off": 100 + 16 + 12,
        }
        d = maps.decode_map(bytes(buf), spec)
        csv = maps.to_csv(d, factor=0.01, offset=0, unit="mg")
        self.assertIn("facteur=0.01", csv)
        self.assertIn("unite=mg", csv)
        # 400 * 0.01 = 4
        self.assertIn(";4", csv)
        self.assertEqual(maps.apply_factor(400, 0.01, 0), 4.0)
        self.assertEqual(maps.fmt_value(4.0), "4")

    def test_csv_delta_section(self):
        x = [i * 100 for i in range(8)]
        y = [i * 50 for i in range(6)]
        z1 = [[10 + c + r for c in range(8)] for r in range(6)]
        z2 = [[20 + c + r for c in range(8)] for r in range(6)]
        a = {"x": x, "y": y, "z": z1, "rows": 6, "cols": 8, "data_off": 0}
        b = {"x": x, "y": y, "z": z2, "rows": 6, "cols": 8, "data_off": 0}
        csv = maps.to_csv(a, other=b, factor=1)
        self.assertIn("# original", csv)
        self.assertIn("# solution", csv)
        self.assertIn("# delta", csv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
