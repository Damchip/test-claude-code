"""Tests v1.40 — file atelier, nettoyage, versions ECU."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from matcher import atelier, db, extract, fingerprint


SIZE = 32 * 1024


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


class PickEcuTests(unittest.TestCase):
    def test_bosch_beats_generic(self):
        info = extract.extract(_ecu(3))
        v = extract.pick_ecu_version(info["typed_candidates"])
        self.assertEqual(v, "0281020088")

    def test_generic_rejected(self):
        data = bytearray(4096)
        s = b"123456"
        data[100:100 + len(s)] = s
        v = extract.pick_ecu_version(extract.extract(bytes(data))["typed_candidates"])
        self.assertEqual(v, "")


class AtelierTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.dbp = os.path.join(self.td, "t.db")
        db.init_db(self.dbp)
        self.raw = _ecu(9)
        self.ori = os.path.join(self.td, "ORI.bin")
        open(self.ori, "wb").write(self.raw)

    def test_sync_fills_fp_and_ecu(self):
        sid = db.add_solution(
            self.dbp, vehicle_label="Audi A3", solution_type="Stage 1",
            stock_sha256="old", stock_size=len(self.raw),
            minhash=[1], minhash_ver=1, original_file=self.ori,
        )
        out = atelier.sync_batch(self.dbp, limit=12)
        self.assertEqual(out["fingerprints"], 1, out)
        self.assertEqual(out["remaining"], 0)
        row = db.get_solution(self.dbp, sid)
        self.assertEqual(int(row["minhash_ver"]), 2)
        self.assertEqual(row["stock_sha256"], fingerprint.sha256(self.raw))
        self.assertEqual(row["ecu_version"], "0281020088")
        self.assertEqual(row["ecu_platform"], "EDC17CP14")

    def test_resume(self):
        a = os.path.join(self.td, "a.bin"); open(a, "wb").write(_ecu(1))
        b = os.path.join(self.td, "b.bin"); open(b, "wb").write(_ecu(2))
        db.add_solution(self.dbp, vehicle_label="A", original_file=a,
                        stock_sha256="a", stock_size=SIZE, minhash=[1], minhash_ver=1)
        db.add_solution(self.dbp, vehicle_label="B", original_file=b,
                        stock_sha256="b", stock_size=SIZE, minhash=[1], minhash_ver=1)
        first = atelier.sync_batch(self.dbp, limit=1)
        self.assertEqual(first["processed"], 1)
        self.assertEqual(first["remaining"], 1)
        second = atelier.sync_batch(self.dbp, limit=12)
        self.assertEqual(second["remaining"], 0)

    def test_does_not_overwrite_ecu(self):
        sid = db.add_solution(
            self.dbp, vehicle_label="Audi", ecu_version="MANUAL123",
            stock_sha256="x", stock_size=len(self.raw),
            minhash=[1], minhash_ver=1, original_file=self.ori,
        )
        atelier.sync_batch(self.dbp, limit=12)
        row = db.get_solution(self.dbp, sid)
        self.assertEqual(row["ecu_version"], "MANUAL123")

    def test_purge_junk(self):
        db.add_solution(self.dbp, vehicle_label=".cache",
                        stock_sha256="j", stock_size=1, minhash=[1])
        db.add_solution(self.dbp, vehicle_label="Audi A3",
                        stock_sha256="ok", stock_size=1, minhash=[1])
        dry = atelier.purge_junk(self.dbp, apply=False)
        self.assertEqual(dry["count"], 1)
        self.assertEqual(db.count(self.dbp), 2)
        live = atelier.purge_junk(self.dbp, apply=True)
        self.assertEqual(live["deleted"], 1)
        self.assertEqual(db.count(self.dbp), 1)

    def test_merge_duplicates(self):
        db.add_solution(self.dbp, vehicle_label="Audi", solution_type="Stage 1",
                        stock_sha256="same", stock_size=10, minhash=[1],
                        original_file=self.ori)
        db.add_solution(self.dbp, vehicle_label="Audi", solution_type="Stage 1",
                        stock_sha256="same", stock_size=10, minhash=[1])
        dry = atelier.merge_duplicates(self.dbp, apply=False)
        self.assertEqual(dry["removed"], 1)
        self.assertEqual(db.count(self.dbp), 2)
        live = atelier.merge_duplicates(self.dbp, apply=True)
        self.assertEqual(live["removed"], 1)
        self.assertEqual(db.count(self.dbp), 1)

    def test_link_missing_solution(self):
        folder = os.path.join(self.td, "Panda")
        os.makedirs(folder)
        ori = os.path.join(folder, "ORIGINE-panda.bin")
        sol = os.path.join(folder, "E85France-panda.bin")
        open(ori, "wb").write(_ecu(4))
        open(sol, "wb").write(_ecu(5))
        sid = db.add_solution(
            self.dbp, vehicle_label="Panda", solution_type="E85 / Flexfuel",
            stock_sha256="p", stock_size=SIZE, minhash=[1],
            original_file=ori, solution_file="",
        )
        conn = db._connect(self.dbp)
        conn.execute("UPDATE solutions SET original_file=?, solution_file='' WHERE id=?",
                     (ori, sid))
        conn.commit(); conn.close()
        dry = atelier.link_missing_solutions(self.dbp, apply=False)
        self.assertEqual(dry["linked"], 1)
        live = atelier.link_missing_solutions(self.dbp, apply=True)
        self.assertEqual(live["linked"], 1)
        row = db.get_solution(self.dbp, sid)
        self.assertTrue(os.path.isfile(row["solution_file"]))
        self.assertTrue((row["solution_file"] or "").strip())


if __name__ == "__main__":
    unittest.main()
