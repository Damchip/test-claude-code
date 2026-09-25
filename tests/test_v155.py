"""Tests v1.55 — livraison en un clic / automatique."""
import io
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import comptes
import demandes
import livraison_auto
from matcher import db as mdb, fingerprint
from test_demandes import _base, _client, _creer
from test_v134 import _ecu


def bibliotheque(tmp, types=("Stage 1",)):
    """Bibliothèque de solutions avec une fiche par type, même stock."""
    dbp = os.path.join(tmp, "solutions.db")
    mdb.init_db(dbp)
    raw = _ecu(31)
    sol = bytearray(raw)
    sol[20000:20100] = b"\x11" * 100
    o, s = os.path.join(tmp, "o.bin"), os.path.join(tmp, "s.bin")
    with open(o, "wb") as fh:
        fh.write(raw)
    with open(s, "wb") as fh:
        fh.write(sol)
    fp = fingerprint.fingerprint(raw)
    for typ in types:
        mdb.add_solution(dbp, ecu_version="0281020088", ecu_platform="EDC17CP14", vehicle_label="Audi A3 " + typ,
                         solution_type=typ, stock_sha256=fp["sha256"], stock_size=fp["size"],
                         minhash=fp["minhash"], minhash_ver=2, original_file=o, solution_file=s)
    return dbp, raw, bytes(sol)


def demande(prestations, **kw):
    d = {"categorie": "vl", "prestations": list(prestations), "siege": 0, "fichier_nom": "ori.bin",
         "numero": "F-00001", "societe": "Garage"}
    d.update(kw)
    return d


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_propre_et_pret(self):
        dbp, raw, sol = bibliotheque(self.tmp.name)
        p = livraison_auto.preparer(demande(["stage1"]), raw, dbp)
        self.assertTrue(p["ok"], p.get("raison"))
        self.assertEqual(p["patched"][20000:20100], b"\x11" * 100)
        self.assertEqual(p["compte_rendu"]["types"], ["Stage 1"])

    def test_jamais_de_prestation_en_trop(self):
        dbp, raw, _ = bibliotheque(self.tmp.name, types=("Stage 1 + DPF/FAP off",))
        p = livraison_auto.preparer(demande(["stage1"]), raw, dbp)
        self.assertFalse(p["ok"])
        self.assertIn("Pas de fiche", p["raison"])

    def test_refus(self):
        dbp, raw, _ = bibliotheque(self.tmp.name)
        self.assertIn("à la main", livraison_auto.preparer(demande(["immo"]), raw, dbp)["raison"])
        self.assertIn("siège", livraison_auto.preparer(demande(["stage1"], siege=1), raw, dbp)["raison"])
        self.assertFalse(livraison_auto.preparer(demande(["stage1", "e85"]), raw, dbp)["ok"])
        modifie = bytearray(raw)
        modifie[20050] ^= 0xFF              # le client ne correspond plus au stock dans la zone
        self.assertFalse(livraison_auto.preparer(demande(["stage1"]), bytes(modifie), dbp)["ok"])


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.sol_db, self.raw, self.sol = bibliotheque(t)
        self.fs_db = _base(t)
        self.files = os.path.join(t, "fichiers")
        self.cid = _client(self.fs_db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_un_clic_dans_l_outil(self):
        import app as outil
        saved = (outil.FS_DB, outil.FS_FILES, outil.DB_PATH, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR)
        outil.FS_DB, outil.FS_FILES, outil.DB_PATH = self.fs_db, self.files, self.sol_db
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(self.tmp.name, "c.json"), self.tmp.name
        try:
            did = _creer(self.fs_db, self.files, self.cid, contenu=self.raw)
            c = outil.app.test_client()
            p = c.post(f"/fs/demandes/{did}/preparer", json={}).get_json()
            self.assertTrue(p["ok"], p)
            self.assertNotIn("patched", p)
            self.assertEqual(demandes.get(self.fs_db, did)["statut"], "recu")   # étape 1 : rien d'écrit
            r = c.post(f"/fs/demandes/{did}/livrer-auto", json={"auteur": "Thomas"}).get_json()
            self.assertEqual(r["version"], 1)
            self.assertEqual(demandes.get(self.fs_db, did)["statut"], "pret")
            self.assertEqual(c.get(f"/fs/demandes/{did}/livre/1").data[20000:20100], b"\x11" * 100)
            did2 = _creer(self.fs_db, self.files, self.cid, prestations=["immo"], contenu=self.raw)
            self.assertEqual(c.post(f"/fs/demandes/{did2}/livrer-auto", json={}).status_code, 400)
        finally:
            outil.FS_DB, outil.FS_FILES, outil.DB_PATH, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = saved

    def test_automatique_a_la_reception(self):
        import portal
        cfg = os.path.join(self.tmp.name, "portal_config.json")
        app = portal.app
        saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG", "FS_SOLUTIONS_DB", "FS_DETECT")}
        app.config.update(FS_DB=self.fs_db, FS_FILES=self.files, FS_DATA_DIR=self.tmp.name, FS_CONFIG=cfg,
                          FS_SOLUTIONS_DB=self.sol_db, FS_DETECT=None)
        try:
            portal.fileservice.LIMITEUR._echecs.clear()
            c = app.test_client()
            tok = lambda p: re.search(r'name="csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)
            c.post("/connexion", data={"csrf": tok("/connexion"), "email": "jean@garage.fr", "password": "motdepasse-solide"})

            def envoyer():
                return c.post("/nouveau", data={"csrf": tok("/nouveau"), "categorie": "vl", "prestas": ["stage1"],
                                                "file": (io.BytesIO(self.raw), "ori.bin")},
                              content_type="multipart/form-data")

            envoyer()                                                   # option désactivée : file normale
            self.assertEqual(demandes.lister(self.fs_db, self.cid)[0]["statut"], "recu")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"livraison_auto": True}, fh)
            r = envoyer()
            d = demandes.lister(self.fs_db, self.cid)[0]
            self.assertEqual(d["statut"], "pret")
            self.assertIn("déjà prêt", c.get(r.headers["Location"]).get_data(as_text=True))
        finally:
            app.config.update(saved)


