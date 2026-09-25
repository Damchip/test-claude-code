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


class RelancesTests(unittest.TestCase):
    def test_solde_bas_et_fichier_non_telecharge(self):
        import datetime as dt
        import relances
        with tempfile.TemporaryDirectory() as t:
            db = _base(t)
            files = os.path.join(t, "f")
            cid = _client(db, credits=60)
            envoyes = []
            run = lambda cfg={}, **kw: relances.verifier(db, cfg, lambda a, s, x: envoyes.append((a, s, x)),
                                                        lambda p: "https://portail.fr" + p, **kw)
            self.assertEqual(run(), [])                                  # 60 >= 50 : rien
            did = _creer(db, files, cid)                                 # -59 -> 1 crédit
            self.assertEqual([r[0] for r in run()], ["solde_bas"])
            self.assertIn("https://portail.fr/credits", envoyes[-1][2])
            self.assertEqual(run(), [])                                  # une seule fois
            comptes.mouvement(db, cid, 100, "recharge")
            run()                                                        # au-dessus du seuil : ré-armé
            comptes.mouvement(db, cid, -100, "conso")
            self.assertEqual([r[0] for r in run()], ["solde_bas"])

            demandes.livrer(db, files, did, "mod.bin", b"\x02")
            self.assertEqual(run(), [])                                  # livré à l'instant : trop tôt
            plus_tard = dt.datetime.now() + dt.timedelta(hours=49)
            self.assertEqual([r[0] for r in run(maintenant=plus_tard)], ["non_telecharge"])
            self.assertIn("/fichiers/F-", envoyes[-1][2])
            self.assertEqual(run(maintenant=plus_tard), [])
            demandes.livrer(db, files, did, "mod.bin", b"\x03")          # nouvelle version : nouveau rappel possible
            self.assertEqual(len(run(maintenant=plus_tard + dt.timedelta(hours=49))), 1)
            self.assertEqual(run({"relances": {"solde_bas": False, "non_telecharge": False}},
                                 maintenant=plus_tard + dt.timedelta(days=9)), [])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import portal
        cls.portal = portal

    def setUp(self):
        import api
        self.api = api
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.sol_db, self.raw, _ = bibliotheque(t)
        app = self.portal.app
        self.cfg = os.path.join(t, "portal_config.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"api_active": True}, fh)
        self.saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG", "FS_SOLUTIONS_DB", "FS_DETECT")}
        app.config.update(FS_DB=_base(t), FS_FILES=os.path.join(t, "fichiers"), FS_DATA_DIR=t, FS_CONFIG=self.cfg,
                          FS_SOLUTIONS_DB=self.sol_db, FS_DETECT=None)
        api.init_db(app.config["FS_DB"])
        self.db = app.config["FS_DB"]
        self.cid = _client(self.db)
        self.cle = api.creer_cle(self.db, self.cid, "test")
        self.c = app.test_client()

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def _h(self, cle=None):
        return {"Authorization": "Bearer " + (cle or self.cle)}

    def test_auth(self):
        self.assertEqual(self.c.get("/api/v1/compte").status_code, 401)               # sans clé
        self.assertEqual(self.c.get("/api/v1/compte", headers=self._h("e85_faux")).status_code, 401)
        r = self.c.get("/api/v1/compte", headers=self._h())
        self.assertEqual(r.get_json()["credits"], 200)
        # API désactivée -> 403 même avec une clé valide
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"api_active": False}, fh)
        self.assertEqual(self.c.get("/api/v1/compte", headers=self._h()).status_code, 403)

    def test_cycle_complet(self):
        d = self.c.get("/api/v1/catalogue", headers=self._h()).get_json()
        self.assertTrue(any(p["code"] == "stage1" for p in d["categories"][0]["prestations"]))
        dv = self.c.post("/api/v1/devis", json={"categorie": "vl", "prestations": ["stage1", "e85"]}, headers=self._h())
        self.assertEqual(dv.get_json()["total"], 99)

        r = self.c.post("/api/v1/demandes", headers=self._h(), data={
            "categorie": "vl", "prestations": "stage1", "marque": "Audi", "modele": "A3",
            "file": (io.BytesIO(self.raw), "ori.bin")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201)
        j = r.get_json()
        numero = j["demande"]["numero"]
        self.assertEqual(j["credits_restants"], 200 - 59)
        self.assertEqual(demandes.get(self.db, demandes.get_par_numero(self.db, numero)["id"])["client_id"], self.cid)

        self.assertEqual(self.c.get("/api/v1/demandes", headers=self._h()).get_json()["demandes"][0]["numero"], numero)
        self.assertEqual(self.c.get(f"/api/v1/demandes/{numero}/original", headers=self._h()).data, self.raw)
        self.assertEqual(self.c.post(f"/api/v1/demandes/{numero}/messages", json={"texte": "Bonjour"},
                                     headers=self._h()).status_code, 201)
        did = demandes.get_par_numero(self.db, numero)["id"]
        demandes.livrer(self.db, self.portal.app.config["FS_FILES"], did, "mod.bin", b"\x09" * 50)
        det = self.c.get(f"/api/v1/demandes/{numero}", headers=self._h()).get_json()
        self.assertEqual(det["livrables"][0]["version"], 1)
        self.assertEqual(self.c.get(det["livrables"][0]["url"], headers=self._h()).data, b"\x09" * 50)
        self.assertIsNotNone(demandes.get(self.db, did)["telecharge_le"])

    def test_isolation_et_revocation(self):
        autre = _client(self.db, email="autre@garage.fr")
        did = _creer(self.db, self.portal.app.config["FS_FILES"], autre)
        num = demandes.get(self.db, did)["numero"]
        self.assertEqual(self.c.get(f"/api/v1/demandes/{num}", headers=self._h()).status_code, 404)  # pas mes demandes
        cle_id = self.api.lister_cles(self.db, self.cid)[0]["id"]
        self.api.revoquer_cle(self.db, self.cid, cle_id)
        self.assertEqual(self.c.get("/api/v1/compte", headers=self._h()).status_code, 401)


class AnglaisTests(unittest.TestCase):
    def setUp(self):
        import portal
        self.portal = portal
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        app = portal.app
        self.saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG")}
        app.config.update(FS_DB=_base(t), FS_FILES=os.path.join(t, "f"), FS_DATA_DIR=t,
                          FS_CONFIG=os.path.join(t, "c.json"))
        portal.fileservice.LIMITEUR._echecs.clear()
        self.db = app.config["FS_DB"]

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def _mails(self):
        p = os.path.join(self.tmp.name, "mails_non_envoyes.log")
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def test_bascule_et_e_mails(self):
        c = self.portal.app.test_client()
        self.assertIn("Bon retour", c.get("/connexion").get_data(as_text=True))       # français par défaut
        r = c.get("/langue/en?next=/connexion")
        self.assertEqual(r.headers["Location"], "/connexion")
        html = c.get("/connexion").get_data(as_text=True)
        self.assertIn("Welcome back", html)
        self.assertIn('lang="en"', html)
        self.assertNotIn("Bon retour", html)
        tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/inscription").get_data(as_text=True)).group(1)
        c.post("/inscription", data={"csrf": tok, "societe": "Garage UK", "siret": "73282932000074",
                                     "email": "uk@garage.co.uk", "password": "motdepasse-solide", "cgv": "1"})
        self.assertIn("account request received", self._mails())
        self.assertEqual(comptes.client_par_email(self.db, "uk@garage.co.uk")["langue"], "en")
        self.assertEqual(c.get("/langue/xx").status_code, 302)                         # langue inconnue -> français

    def test_pages_connectees_en_anglais(self):
        cid = _client(self.db)
        comptes.changer_langue(self.db, cid, "en")
        c = self.portal.app.test_client()
        c.set_cookie("lang", "en")
        tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/connexion").get_data(as_text=True)).group(1)
        c.post("/connexion", data={"csrf": tok, "email": "jean@garage.fr", "password": "motdepasse-solide"})
        pages = {"/": "Dashboard", "/fichiers": "My files", "/nouveau": "Submit request", "/credits": "Transactions",
                 "/parametres": "Change password", "/support": "Frequently asked questions"}
        for url, attendu in pages.items():
            self.assertIn(attendu, c.get(url).get_data(as_text=True), url)
        _creer(self.db, self.portal.app.config["FS_FILES"], cid)
        self.assertIn("Received", c.get("/fichiers").get_data(as_text=True))        # libellé de statut traduit

    def test_e_mail_atelier_dans_la_langue_du_client(self):
        import app as outil
        saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR)
        outil.FS_DB, outil.FS_FILES = self.db, os.path.join(self.tmp.name, "f")
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(self.tmp.name, "c.json"), self.tmp.name
        try:
            cid = _client(self.db)
            comptes.changer_langue(self.db, cid, "en")
            did = _creer(self.db, outil.FS_FILES, cid)
            o = outil.app.test_client()
            o.post(f"/fs/demandes/{did}/message", data={"texte": "Please send the EEPROM", "attente": "1"})
            self.assertIn("information needed", self._mails())
            o.post(f"/fs/demandes/{did}/refuser", json={"motif": "unreadable"})
            self.assertIn("file declined", self._mails())
        finally:
            outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = saved


