"""Tests v1.66 — détection du modèle de calculateur : familles ajoutées, références constructeurs, plateforme
reprise de la bibliothèque quand le fichier ne la contient pas."""
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

from matcher import db, engine, extract, fingerprint, metadata


def _bin(*chaines, graine=3):
    rng = random.Random(graine)
    b = bytearray(rng.getrandbits(8) for _ in range(65536))
    pos = 1000
    for c in chaines:
        b[pos:pos + len(c) + 2] = b"\x00" + c.encode() + b"\x00"
        pos += 3000
    return bytes(b)


class FamillesTests(unittest.TestCase):
    CAS = [
        (("VD56.1", "9678976480"), "VD56.1", "Valeo"),
        (("VD46.11 PSA",), "VD46.11", "Valeo"),
        (("MT80 SW",), "MT80", "Delphi"),
        (("ME17.9.21",), "ME17.9.21", "Bosch"),
        (("8GMK",), "8GMK", "Marelli"),
        (("A4E2 Perkins 1104D",), "A4E2", "Caterpillar/Perkins"),
        (("CM2250",), "CM2250", "Cummins"),
        (("CM850",), "CM850", "Cummins"),
        (("SIRIUS34",), "SIRIUS34", "Continental/Siemens"),
        (("PPD1.2",), "PPD1.2", "Continental/Siemens"),
        (("SID807EVO",), "SID807EVO", "Continental/Siemens"),
        (("EDC17C49",), "EDC17C49", "Bosch"),
    ]

    def test_plateformes(self):
        for chaines, plateforme, fabricant in self.CAS:
            r = extract.extract(_bin(*chaines))
            self.assertEqual((r["platform"], r["manufacturer"]), (plateforme, fabricant), chaines)

    def test_references(self):
        types = {c["value"]: c["type"] for c in extract.detect_candidates(_bin(
            "9678976480", "237101234R", "BV61-12A650-NB", "0261S12345", "28231014"))}
        self.assertEqual(types["9678976480"], "Référence PSA")
        self.assertEqual(types["237101234R"], "Référence Renault")
        self.assertEqual(types["BV61-12A650-NB"], "Référence Ford")
        self.assertEqual(types["0261S12345"], "Numéro Bosch (essence/diesel)")
        self.assertEqual(types["28231014"], "Référence Delphi")

    def test_mot_seul_pas_pris_pour_un_identifiant(self):
        valeurs = [c["value"] for c in extract.detect_candidates(_bin("A4E2 Perkins 1104D"))]
        self.assertNotIn("Perkins", valeurs)

    def test_chemins(self):
        for chemin, plateforme, fabricant in [
                (r"D:\CARTOS\Citroen VD56.1 REFERENCE2 C4\ori.bin", "VD56.1", "Valeo"),
                (r"D:\CARTOS\Manitou MT1840 Perkins A4E2\ori.bin", "A4E2", "Caterpillar/Perkins"),
                (r"D:\CARTOS\Case Puma CM2250\ori.bin", "CM2250", "Cummins"),
                (r"D:\CARTOS\Kia Ceed ME17.9.21\ori.bin", "ME17.9.21", "Bosch")]:
            m = metadata.parse(chemin)
            self.assertEqual((m["platform"], m["manufacturer"]), (plateforme, fabricant), chemin)
        self.assertIsNone(metadata.parse(r"D:\CARTOS\Manitou MT1335\ori.bin")["platform"])   # modèle d'engin, pas un ECU


class PlateformeBibliothequeTests(unittest.TestCase):
    def test_reprise_depuis_fiche_identique(self):
        with tempfile.TemporaryDirectory() as t:
            dbp = os.path.join(t, "solutions.db")
            db.init_db(dbp)
            data = _bin("1234567890", graine=8)                      # aucune plateforme lisible
            fp = fingerprint.fingerprint(data)
            db.add_solution(dbp, vehicle_label="Manitou", ecu_platform="EDC17CV41", stock_sha256=fp["sha256"],
                            stock_size=fp["size"], minhash=fp["minhash"], solution_type="Stage 1")
            inc = engine.match(data, dbp)["incoming"]
            self.assertIsNone(inc["platform"])
            self.assertEqual(inc["platform_bibliotheque"], "EDC17CV41")
            self.assertEqual(inc["manufacturer_bibliotheque"], "Bosch")
            # fichier inconnu : rien d'inventé
            self.assertIsNone(engine.match(_bin("9999999999", graine=9), dbp)["incoming"]["platform_bibliotheque"])


if __name__ == "__main__":
    unittest.main()