if __name__ == "__main__":
    unittest.main()


class EquipeTests(unittest.TestCase):
    def setUp(self):
        import app as outil
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR)
        outil.FS_DB = _base(self.tmp.name)
        outil.FS_FILES = os.path.join(self.tmp.name, "fichiers")
        outil.PORTAL_CONFIG_PATH = os.path.join(self.tmp.name, "c.json")
        outil.DATA_DIR = self.tmp.name
        outil.LIMITEUR_OUTIL._echecs.clear()
        self.cid = _client(outil.FS_DB)
        self.did = _creer(outil.FS_DB, outil.FS_FILES, self.cid)

    def tearDown(self):
        self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR = self.saved
        self.tmp.cleanup()

    def _login(self, c, ident, mdp):
        return c.post("/login", data={"identifiant": ident, "password": mdp})

    def test_parcours(self):
        c = self.o.app.test_client()
        self.assertEqual(c.get("/fs/demandes").status_code, 200)          # aucun compte : accès d'origine
        r = c.post("/equipe/creer", json={"identifiant": "damien", "nom": "Damien", "role": "technicien",
                                          "mdp": "admin-solide-1"}).get_json()
        self.assertTrue(r["premier"])
        self.assertEqual(c.get("/equipe").get_json()["moi"]["role"], "admin")   # 1er compte = admin, connecté
        c.post("/equipe/creer", json={"identifiant": "thomas", "nom": "Thomas", "role": "technicien", "mdp": "tech-solide-12"})

        anon = self.o.app.test_client()
        self.assertEqual(anon.get("/fs/demandes").status_code, 401)        # désormais identifiant obligatoire
        self.assertIn("Identifiant", anon.get("/login").get_data(as_text=True))
        self.assertIn("incorrect", self._login(anon, "thomas", "faux-mot-de-passe").get_data(as_text=True))

        t = self.o.app.test_client()
        self.assertEqual(self._login(t, "Thomas", "tech-solide-12").status_code, 302)
        r = t.post(f"/fs/demandes/{self.did}/message", data={"texte": "Bonjour", "auteur": "Usurpateur"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(demandes.messages(self.o.FS_DB, self.did)[0]["auteur_nom"], "Thomas")   # signature imposée
        for url, body in (("/clients/credits", {"id": self.cid, "montant": "100"}),
                          ("/clients/facture", {"id": self.cid, "credits": 1, "ht": 1}),
                          ("/fs/reglages", {}), ("/equipe/creer", {"identifiant": "x"})):
            self.assertEqual(t.post(url, json=body).status_code, 403, url)
        self.assertEqual(t.post("/clients/statut", json={"id": self.cid, "statut": "actif"}).status_code, 200)

        j = c.get("/equipe/journal").get_json()["journal"]
        self.assertTrue(any(x["action"] == "Message au client" and x["technicien"] == "Thomas"
                            and x["cible"].startswith("F-") for x in j))
        admin_id = next(x["id"] for x in c.get("/equipe").get_json()["techniciens"] if x["identifiant"] == "damien")
        r = c.post("/equipe/modifier", json={"id": admin_id, "nom": "Damien", "role": "technicien", "actif": True})
        self.assertIn("au moins un administrateur", r.get_json()["error"])

    def test_force_brute(self):
        c = self.o.app.test_client()
        c.post("/equipe/creer", json={"identifiant": "damien", "nom": "Damien", "mdp": "admin-solide-1"})
        a = self.o.app.test_client()
        for _ in range(8):
            self._login(a, "damien", "mauvais")
        self.assertIn("Trop de tentatives", self._login(a, "damien", "admin-solide-1").get_data(as_text=True))


class RemisesTests(unittest.TestCase):
    def test_devis_avec_remise(self):
        import catalogue
        d = catalogue.devis("vl", ["stage1", "e85"], garantie="g1", remise=10, niveau="Partenaire")
        self.assertEqual(d["remise"], 10)                          # 10 % de 99, arrondi
        self.assertEqual(d["total"], 99 - 10 + 20)                 # la garantie n'est pas remisée
        self.assertIn("Remise Partenaire −10 %", [l["nom"] for l in d["lignes"]])
        self.assertEqual(catalogue.devis("vl", ["stage1"], remise=500)["total"], 59 - 53)   # plafonnée à 90 %
        self.assertEqual(catalogue.remises({})["VIP"], 20)
        self.assertEqual(catalogue.remises({"remises": {"Revendeur": "15", "X": "abc"}}), {"Revendeur": 15.0})

    def test_niveau_applique_a_l_envoi(self):
        with tempfile.TemporaryDirectory() as t:
            db = _base(t)
            cid = _client(db)
            did = demandes.creer(db, os.path.join(t, "f"), cid, categorie="vl", prestations=["stage1"],
                                 fichier_nom="a.bin", contenu=b"x", remise=20, niveau="VIP")
            self.assertEqual(demandes.get(db, did)["total"], 47)
            self.assertEqual(comptes.get_client(db, cid)["credits"], 200 - 47)
            self.assertNotIn("Remise", comptes.mouvements(db, cid)[0]["libelle"])
