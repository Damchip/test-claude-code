"""Tests des comptes clients du fileservice (module + parcours dans le portail)."""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import comptes
import demandes
import factures

SIRET_OK = "73282932000074"


class ComptesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "fs.db")
        comptes.init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _creer(self, **kw):
        data = dict(societe="Garage Test", siret=SIRET_OK, tva="FR 12 345678901", contact="Jean Test",
                    email="Jean@Garage.fr", tel="0600000000", mdp="motdepasse-solide")
        data.update(kw)
        return comptes.creer_client(self.db, **data)

    def test_siret(self):
        self.assertTrue(comptes.siret_valide(SIRET_OK))
        self.assertFalse(comptes.siret_valide("73282932000075"))
        self.assertFalse(comptes.siret_valide("1234"))

    def test_creation_et_normalisation(self):
        c = comptes.get_client(self.db, self._creer())
        self.assertEqual(c["email"], "jean@garage.fr")
        self.assertEqual(c["tva"], "FR12345678901")
        self.assertEqual(c["statut"], "en_attente")
        self.assertNotIn("motdepasse", c["mdp_hash"])

    def test_refus(self):
        self._creer()
        with self.assertRaises(comptes.ErreurCompte):
            self._creer(email="jean@garage.fr")          # doublon, casse ignorée
        with self.assertRaises(comptes.ErreurCompte):
            self._creer(email="autre@garage.fr", siret="123")
        with self.assertRaises(comptes.ErreurCompte):
            self._creer(email="autre@garage.fr", mdp="court")

    def test_authentification(self):
        self._creer()
        self.assertIsNotNone(comptes.authentifier(self.db, "JEAN@garage.fr", "motdepasse-solide"))
        self.assertIsNone(comptes.authentifier(self.db, "jean@garage.fr", "mauvais"))
        self.assertIsNone(comptes.authentifier(self.db, "inconnu@garage.fr", "motdepasse-solide"))

    def test_credits(self):
        cid = self._creer()
        self.assertEqual(comptes.mouvement(self.db, cid, 440, "Pack 400 + 40"), 440)
        self.assertEqual(comptes.mouvement(self.db, cid, -59, "Stage 1"), 381)
        with self.assertRaises(comptes.ErreurCompte):
            comptes.mouvement(self.db, cid, -1000, "trop")
        self.assertEqual(comptes.get_client(self.db, cid)["credits"], 381)
        self.assertEqual([m["montant"] for m in comptes.mouvements(self.db, cid)], [-59, 440])

    def test_jeton_usage_unique(self):
        cid = self._creer()
        jeton = comptes.creer_jeton(self.db, cid)
        self.assertEqual(comptes.jeton_valide(self.db, jeton), cid)
        comptes.reinitialiser_mdp(self.db, jeton, "nouveau-mot-de-passe")
        self.assertIsNone(comptes.jeton_valide(self.db, jeton))
        with self.assertRaises(comptes.ErreurCompte):
            comptes.reinitialiser_mdp(self.db, jeton, "encore-un-autre")
        self.assertIsNotNone(comptes.authentifier(self.db, "jean@garage.fr", "nouveau-mot-de-passe"))

    def test_limiteur(self):
        lim = comptes.Limiteur(max_echecs=3, fenetre=60)
        for _ in range(3):
            lim.echec("ip:1")
        self.assertTrue(lim.bloque("ip:1", "email:x"))
        lim.reussite("ip:1")
        self.assertFalse(lim.bloque("ip:1"))


