"""Tests du catalogue fileservice — calcul du tarif le plus avantageux."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import catalogue as cat


class DevisTests(unittest.TestCase):
    def test_single(self):
        d = cat.devis("vl", ["stage1"])
        self.assertEqual(d["total"], 59)
        self.assertEqual(d["economie"], 0)

    def test_pack_applique_automatiquement(self):
        d = cat.devis("vl", ["e85", "stage1"])
        self.assertEqual(d["total"], 99)
        self.assertEqual(d["economie"], 19)
        self.assertEqual([l["nom"] for l in d["lignes"]], ["Pack E85 + débridage moteur"])

    def test_pack_plus_prestation_seule(self):
        d = cat.devis("vl", ["stage1", "speed", "e85"])
        self.assertEqual(d["total"], 99 + 29)

    def test_siege(self):
        self.assertEqual(cat.devis("vl", ["e85"], siege=True)["total"], 89)
        self.assertEqual(cat.devis("vl", ["e85", "stage1"], siege=True)["total"], 129)
        d = cat.devis("vl", ["stage1"], siege=True)   # sans objet : prix normal
        self.assertEqual(d["total"], 59)
        self.assertFalse(d["siege_possible"])

    def test_categories(self):
        self.assertEqual(cat.devis("pl", ["stage1"])["total"], 79)
        self.assertEqual(cat.devis("moto", ["speed"])["total"], 20)
        self.assertTrue(cat.devis("pl", ["e85"])["erreur"])  # pas d'E85 PL
        self.assertTrue(cat.devis("xx", ["stage1"])["erreur"])

    def test_services_partout(self):
        for c in ("vl", "pl", "moto"):
            self.assertEqual(cat.devis(c, ["clonage"])["total"], 40)

    def test_garantie(self):
        self.assertEqual(cat.devis("vl", ["stage1"], garantie="g2")["total"], 89)
        self.assertEqual(cat.devis("vl", [], garantie="g2")["total"], 0)

    def test_ordre_de_selection(self):
        d = cat.devis("pl", ["stage1", "dtc"])
        self.assertEqual([l["nom"] for l in d["lignes"]], ["Stage 1 – PL", "Suppression DTC – PL"])

    def test_doublons_ignores(self):
        self.assertEqual(cat.devis("vl", ["stage1", "stage1"])["total"], 59)

    def test_packs_credits(self):
        for p in cat.PACKS_CREDITS:
            self.assertAlmostEqual(p["prix_eur"], p["credits"] * cat.PRIX_CREDIT_EUR)


if __name__ == "__main__":
    unittest.main()
