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


class FichiersReelsTests(unittest.TestCase):
    """Cas relevés sur de vrais fichiers (chaînes reproduites, fichiers clients non versionnés)."""

    def _avec(self, *morceaux):
        rng = random.Random(11)
        b = bytearray(rng.getrandbits(8) for _ in range(131072))
        pos = 0x1000
        for m in morceaux:
            b[pos:pos + len(m)] = m
            pos += 0x4000
        return bytes(b)

    def test_bosch_bloc_identification(self):
        # Smart ForTwo ME17.9.20 : la ligne socle « ME(D)/EDC17 » ne doit pas faire croire à un EDC17
        data = self._avec(b"\x00ME(D)/EDC17 SB_V18.00.02/1782\x00", b"\x0039/1/ME17_9_20/15/P_1220//r1780_8E0_///\x00",
                          b"\x0010SW0566031220_8E0\x00")
        r = extract.extract(data)
        self.assertEqual((r["platform"], r["manufacturer"]), ("ME17.9.20", "Bosch"))
        self.assertEqual(r["best_ecu_version"], "10SW0566031220")
        self.assertEqual(r["typed_candidates"][0]["type"], "Logiciel Bosch (10SW)")
        r = extract.extract(self._avec(b"\x0034/1/EDC17C46/3/P1135//\x00"))
        self.assertEqual(r["platform"], "EDC17C46")

    def test_valeo_vd56(self):
        data = self._avec(b"J`VX56_L_29_07-6M     \x00", b"66666FBL P2017 (c) 2014 Valeo\x00")
        r = extract.extract(data)
        self.assertEqual((r["platform"], r["manufacturer"], r["best_ecu_version"]), ("VD56", "Valeo", "VX56_L_29_07-6M"))
        inc = engine.analyze(data, r"PEUGEOT_308_1.2_PURETECH_VD56.1_VALEO_ORI.mpc")
        self.assertEqual((inc["platform"], inc["platform_confirmed"]), ("VD56.1", True))   # version exacte du nom

    def test_octets_de_code_et_fragments(self):
        # « ME9 » entouré d'octets binaires, « 2dXRMHD » / « tH4FyG » : du code, pas un calculateur ni une référence
        r = extract.extract(self._avec(b"\x83\nME9\x1fw\xfa", b"\x002dXRMHD\x00", b"\x00tH4FyG\x00"))
        self.assertIsNone(r["platform"])
        self.assertEqual(r["best_ecu_version"], "")
        self.assertNotIn("2dXRMHD", r["candidate_ids"])


class FichiersAgricolesTests(FichiersReelsTests):
    """Claas (Bosch MD1 / EDC17, John Deere Phoenix), John Deere, Kubota (Denso) — chaînes relevées sur de vrais fichiers."""

    def test_bosch_md1_variante(self):
        data = self._avec(b"\x0049/1/MD1CE101_C1/242/P1603//P1603_MD1CE101_456///\x00", b"\x005802247472\x00")
        r = extract.extract(data)
        self.assertEqual((r["platform"], r["manufacturer"]), ("MD1CE101", "Bosch"))       # C1 = variante, pas le modèle
        self.assertEqual(r["best_ecu_version"], "5802247472")
        self.assertEqual(r["typed_candidates"][0]["type"], "Référence CNH / FPT")
        inc = engine.analyze(data, r"CLAAS_AXION-800_6.7L_BOSCH_MD1CE101_ORI.dec")
        self.assertTrue(inc["platform_confirmed"])

    def test_john_deere(self):
        bloc = b"\x004045HL555\x00\x00\x00CD4045U123686\x00\x00\x00SW64870F\x00\x00\x00\x00\x00\x00\x00\x00RE590386\x00"
        r = extract.extract(self._avec(bloc))
        self.assertEqual((r["manufacturer"], r["best_ecu_version"]), ("John Deere", "SW64870F"))
        types = {c["value"]: c["type"] for c in r["typed_candidates"]}
        self.assertEqual(types["RE590386"], "Référence John Deere")
        self.assertEqual(types["4045HL555"], "Moteur John Deere")
        m = metadata.parse(r"D:\CARTOS\CLAAS_ARION-540_4.5L_PHOENIX_L23_ORI.cod.dec")
        self.assertEqual((m["platform"], m["manufacturer"]), ("PHOENIX L23", "John Deere"))

    def test_denso_logiciel(self):
        data = self._avec(b"\x00\x00R5E72546R\x00\t\tR5F72546R\x00",
                          b"NS0HKB42A68MA-00008             \x15\x04\x10Copr.DENSO20150\xff\xff")
        r = extract.extract(data)
        self.assertEqual((r["manufacturer"], r["best_ecu_version"]), ("Denso", "NS0HKB42A68MA-00008"))
        self.assertEqual(r["typed_candidates"][0]["type"], "Logiciel Denso")


class FichiersTPTests(FichiersReelsTests):
    """Liebherr R914 (Deutz, EDC17CV52) et Yanmar SV100 (EDC17CV54)."""

    def test_nom_avec_deux_modeles(self):
        data = self._avec(b"\x00RB EDC17CV52\x00", b"\x0055/1/EDC17CV52_DSample/10/P_1204//P_1204_290_290_001///\x00")
        inc = engine.analyze(data, r"LIEBHERR_R914-EDC17CV54_DIESEL_BOSCH_EDC17CV52_ORI.mpc")
        self.assertEqual((inc["platform"], inc["platform_confirmed"]), ("EDC17CV52", True))   # le fichier fait foi

    def test_yanmar(self):
        data = self._avec(b"\x0046/1/EDC17CV54_CSample/973/P_950//P_950_344///\x00", b"\x00EDC17C04 BASE_ECU_EDC17 xx xx\x00",
                          b"\x00129E30-7401400\x00", b"1037516806")
        r = extract.extract(data)
        self.assertEqual((r["platform"], r["best_ecu_version"]), ("EDC17CV54", "1037516806"))
        self.assertIn("Référence Yanmar", {c["type"] for c in r["typed_candidates"]})
