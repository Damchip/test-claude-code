"""Tests v1.58 — passerelle PC atelier ↔ fileservice en ligne (bibliothèque et fichiers restés sur le PC)."""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import comptes
import demandes
import equipe
import livraison_auto
import passerelle
from test_demandes import _base, _client, _creer
from test_v155 import bibliotheque


class ClesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _base(self.tmp.name)
        passerelle.init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cycle_de_vie(self):
        cle = passerelle.creer_cle(self.db, "PC atelier")
        self.assertTrue(cle.startswith("e85pc_"))
        with comptes.connect(self.db) as con:
            self.assertNotIn(cle, json.dumps([dict(r) for r in con.execute("SELECT * FROM passerelle_cles")]))  # empreinte seule
        self.assertEqual(passerelle.verifier_cle(self.db, cle, "1.2.3.4")["nom"], "PC atelier")
        self.assertEqual(passerelle.lister_cles(self.db)[0]["derniere_ip"], "1.2.3.4")
        self.assertIsNone(passerelle.verifier_cle(self.db, cle + "x"))
        self.assertIsNone(passerelle.verifier_cle(self.db, "autre"))
        passerelle.revoquer_cle(self.db, passerelle.lister_cles(self.db)[0]["id"])
        self.assertIsNone(passerelle.verifier_cle(self.db, cle))
        for i in range(passerelle.MAX_CLES):
            passerelle.creer_cle(self.db, f"PC {i}")
        with self.assertRaises(comptes.ErreurCompte):
            passerelle.creer_cle(self.db, "de trop")

    def test_client_refuse_le_clair(self):
        with self.assertRaises(passerelle.ErreurPasserelle):
            passerelle.Client("http://atelier.exemple.fr", "e85pc_x")          # clé en clair sur internet : non
        with self.assertRaises(passerelle.ErreurPasserelle):
            passerelle.Client("https://atelier.exemple.fr", "mauvaise")
        passerelle.Client("https://atelier.exemple.fr", "e85pc_x")
        passerelle.Client("http://127.0.0.1:5000", "e85pc_x")


