"""Tests v1.60 — tarifs des prestations réglés depuis l'outil atelier (prix et retrait de l'offre uniquement)."""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import catalogue
from test_demandes import _base, _client


class TarifsCatalogueTests(unittest.TestCase):
    def tearDown(self):
        catalogue.appliquer_tarifs({})

    def test_prix_et_retrait(self):
        base = catalogue.devis("vl", ["stage1"])["total"]
        t = catalogue.normaliser_tarifs({"prestations": {"vl.stage1": {"prix": "69"}, "vl.e85": {"prix": 59}},
                                         "packs": {"vl.0": {"prix": 109}}, "masques": ["vl.torque", "inconnu"]})
        self.assertEqual(t, {"prestations": {"vl.stage1": {"prix": 69}}, "packs": {"vl.0": {"prix": 109}},
                             "masques": ["vl.torque"]})                         # valeurs d'origine non gardées
        catalogue.appliquer_tarifs({"tarifs": t})
        self.assertEqual(catalogue.devis("vl", ["stage1"])["total"], 69)
        self.assertEqual(catalogue.devis("vl", ["stage1", "e85"])["total"], 109)  # le pack suit son nouveau prix
        self.assertIn("indisponible", catalogue.devis("vl", ["torque"])["erreur"])
        catalogue.appliquer_tarifs({})
        self.assertEqual(catalogue.devis("vl", ["stage1"])["total"], base)
        self.assertEqual(len(catalogue.PRESTATIONS["vl"]), len(catalogue._DEFAUTS["PRESTATIONS"]["vl"]))

    def test_rien_d_autre_que_des_prix(self):
        t = catalogue.normaliser_tarifs({"prestations": {"vl.stage1": {"prix": 70, "nom": "Autre chose"},
                                                         "vl.nouvelle": {"prix": 10}},
                                         "nouvelles": [{"code": "x", "nom": "x", "prix": 1}]})
        self.assertEqual(t, {"prestations": {"vl.stage1": {"prix": 70}}})
        catalogue.appliquer_tarifs({"tarifs": t})
        self.assertEqual({p["code"] for p in catalogue.PRESTATIONS["vl"]} - {p["code"] for p in catalogue._DEFAUTS["PRESTATIONS"]["vl"]}, set())
        self.assertEqual(next(p for p in catalogue.PRESTATIONS["vl"] if p["code"] == "stage1")["nom"], "Stage 1")
        for mauvais in ("-5", "abc", 999999):
            with self.assertRaises(ValueError):
                catalogue.normaliser_tarifs({"prestations": {"vl.stage1": {"prix": mauvais}}})

    def test_nouvelles_prestations_et_packs(self):
        self.assertEqual(catalogue.devis("vl", ["e85", "startstop"])["total"], 79)
        self.assertEqual(catalogue.devis("vl", ["e85", "startstop"], siege=True)["total"], 109)
        self.assertEqual(catalogue.devis("vl", ["stage1", "popbang"])["total"], 89)
        self.assertEqual(catalogue.devis("vl", ["stage2", "boite", "launch", "rupteur"])["total"], 89 + 59 + 39 + 29)
        self.assertEqual(catalogue.devis("moto", ["popbang"])["total"], 29)
        import traductions
        for items in catalogue.PRESTATIONS.values():
            for p in items:
                self.assertIn(p["desc"], traductions.EN, p["desc"])

    def test_packs_credits_jamais_retires(self):
        n = len(catalogue.PACKS_CREDITS)
        catalogue.appliquer_tarifs({"tarifs": {"packs_credits": {"50": {"prix_eur": 120, "bonus": 2}}, "masques": ["50"]}})
        self.assertEqual(len(catalogue.PACKS_CREDITS), n)                     # l'ordre sert au paiement Stripe
        self.assertEqual(catalogue.PACKS_CREDITS[0]["prix_eur"], 120)
        self.assertEqual(catalogue.PACKS_CREDITS[0]["bonus"], 2)


class TarifsOutilTests(unittest.TestCase):
    def setUp(self):
        import app as outil
        import portal
        self.o, self.portal = outil, portal
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.cfg = os.path.join(t, "c.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        self.saved_o = (outil.PORTAL_CONFIG_PATH, outil.FS_DB)
        outil.PORTAL_CONFIG_PATH = self.cfg
        outil.FS_DB = _base(os.path.join(t))
        outil._fs_init()
        app = portal.app
        self.saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG", "FS_DETECT")}
        app.config.update(FS_DB=_base(t), FS_FILES=os.path.join(t, "f"), FS_DATA_DIR=t, FS_CONFIG=self.cfg, FS_DETECT=None)
        _client(app.config["FS_DB"])
        portal.fileservice.LIMITEUR._echecs.clear()

    def tearDown(self):
        self.o.PORTAL_CONFIG_PATH, self.o.FS_DB = self.saved_o
        self.portal.app.config.update(self.saved)
        catalogue.appliquer_tarifs({})
        self.tmp.cleanup()

    def test_atelier_regle_le_portail_applique(self):
        c = self.o.app.test_client()
        lignes = c.get("/fs/tarifs").get_json()["lignes"]
        self.assertTrue(any(l["cle"] == "vl.stage1" for l in lignes))
        r = c.post("/fs/tarifs", json={"tarifs": {"prestations": {"vl.stage1": {"prix": 75}}, "masques": ["clonage"]}})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(c.post("/fs/tarifs", json={"tarifs": {"prestations": {"vl.stage1": {"prix": -1}}}}).status_code, 400)
        with open(self.cfg, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["tarifs"]["prestations"]["vl.stage1"]["prix"], 75)
        catalogue.appliquer_tarifs({})                                          # autre processus : relit la config
        p = self.portal.app.test_client()
        p.post("/connexion", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', p.get("/connexion").get_data(as_text=True)).group(1),
                                   "email": "jean@garage.fr", "password": "motdepasse-solide"})
        page = p.get("/nouveau").get_data(as_text=True)
        self.assertNotIn("Clonage calculateur", page)
        tok = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        d = p.post("/tarif", json={"categorie": "vl", "prestations": ["stage1"]}, headers={"X-CSRF-Token": tok}).get_json()
        self.assertEqual(d["total"], 75)
        self.assertTrue(any(x["action"] == "Tarifs modifiés" for x in self.o.equipe.journal(self.o.FS_DB)))

    def test_reserve_aux_administrateurs(self):
        self.assertIn("fs_tarifs_set", self.o.ADMIN_ENDPOINTS)
        self.assertIn("fs_tarifs", self.o.ADMIN_ENDPOINTS)


if __name__ == "__main__":
    unittest.main()
