"""Tests v1.59 — outil en ligne : liste des solutions envoyée par le PC, fichiers demandés au PC à la volée."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import passerelle
from matcher import db as mdb
from test_demandes import _base
from test_v155 import bibliotheque


class FichiersServeurTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _base(self.tmp.name)
        passerelle.init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fichier_efface_apres_telechargement_ou_delai(self):
        t = self.tmp.name
        rid = passerelle.demander_fichier(self.db, 3, "solution", "fiche 3", 1, "Damien")
        self.assertEqual([f["id"] for f in passerelle.fichiers_en_attente(self.db)], [rid])
        passerelle.deposer_fichier(self.db, t, rid, "C:\\OneDrive\\sol.bin", b"abc", "PC")
        self.assertEqual(passerelle.fichiers_en_attente(self.db), [])
        self.assertEqual(passerelle.retirer_fichier(self.db, t, rid), ("sol.bin", b"abc"))
        self.assertEqual(os.listdir(os.path.join(t, "passerelle_tmp")), [])          # effacé du serveur
        self.assertIsNone(passerelle.retirer_fichier(self.db, t, rid))                # une seule fois
        # non téléchargé : effacé au bout de 10 minutes
        rid2 = passerelle.demander_fichier(self.db, 3)
        passerelle.deposer_fichier(self.db, t, rid2, "s.bin", b"x", "PC")
        passerelle.purger_fichiers(self.db, t, maintenant=time.time() + passerelle.DUREE_FICHIER + 1)
        self.assertEqual(passerelle.fichier_demande(self.db, rid2)["statut"], "expire")
        self.assertEqual(os.listdir(os.path.join(t, "passerelle_tmp")), [])

    def test_base_gzip_bornee(self):
        import gzip
        import io
        src = gzip.compress(b"\0" * 5000)
        dest = os.path.join(self.tmp.name, "b.db")
        passerelle.recevoir_base(io.BytesIO(src), dest)
        self.assertEqual(os.path.getsize(dest), 5000)
        with self.assertRaises(passerelle.ErreurCompte):
            passerelle.recevoir_base(io.BytesIO(src[:-12]), dest)                  # envoi tronqué


class BoutEnBoutTests(unittest.TestCase):
    """Un vrai serveur : l'outil « en ligne » (base vide, aucun fichier) et le PC (bibliothèque + fichiers)."""

    def setUp(self):
        import app as outil
        from werkzeug.serving import make_server
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR, outil.CONFIG_PATH, outil.DB_PATH)
        pc = os.path.join(t, "pc")
        os.makedirs(pc)
        self.pc_db, self.raw, self.sol = bibliotheque(pc)                        # côté PC uniquement
        serveur = os.path.join(t, "serveur")
        os.makedirs(serveur)
        outil.DB_PATH = os.path.join(serveur, "solutions.db")
        mdb.init_db(outil.DB_PATH)
        outil.FS_DB, outil.FS_FILES = _base(serveur), os.path.join(serveur, "fichiers")
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(serveur, "c.json"), serveur
        outil.CONFIG_PATH = os.path.join(t, "config_pc.json")
        outil._fs_init()
        outil.LIMITEUR_PASSERELLE._echecs.clear()
        self.srv = make_server("127.0.0.1", 0, outil.app, threaded=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        self.c = outil.app.test_client()
        self.cle = self.c.post("/fs/passerelle/creer", json={"nom": "PC atelier"}).get_json()["cle"]
        self.reglages = {"url": self.url, "cle": self.cle}
        self.a = passerelle.Automate(lambda: dict(self.reglages), self.pc_db, pc, None, None, None,
                                     chemin_fichier=outil._chemin_fichier_fiche)

    def tearDown(self):
        self.srv.shutdown()
        (self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR, self.o.CONFIG_PATH,
         self.o.DB_PATH) = self.saved
        self.tmp.cleanup()

    def test_liste_des_solutions_envoyee_sans_fichiers(self):
        self.assertEqual(self.c.get("/solutions").get_json()["db_size"], 0)
        self.assertEqual(self.a.synchroniser_base(), 1)
        d = self.c.get("/solutions").get_json()
        self.assertEqual(d["db_size"], 1)
        self.assertEqual(d["synchro"]["poste"], "PC atelier")
        self.assertIsNone(self.a.synchroniser_base())                               # rien de changé : rien d'envoyé
        self.assertIsNone(self.a.synchroniser_base(forcer=True))                    # même empreinte : pas renvoyée
        serveur = os.path.dirname(self.o.DB_PATH)
        for base, _, fichiers in os.walk(serveur):
            for f in fichiers:
                with open(os.path.join(base, f), "rb") as fh:
                    self.assertNotIn(self.sol[19990:20110], fh.read(), f)           # aucun .bin de la bibliothèque
        self.reglages["base"] = False
        mdb.add_solution(self.pc_db, vehicle_label="Golf", solution_type="Stage 1")
        self.assertIsNone(self.a.synchroniser_base())                               # option décochée
        self.reglages["base"] = True
        self.assertEqual(self.a.synchroniser_base(), 2)
        self.assertIn("synchronisée", json.dumps(self.o.equipe.journal(self.o.FS_DB), ensure_ascii=False))

    def test_fichier_demande_au_pc(self):
        self.a.synchroniser_base()
        sid = self.c.get("/solutions").get_json()["solutions"][0]["id"]
        self.assertIn('data-distants="1"', self.c.get("/").get_data(as_text=True))
        # l'envoi de la base vient de montrer que le PC est connecté
        r = self.c.post("/fs/passerelle/fichiers", json={"id": sid}).get_json()
        rid = r["id"]
        self.assertEqual(self.c.get(f"/fs/passerelle/fichiers/{rid}").get_json()["statut"], "attente")
        self.assertEqual(self.a.servir_fichiers(), [(rid, "envoyé")])
        self.assertEqual(self.c.get(f"/fs/passerelle/fichiers/{rid}").get_json()["statut"], "pret")
        dl = self.c.get(f"/fs/passerelle/fichiers/{rid}/telecharger")
        self.assertEqual(dl.data, self.sol)
        self.assertEqual(os.listdir(os.path.join(self.o.DATA_DIR, "passerelle_tmp")), [])
        dl.close()
        self.assertEqual(self.c.get(f"/fs/passerelle/fichiers/{rid}/telecharger").status_code, 410)
        # fichier absent du PC (OneDrive pas synchronisé…) : erreur claire
        os.remove(self.o._chemin_fichier_fiche(self.pc_db, sid, "solution"))
        rid = self.c.post("/fs/passerelle/fichiers", json={"id": sid}).get_json()["id"]
        self.assertEqual(self.a.servir_fichiers(), [(rid, "introuvable")])
        e = self.c.get(f"/fs/passerelle/fichiers/{rid}").get_json()
        self.assertEqual(e["statut"], "erreur")
        self.assertIn("introuvable sur le PC", e["message"])
        # option décochée sur le PC : il ne sert rien
        self.reglages["fichiers"] = False
        self.c.post("/fs/passerelle/fichiers", json={"id": sid})
        self.assertEqual(self.a.servir_fichiers(), [])

    def test_pc_pas_connecte(self):
        mdb.add_solution(self.o.DB_PATH, vehicle_label="Golf", solution_file="C:\\OneDrive\\x.bin")
        sid = self.c.get("/solutions").get_json()["solutions"][0]["id"]
        r = self.c.post("/fs/passerelle/fichiers", json={"id": sid})
        self.assertEqual(r.status_code, 409)
        self.assertIn("pas connecté", r.get_json()["error"])
        self.assertEqual(self.c.post("/fs/passerelle/fichiers", json={"id": 999}).status_code, 404)

    def test_seul_le_demandeur_telecharge(self):
        eq = self.o.equipe
        eq.creer(self.o.FS_DB, "damien", "Damien", "admin", "admin-solide-1")
        eq.creer(self.o.FS_DB, "paul", "Paul", "technicien", "tech-solide-12")
        tech = {t["identifiant"]: t for t in eq.lister(self.o.FS_DB)}
        rid = passerelle.demander_fichier(self.o.FS_DB, 1, demandeur_id=tech["damien"]["id"], demandeur="Damien")
        c = self.o.app.test_client()
        paul = tech["paul"]
        with c.session_transaction() as s:
            s["tech_id"] = paul["id"]
        self.assertEqual(c.get(f"/fs/passerelle/fichiers/{rid}").status_code, 403)

    def test_cle_obligatoire(self):
        c = self.o.app.test_client()
        self.assertEqual(c.get("/passerelle/v1/fichiers").status_code, 401)
        self.assertEqual(c.post("/passerelle/v1/base", data=b"x").status_code, 401)
        h = {"Authorization": "Bearer " + self.cle}
        r = c.post("/passerelle/v1/base", data=b"pas du gzip", headers=h)
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