class BoutEnBoutTests(unittest.TestCase):
    """Un vrai serveur HTTP : l'outil « en ligne » (demandes) et l'outil « PC » (bibliothèque avec fichiers)."""

    def setUp(self):
        import app as outil
        from werkzeug.serving import make_server
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR, outil.CONFIG_PATH, outil.DB_PATH)
        self.sol_db, self.raw, self.sol = bibliotheque(t)            # bibliothèque + .bin : côté PC
        outil.DB_PATH = self.sol_db
        outil.FS_DB, outil.FS_FILES = _base(t), os.path.join(t, "fichiers")
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(t, "c.json"), t
        outil.CONFIG_PATH = os.path.join(t, "config.json")
        with open(outil.CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"secret_key": "x"}, fh)
        outil._fs_init()
        outil.LIMITEUR_PASSERELLE._echecs.clear()
        self.cid = _client(outil.FS_DB)
        self.did = _creer(outil.FS_DB, outil.FS_FILES, self.cid, contenu=self.raw)
        self.srv = make_server("127.0.0.1", 0, outil.app, threaded=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        self.c = outil.app.test_client()
        self.cle = self.c.post("/fs/passerelle/creer", json={"nom": "PC atelier"}).get_json()["cle"]

    def tearDown(self):
        self.srv.shutdown()
        (self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR, self.o.CONFIG_PATH,
         self.o.DB_PATH) = self.saved
        self.tmp.cleanup()

    def test_api_serveur(self):
        c = self.o.app.test_client()
        self.assertEqual(c.get("/passerelle/v1/etat").status_code, 401)
        self.assertEqual(c.get("/passerelle/v1/etat", headers={"Authorization": "Bearer e85pc_faux"}).status_code, 401)
        h = {"Authorization": "Bearer " + self.cle}
        self.assertEqual(c.get("/passerelle/v1/etat", headers=h).get_json()["poste"], "PC atelier")
        lst = c.get("/passerelle/v1/demandes", headers=h).get_json()["demandes"]
        self.assertEqual(len(lst), 1)
        self.assertNotIn("email", lst[0])                                   # pas de données personnelles inutiles
        self.assertNotIn("tel", lst[0])
        r = c.get(f"/passerelle/v1/demandes/{self.did}/original", headers=h)
        self.assertEqual(r.data, self.raw)
        r.close()
        # la clé ne donne accès qu'à la passerelle, pas au reste de l'outil ni aux réglages
        self.o.equipe.creer(self.o.FS_DB, "damien", "Damien", "admin", "admin-solide-1")
        self.assertEqual(c.get("/fs/demandes", headers=h).status_code, 401)
        self.assertEqual(c.post("/fs/passerelle/creer", json={"nom": "x"}, headers=h).status_code, 401)

    def test_pc_prepare_et_livre(self):
        pc = self.o.app.test_client()
        self.assertEqual(pc.post("/enligne/reglages", json={"url": "http://atelier.exemple.fr", "cle": self.cle}).status_code, 400)
        self.assertTrue(pc.post("/enligne/reglages", json={"url": self.url, "cle": self.cle}).get_json()["ok"])
        self.assertEqual(pc.post("/enligne/test", json={}).get_json()["a_traiter"], 1)
        self.assertNotIn(self.cle, json.dumps(pc.get("/enligne/reglages").get_json()))   # jamais renvoyée au navigateur
        d = pc.get("/enligne/demandes").get_json()["demandes"][0]
        self.assertEqual(d["numero"], "F-00001")
        p = pc.post(f"/enligne/demandes/{self.did}/preparer", json={}).get_json()
        self.assertTrue(p["ok"], p)
        self.assertEqual(demandes.get(self.o.FS_DB, self.did)["statut"], "recu")      # préparer n'écrit rien
        r = pc.post(f"/enligne/demandes/{self.did}/livrer-auto", json={}).get_json()
        self.assertTrue(r["ok"], r)
        d = demandes.get(self.o.FS_DB, self.did)
        self.assertEqual(d["statut"], "pret")
        liv = demandes.livrables(self.o.FS_DB, self.did)[0]
        with open(os.path.join(self.o.FS_FILES, str(self.did), liv["fichier"]), "rb") as fh:
            self.assertEqual(fh.read()[20000:20100], b"\x11" * 100)                 # patch fait avec la bibliothèque du PC
        j = equipe.journal(self.o.FS_DB)
        self.assertTrue(any(x["action"] == "Livraison (PC atelier)" and x["technicien"] == "PC · PC atelier" for x in j))
        # livraison d'un fichier préparé à la main (nouvelle version)
        r = pc.post(f"/enligne/demandes/{self.did}/livrer", data={"file": (io.BytesIO(b"\x05" * 64), "main.bin"), "note": "retouche"},
                    content_type="multipart/form-data").get_json()
        self.assertEqual(r["version"], 2)
        orig = pc.get(f"/enligne/demandes/{self.did}/original")
        self.assertEqual(orig.data, self.raw)
        orig.close()

    def test_livraison_automatique_du_pc(self):
        with open(self.o.CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"secret_key": "x", "passerelle": {"url": self.url, "cle": self.cle, "auto": True}}, fh)
        a = passerelle.Automate(self.o._enligne_reglages, self.o.DB_PATH, self.o.DATA_DIR, livraison_auto.preparer,
                                livraison_auto.nom_fichier, livraison_auto.journaliser)
        did2 = _creer(self.o.FS_DB, self.o.FS_FILES, self.cid, prestations=["immo"], contenu=self.raw)   # à la main
        faits = dict(a.tour())
        self.assertEqual(faits["F-00001"], "livrée")
        self.assertIn("à la main", faits["F-00002"])
        self.assertEqual(demandes.get(self.o.FS_DB, self.did)["statut"], "pret")
        self.assertEqual(demandes.get(self.o.FS_DB, did2)["statut"], "recu")
        self.assertEqual(a.tour(), [])                                                # chaque demande tentée une fois

    def test_serveur_arrete(self):
        pc = self.o.app.test_client()
        pc.post("/enligne/reglages", json={"url": "http://127.0.0.1:9", "cle": self.cle})
        r = pc.get("/enligne/demandes")
        self.assertEqual(r.status_code, 400)
        self.assertIn("injoignable", r.get_json()["error"])


class ProductionTests(unittest.TestCase):
    def test_onglet_masque_en_ligne(self):
        import app as outil
        saved = outil.PROD
        try:
            outil.PROD = False
            with mock.patch.object(outil, "_equipe_active", return_value=False):
                self.assertIn('data-view="enligne"', outil.app.test_client().get("/").get_data(as_text=True))
        finally:
            outil.PROD = saved


if __name__ == "__main__":
    unittest.main()