class CreationManuelleTests(unittest.TestCase):
    def setUp(self):
        import app as outil
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR)
        outil.FS_DB = _base(self.tmp.name)
        outil.FS_FILES = os.path.join(self.tmp.name, "f")
        outil.PORTAL_CONFIG_PATH = os.path.join(self.tmp.name, "c.json")
        outil.DATA_DIR = self.tmp.name
        with open(outil.PORTAL_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"public_url": "https://portail.exemple.fr"}, fh)
        self.c = outil.app.test_client()

    def tearDown(self):
        self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR = self.saved
        self.tmp.cleanup()

    def _mails(self):
        with open(os.path.join(self.tmp.name, "mails_non_envoyes.log"), encoding="utf-8") as fh:
            return fh.read()

    def test_invitation(self):
        d = self.c.post("/clients/creer", json={"societe": "Garage Invité", "email": "Invite@Garage.fr",
                                                "siret": "73282932000074", "niveau": "Partenaire",
                                                "credits": "440"}).get_json()
        self.assertTrue(d["ok"], d)
        c = comptes.get_client(self.o.FS_DB, d["id"])
        self.assertEqual((c["statut"], c["niveau"], c["credits"], c["email"]), ("actif", "Partenaire", 440, "invite@garage.fr"))
        lien = re.search(r"https://portail\.exemple\.fr(/reinitialiser/[\w-]+)", self._mails()).group(1)
        self.assertIn("valable 7 jours", self._mails())
        # le client choisit son mot de passe via le portail puis se connecte
        import portal
        p = portal.app
        saved = {k: p.config[k] for k in ("FS_DB", "FS_DATA_DIR", "FS_CONFIG", "FS_FILES")}
        p.config.update(FS_DB=self.o.FS_DB, FS_DATA_DIR=self.tmp.name, FS_CONFIG=self.o.PORTAL_CONFIG_PATH,
                        FS_FILES=self.o.FS_FILES)
        try:
            portal.fileservice.LIMITEUR._echecs.clear()
            pc = p.test_client()
            tok = re.search(r'name="csrf" value="([^"]+)"', pc.get(lien).get_data(as_text=True)).group(1)
            pc.post(lien, data={"csrf": tok, "password": "choisi-par-client", "password2": "choisi-par-client"})
            self.assertIsNotNone(comptes.authentifier(self.o.FS_DB, "invite@garage.fr", "choisi-par-client"))
        finally:
            p.config.update(saved)
        r = self.c.post("/clients/inviter", json={"id": d["id"]}).get_json()
        self.assertTrue(r["ok"])
        self.assertIn("/reinitialiser/", r["lien"])          # sans SMTP : lien fourni à l'atelier

    def test_mot_de_passe_fixe_et_client_etranger(self):
        d = self.c.post("/clients/creer", json={"societe": "Garage UK Ltd", "email": "uk@garage.co.uk", "pays": "Royaume-Uni",
                                                "siret": "Companies House 01234567", "langue": "en",
                                                "mdp": "mot-de-passe-atelier"}).get_json()
        self.assertTrue(d["ok"], d)
        self.assertIsNotNone(comptes.authentifier(self.o.FS_DB, "uk@garage.co.uk", "mot-de-passe-atelier"))
        self.assertEqual(comptes.get_client(self.o.FS_DB, d["id"])["langue"], "en")
        r = self.c.post("/clients/creer", json={"societe": "X", "email": "x@x.fr", "siret": "123"})
        self.assertEqual(r.status_code, 400)                 # en France, le SIRET reste vérifié
        r = self.c.post("/clients/creer", json={"societe": "Y", "email": "uk@garage.co.uk", "pays": "Belgique"})
        self.assertIn("existe déjà", r.get_json()["error"])

    def test_technicien_sans_credits_d_ouverture(self):
        self.c.post("/equipe/creer", json={"identifiant": "admin", "nom": "Admin", "mdp": "admin-solide-1"})
        self.c.post("/equipe/creer", json={"identifiant": "tech", "nom": "Tech", "role": "technicien", "mdp": "tech-solide-12"})
        t = self.o.app.test_client()
        t.post("/login", data={"identifiant": "tech", "password": "tech-solide-12"})
        base = {"societe": "G", "email": "g@g.fr", "siret": "73282932000074"}
        self.assertEqual(t.post("/clients/creer", json={**base, "credits": "100"}).status_code, 403)
        self.assertTrue(t.post("/clients/creer", json=base).get_json()["ok"])
        j = self.c.get("/equipe/journal").get_json()["journal"]
        self.assertTrue(any(x["action"] == "Compte client créé" and x["technicien"] == "Tech" for x in j))
