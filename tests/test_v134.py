"""Tests v1.34 — headers programmeur, MinHash v2, checksums Bosch, archive, patch serveur."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import checksum, db, engine, fingerprint, headers, patch as pmod


SIZE = 128 * 1024  # taille flash standard → header = surplus connu


def _ecu(seed, ecu="0281020088", platform="EDC17CP14", size=SIZE):
    data = bytearray((seed * 17 + i * 13) % 256 for i in range(size))

    def place(off, text):
        b = text.encode("ascii")
        data[off - 2:off] = b"\x00\x00"
        data[off:off + len(b)] = b
        data[off + len(b):off + len(b) + 2] = b"\x00\x00"

    place(4096, ecu)
    place(8192, platform)
    return bytes(data)


def _kess(body, hdr=0x400):
    head = bytearray(hdr)
    head[:4] = b"KESS"
    mag = b"Alientech"
    head[16:16 + len(mag)] = mag
    return bytes(head) + body


def _rand_ecu(platform="EDC17CP14", size=SIZE, ecu="0281020088"):
    data = bytearray(os.urandom(size))

    def place(off, text):
        b = text.encode("ascii")
        data[off - 2:off] = b"\x00\x00"
        data[off:off + len(b)] = b
        data[off + len(b):off + len(b) + 2] = b"\x00\x00"

    place(4096, ecu)
    place(8192, platform)
    return bytes(data)


def _with_ck(data, block=0x4000, mode="complement"):
    out = bytearray(data)
    n = len(out) // block
    for b in range(n):
        start = b * block
        end = start + block
        payload_end = end - 2
        s = 0
        for i in range(start, payload_end, 2):
            s += out[i] | (out[i + 1] << 8)
        s &= 0xFFFF
        want = ((0x10000 - s) & 0xFFFF) if mode == "complement" else s
        out[payload_end] = want & 0xFF
        out[payload_end + 1] = (want >> 8) & 0xFF
    return bytes(out)


class HeaderTests(unittest.TestCase):
    def test_strip_kess(self):
        body = _ecu(1)
        dump = _kess(body)
        d = headers.detect(dump)
        self.assertEqual(d["header_len"], 0x400)
        self.assertEqual(d["tool"], "KESS")
        self.assertEqual(d["body"], body)
        self.assertEqual(d["body_size"], SIZE)

    def test_no_header_on_standard_size(self):
        body = _ecu(2)
        d = headers.detect(body)
        self.assertEqual(d["header_len"], 0)
        self.assertEqual(d["body"], body)

    def test_reattach(self):
        body = _ecu(3)
        dump = _kess(body)
        h, b, t = headers.split(dump)
        self.assertEqual(headers.reattach(h, b, t), dump)

    def test_sizes_compatible_with_header(self):
        ok, ratio = headers.sizes_compatible(SIZE, SIZE + 0x400)
        self.assertTrue(ok)
        self.assertGreaterEqual(ratio, 0.98)

    def test_align_to_strips_header(self):
        body = _ecu(4)
        aligned, info = headers.align_to(body, _kess(body))
        self.assertEqual(aligned, body)
        self.assertTrue(info.get("aligned"))


class MinHashV2Tests(unittest.TestCase):
    def test_padding_ignored(self):
        a = bytearray(32 * 1024)
        b = bytearray(32 * 1024)
        a[:512] = bytes((i * 7) % 256 for i in range(512))
        b[:512] = bytes((i * 11) % 256 for i in range(512))
        a[512:] = b"\xff" * (len(a) - 512)
        b[512:] = b"\xff" * (len(b) - 512)
        v1 = fingerprint.jaccard(
            fingerprint.minhash_signature(bytes(a), skip_padding=False),
            fingerprint.minhash_signature(bytes(b), skip_padding=False),
        )
        v2 = fingerprint.jaccard(
            fingerprint.minhash_signature(bytes(a), skip_padding=True),
            fingerprint.minhash_signature(bytes(b), skip_padding=True),
        )
        self.assertGreater(v1, v2)
        self.assertLess(v2, 0.5)

    def test_v1_row_still_matches(self):
        td = tempfile.mkdtemp()
        try:
            dbp = os.path.join(td, "t.db")
            db.init_db(dbp)
            raw = _ecu(11)
            mh_v1 = fingerprint.minhash_signature(raw, skip_padding=False)
            db.add_solution(dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
                            vehicle_label="Audi", solution_type="Stage 1",
                            stock_sha256="deadbeef", stock_size=len(raw),
                            minhash=mh_v1, minhash_ver=1)
            r = engine.match(raw, dbp)
            self.assertTrue(r["matches"])
            self.assertGreaterEqual(r["matches"][0]["jaccard"], 0.9)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_body_sha_is_exact_despite_header(self):
        td = tempfile.mkdtemp()
        try:
            dbp = os.path.join(td, "t.db")
            db.init_db(dbp)
            raw = _ecu(12)
            fp = fingerprint.fingerprint(raw)
            db.add_solution(dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
                            vehicle_label="Audi", solution_type="Stage 1",
                            stock_sha256=fp["sha256"], stock_size=fp["size"],
                            minhash=fp["minhash"], minhash_ver=2)
            r = engine.match(_kess(raw), dbp)
            v, rel = engine.portal_verdict(r)
            self.assertEqual(v, "compatible")
            self.assertTrue(rel[0]["exact"])
            self.assertEqual(r["incoming"]["header_len"], 0x400)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class ChecksumTests(unittest.TestCase):
    def test_complement_detected_and_corrected(self):
        raw = _with_ck(_ecu(21), mode="complement")
        broken = bytearray(raw)
        broken[-2] ^= 0xFF
        res = checksum.apply(bytes(broken), platform="EDC17CP14")
        self.assertEqual(res["status"], "corrige")
        self.assertTrue(res["ready"])
        self.assertGreaterEqual(res["blocks_corrected"], 1)
        again = checksum.apply(res["data"], platform="EDC17CP14")
        self.assertEqual(again["status"], "ok")
        self.assertTrue(again["ready"])

    def test_direct_mode(self):
        raw = _with_ck(_ecu(22), mode="direct")
        res = checksum.apply(raw, platform="EDC17CP14")
        self.assertIn(res["status"], ("ok", "corrige"))
        self.assertTrue(res["ready"])
        self.assertIn("direct", res["method"])

    def test_unknown_not_ready(self):
        raw = _rand_ecu("EDC17CP14")
        res = checksum.apply(raw, platform="EDC17CP14")
        self.assertEqual(res["status"], "inconnu")
        self.assertFalse(res["ready"])

    def test_non_bosch_not_applicable(self):
        raw = _rand_ecu("SIMOS18.1")
        res = checksum.apply(raw, platform="SIMOS18.1")
        self.assertEqual(res["status"], "non_applicable")
        self.assertFalse(res["ready"])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.dbp = os.path.join(self.td, "t.db")
        db.init_db(self.dbp)
        self.ori = os.path.join(self.td, "ori.bin")
        self.sol = os.path.join(self.td, "sol.bin")
        raw = _ecu(31)
        sol = bytearray(raw)
        sol[20000:20100] = b"\x11" * 100
        open(self.ori, "wb").write(raw)
        open(self.sol, "wb").write(sol)
        self.raw, self.soldata = raw, bytes(sol)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_add_copies_into_files(self):
        with open(os.path.join(self.td, "config.json"), "w") as fh:   # option « Copier les fichiers » cochée
            fh.write('{"copier_fichiers": true}')
        sid = db.add_solution(
            self.dbp, ecu_version="0281020088", ecu_platform="EDC17CP14",
            vehicle_label="Audi", solution_type="Stage 1",
            stock_sha256=fingerprint.sha256(self.raw), stock_size=len(self.raw),
            minhash=fingerprint.minhash_signature(self.raw),
            original_file=self.ori, solution_file=self.sol,
        )
        row = db.get_solution(self.dbp, sid)
        self.assertTrue(row["original_file"].endswith("original.bin")
                        or os.path.basename(row["original_file"]).startswith("original"))
        self.assertTrue(os.path.isfile(row["original_file"]))
        self.assertTrue(os.path.isfile(row["solution_file"]))
        folder = os.path.join(db.files_root(self.dbp), f"{sid:06d}")
        self.assertTrue(os.path.isdir(folder))
        import sqlite3
        raw_path = sqlite3.connect(self.dbp).execute(
            "SELECT original_file FROM solutions WHERE id=?", (sid,)
        ).fetchone()[0]
        self.assertFalse(os.path.isabs(raw_path), raw_path)
        self.assertTrue(raw_path.replace("\\", "/").startswith("files/"))

    def test_delete_removes_archive_not_source(self):
        sid = db.add_solution(
            self.dbp, vehicle_label="Audi", solution_type="Stage 1",
            stock_sha256="x", stock_size=len(self.raw),
            original_file=self.ori, solution_file=self.sol,
        )
        folder = os.path.join(db.files_root(self.dbp), f"{sid:06d}")
        db.delete_solution(self.dbp, sid)
        self.assertFalse(os.path.isdir(folder))
        self.assertTrue(os.path.isfile(self.ori))
        self.assertTrue(os.path.isfile(self.sol))

    def test_rebuild_fingerprints(self):
        raw = _ecu(32)
        open(self.ori, "wb").write(raw)
        sid = db.add_solution(
            self.dbp, vehicle_label="Audi", solution_type="Stage 1",
            stock_sha256="old", stock_size=len(raw),
            minhash=[1, 2, 3], minhash_ver=1,
            original_file=self.ori,
        )
        out = db.rebuild_fingerprints(self.dbp)
        self.assertEqual(out["updated"], 1)
        row = db.get_solution(self.dbp, sid)
        self.assertEqual(int(row["minhash_ver"]), 2)
        self.assertEqual(row["stock_sha256"], fingerprint.sha256(raw))


class PatchServerTests(unittest.TestCase):
    def setUp(self):
        self.orig = _ecu(41)
        self.sol = bytearray(self.orig)
        self.sol[20000:20400] = b"\x5a" * 400
        self.sol = bytes(self.sol)

    def test_propre_on_stock_client(self):
        ev = pmod.evaluate_patch(self.orig, self.orig, self.sol, "Stage 1")
        self.assertEqual(ev["verdict"], "propre")
        self.assertGreater(ev["matched"], 0)
        self.assertEqual(ev["mismatched"], 0)

    def test_header_client_still_propre(self):
        client = _kess(self.orig)
        ev = pmod.evaluate_patch(client, self.orig, self.sol, "Stage 1")
        self.assertNotIn("error", ev)
        self.assertEqual(ev["verdict"], "propre")
        self.assertTrue(ev.get("header_note"))

    def test_build_patched_reattaches_header(self):
        client = _kess(self.orig)
        res = pmod.build_patched(client, self.orig, self.sol,
                                 fiche_type="Stage 1", platform="EDC17CP14")
        self.assertNotIn("error", res)
        self.assertEqual(res["patched"][:4], b"KESS")
        self.assertEqual(len(res["patched"]), len(client))
        body = res["patched"][0x400:]
        self.assertEqual(body[20000:20400], b"\x5a" * 400)

    def test_incompatible_not_ready(self):
        client = bytearray(self.orig)
        client[20000:20100] = b"\x00" * 100
        res = pmod.build_patched(bytes(client), self.orig, self.sol, partial=False)
        self.assertEqual(res.get("error"), "zones_incompatibles")
        self.assertFalse(res.get("ready_to_flash"))

    def test_unknown_checksum_not_ready_to_flash(self):
        orig = _rand_ecu("EDC17CP14")
        sol = bytearray(orig)
        sol[20000:20400] = b"\x5a" * 400
        res = pmod.build_patched(orig, orig, bytes(sol),
                                 fiche_type="Stage 1", platform="EDC17CP14")
        self.assertEqual(res["verdict"], "propre")
        self.assertFalse(res["checksum"]["ready"])
        self.assertFalse(res["ready_to_flash"])
        self.assertEqual(res["checksum"]["status"], "inconnu")

    def test_ready_when_scheme_ok(self):
        orig = _with_ck(_ecu(42), mode="complement")
        sol = bytearray(orig)
        sol[20000:20100] = b"\x5a" * 100
        sol = _with_ck(bytes(sol), mode="complement")  # sol already correct
        # client = orig (checksums justes) ; après patch ils seront recalé
        res = pmod.build_patched(orig, orig, sol,
                                 fiche_type="Stage 1", platform="EDC17CP14")
        self.assertEqual(res["verdict"], "propre")
        self.assertTrue(res["checksum"]["ready"])
        self.assertTrue(res["ready_to_flash"])
        self.assertIn(res["checksum"]["status"], ("ok", "corrige"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