class ParcoursPortailTests(unittest.TestCase):
    """Inscription -> attente -> validation -> connexion -> mot de passe oublié."""

    @classmethod
    def setUpClass(cls):
        import portal
        cls.portal = portal

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app = self.portal.app
        self.saved = {k: app.config[k] for k in ("FS_DB", "FS_DATA_DIR", "FS_CONFIG", "FS_PUBLIC_URL", "FS_FILES")}
        app.config.update(FS_DB=os.path.join(self.tmp.name, "fs.db"), FS_DATA_DIR=self.tmp.name,
                          FS_CONFIG=os.path.join(self.tmp.name, "absent.json"), FS_PUBLIC_URL="",
                          FS_FILES=os.path.join(self.tmp.name, "fichiers"))
        demandes.init_db(app.config["FS_DB"])
        factures.init_db(app.config["FS_DB"])
        self.portal.fileservice.LIMITEUR._echecs.clear()
        self.c = app.test_client()

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def _csrf(self, path):
        html = self.c.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    def _mails(self):
        p = os.path.join(self.tmp.name, "mails_non_envoyes.log")
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def _login(self, mdp):
        return self.c.post("/espace/connexion", data={
            "csrf": self._csrf("/espace/connexion"), "email": "jean@garage.fr", "password": mdp})

    def test_parcours_complet(self):
        self.assertEqual(self.c.get("/espace/").status_code, 302)       # page protégée

        r = self.c.post("/espace/inscription", data={
            "csrf": self._csrf("/espace/inscription"), "societe": "Garage Test", "siret": SIRET_OK,
            "email": "jean@garage.fr", "password": "motdepasse-solide", "cgv": "1"})
        self.assertIn("Demande envoyée", r.get_data(as_text=True))
        self.assertIn("demande d'ouverture de compte", self._mails())

        r = self._login("motdepasse-solide")
        self.assertIn("en attente de validation", r.get_data(as_text=True))

        cid = comptes.client_par_email(self.portal.app.config["FS_DB"], "jean@garage.fr")["id"]
        comptes.changer_statut(self.portal.app.config["FS_DB"], cid, "actif")
        r = self._login("motdepasse-solide")
        self.assertEqual(r.status_code, 302)
        self.assertIn("Garage Test", self.c.get("/espace/").get_data(as_text=True))
        self.assertEqual(self.c.get("/espace/credits").status_code, 200)

        # Tarif : exige le jeton CSRF en en-tête
        r = self.c.post("/espace/tarif", json={"categorie": "vl", "prestations": ["stage1"]})
        self.assertEqual(r.status_code, 400)
        tok = self._csrf("/espace/nouveau")
        r = self.c.post("/espace/tarif", json={"categorie": "vl", "prestations": ["stage1"]},
                        headers={"X-CSRF-Token": tok})
        self.assertEqual(r.get_json()["total"], 59)

        # Compte bloqué : la session tombe immédiatement
        comptes.changer_statut(self.portal.app.config["FS_DB"], cid, "bloque")
        self.assertEqual(self.c.get("/espace/").status_code, 302)

    def test_csrf_obligatoire(self):
        # sans session ni jeton, puis avec une session mais un faux jeton
        for _ in range(2):
            r = self.c.post("/espace/connexion", data={"email": "x@y.fr", "password": "z", "csrf": "faux"})
            self.assertEqual(r.status_code, 302)   # renvoyé sur le formulaire, rien n'est traité
            self._csrf("/espace/connexion")

    def test_force_brute_bloquee(self):
        comptes.creer_client(self.portal.app.config["FS_DB"], societe="G", siret=SIRET_OK, tva="",
                             contact="", email="jean@garage.fr", tel="", mdp="motdepasse-solide")
        for _ in range(5):
            self._login("mauvais-mot-de-passe")
        r = self._login("motdepasse-solide")
        self.assertIn("Trop de tentatives", r.get_data(as_text=True))

    def test_mot_de_passe_oublie(self):
        db = self.portal.app.config["FS_DB"]
        cid = comptes.creer_client(db, societe="G", siret=SIRET_OK, tva="", contact="",
                                   email="jean@garage.fr", tel="", mdp="motdepasse-solide")
        comptes.changer_statut(db, cid, "actif")
        # même réponse pour un e-mail inconnu : pas d'énumération des comptes
        for email in ("jean@garage.fr", "inconnu@garage.fr"):
            r = self.c.post("/espace/mot-de-passe-oublie", data={
                "csrf": self._csrf("/espace/mot-de-passe-oublie"), "email": email})
            self.assertIn("Si un compte existe", r.get_data(as_text=True))
        lien = re.search(r"(/espace/reinitialiser/[\w-]+)", self._mails()).group(1)
        self.assertEqual(self._mails().count("/espace/reinitialiser/"), 1)
        r = self.c.post(lien, data={"csrf": self._csrf(lien), "password": "nouveau-mot-de-passe",
                                    "password2": "nouveau-mot-de-passe"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._login("nouveau-mot-de-passe").status_code, 302)
        self.assertIn("a expiré", self.c.get(lien).get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()


class OngletClientsTests(unittest.TestCase):
    """Routes /clients de l'outil interne."""

    @classmethod
    def setUpClass(cls):
        import app as outil
        cls.outil = outil

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        o = self.outil
        self.saved = (o.FS_DB, o.PORTAL_CONFIG_PATH, o.DATA_DIR)
        o.FS_DB = os.path.join(self.tmp.name, "fs.db")
        o.PORTAL_CONFIG_PATH = os.path.join(self.tmp.name, "portal_config.json")
        o.DATA_DIR = self.tmp.name
        comptes.init_db(o.FS_DB)
        self.cid = comptes.creer_client(o.FS_DB, societe="Garage Test", siret=SIRET_OK, tva="", contact="",
                                        email="jean@garage.fr", tel="", mdp="motdepasse-solide")
        self.c = o.app.test_client()

    def tearDown(self):
        self.outil.FS_DB, self.outil.PORTAL_CONFIG_PATH, self.outil.DATA_DIR = self.saved
        self.tmp.cleanup()

    def test_validation_envoie_le_mail(self):
        self.c.post("/clients/smtp", json={"public_url": "https://portail.exemple.fr/"})
        d = self.c.post("/clients/statut", json={"id": self.cid, "statut": "actif"}).get_json()
        self.assertTrue(d["ok"])
        with open(os.path.join(self.tmp.name, "mails_non_envoyes.log"), encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn("votre compte est ouvert", log)
        self.assertIn("https://portail.exemple.fr/espace/connexion", log)
        self.assertEqual(comptes.get_client(self.outil.FS_DB, self.cid)["statut"], "actif")

    def test_credits(self):
        d = self.c.post("/clients/credits", json={"id": self.cid, "montant": "+440", "libelle": "Pack"}).get_json()
        self.assertEqual(d["credits"], 440)
        r = self.c.post("/clients/credits", json={"id": self.cid, "montant": "-1000"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/clients/credits", json={"id": self.cid, "montant": "abc"})
        self.assertEqual(r.status_code, 400)

    def test_mot_de_passe_smtp_jamais_renvoye(self):
        self.c.post("/clients/smtp", json={"host": "mail.exemple.fr", "port": "465",
                                           "user": "fs@exemple.fr", "password": "secret-smtp"})
        self.c.post("/clients/smtp", json={"host": "mail.exemple.fr", "password": ""})   # vide = conservé
        d = self.c.get("/clients/smtp").get_json()
        self.assertTrue(d["password_set"])
        self.assertNotIn("secret-smtp", self.c.get("/clients/smtp").get_data(as_text=True))
        self.assertNotIn("secret-smtp", self.c.get("/portal-config").get_data(as_text=True))
