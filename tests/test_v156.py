"""Tests v1.56 — mise en production : double authentification, sauvegarde externe, santé, tâches."""
import base64
import json
import os
import re
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import comptes
import equipe
import mailer
import sante
import sauvegarde_externe
import taches
from test_demandes import _base, _client, _creer


class TotpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _base(self.tmp.name)
        equipe.init_db(self.db)
        self.tid = equipe.creer(self.db, "damien", "Damien", "admin", "admin-solide-1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_vecteurs_rfc6238(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(equipe.code_totp(secret, 59), "287082")
        self.assertEqual(equipe.code_totp(secret, 1111111109), "081804")
        self.assertEqual(equipe.code_totp(secret, 2000000000), "279037")

    def test_activation_rejeu_et_secours(self):
        s = equipe.nouveau_secret()
        with self.assertRaises(comptes.ErreurCompte):
            equipe.activer_double_auth(self.db, self.tid, s, "000000" if equipe.code_totp(s) != "000000" else "111111")
        codes = equipe.activer_double_auth(self.db, self.tid, s, equipe.code_totp(s))
        self.assertEqual(len(codes), equipe.CODES_SECOURS_N)
        t = equipe.authentifier(self.db, "damien", "admin-solide-1")
        self.assertTrue(t["double_auth"])
        # le code qui a servi à l'activation ne peut pas resservir (anti-rejeu)
        self.assertIsNone(equipe.verifier_second_facteur(self.db, self.tid, equipe.code_totp(s)))
        futur = time.time() + 30
        self.assertEqual(equipe.verifier_second_facteur(self.db, self.tid, equipe.code_totp(s, futur)), "totp")
        self.assertEqual(equipe.verifier_second_facteur(self.db, self.tid, codes[0].lower()), "secours")
        self.assertIsNone(equipe.verifier_second_facteur(self.db, self.tid, codes[0]))       # usage unique
        self.assertEqual(equipe.codes_secours_restants(self.db, self.tid), equipe.CODES_SECOURS_N - 1)
        self.assertIn("secret=" + s, equipe.uri_otpauth(s, "damien"))


class ConnexionOutilTests(unittest.TestCase):
    def setUp(self):
        import app as outil
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR, outil.CONFIG_PATH, outil.PROD)
        outil.FS_DB = _base(t)
        outil.FS_FILES = os.path.join(t, "fichiers")
        outil.PORTAL_CONFIG_PATH = os.path.join(t, "c.json")
        outil.CONFIG_PATH = os.path.join(t, "config.json")
        with open(outil.CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"secret_key": "x"}, fh)
        outil.DATA_DIR = t
        outil.LIMITEUR_OUTIL._echecs.clear()

    def tearDown(self):
        (self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR,
         self.o.CONFIG_PATH, self.o.PROD) = self.saved
        self.tmp.cleanup()

    def _activer(self, c):
        d = c.post("/equipe/2fa/debut", json={}).get_json()
        r = c.post("/equipe/2fa/activer", json={"code": equipe.code_totp(d["secret"])}).get_json()
        self.assertTrue(r["ok"], r)
        return d["secret"], r["codes_secours"]

    def test_connexion_en_deux_etapes(self):
        c = self.o.app.test_client()
        c.post("/equipe/creer", json={"identifiant": "damien", "nom": "Damien", "mdp": "admin-solide-1"})
        secret, codes = self._activer(c)
        self.assertTrue(c.get("/equipe").get_json()["moi"]["double_auth"])

        n = self.o.app.test_client()
        r = n.post("/login", data={"identifiant": "damien", "password": "admin-solide-1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="code"', r.get_data(as_text=True))
        self.assertEqual(n.get("/fs/demandes").status_code, 401)               # mot de passe seul : pas connecté
        self.assertIn("incorrect", n.post("/login", data={"code": "12"}).get_data(as_text=True))
        r = n.post("/login", data={"code": equipe.code_totp(secret, time.time() + 30)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(n.get("/fs/demandes").status_code, 200)

        for _ in range(8):                                                     # force brute du code : bloquée par compte
            b = self.o.app.test_client()
            ip = {"REMOTE_ADDR": f"10.0.0.{_}"}
            b.post("/login", data={"identifiant": "damien", "password": "admin-solide-1"}, environ_base=ip)
            b.post("/login", data={"code": "000000"}, environ_base=ip)
        b = self.o.app.test_client()
        ip = {"REMOTE_ADDR": "10.0.0.99"}
        b.post("/login", data={"identifiant": "damien", "password": "admin-solide-1"}, environ_base=ip)
        self.assertIn("Trop de tentatives", b.post("/login", data={"code": equipe.code_totp(secret)},
                                                   environ_base=ip).get_data(as_text=True))
        self.o.LIMITEUR_OUTIL._echecs.clear()

        s = self.o.app.test_client()                                           # code de secours
        s.post("/login", data={"identifiant": "damien", "password": "admin-solide-1"})
        self.assertEqual(s.post("/login", data={"code": codes[1]}).status_code, 302)

        # code attendu depuis trop longtemps : retour à l'identifiant
        e = self.o.app.test_client()
        e.post("/login", data={"identifiant": "damien", "password": "admin-solide-1"})
        with e.session_transaction() as sess:
            sess["tech_2fa_t"] = time.time() - 3600
        self.assertIn('name="identifiant"', e.get("/login").get_data(as_text=True))

    def test_obligatoire_et_retrait_par_admin(self):
        c = self.o.app.test_client()
        c.post("/equipe/creer", json={"identifiant": "damien", "nom": "Damien", "mdp": "admin-solide-1"})
        self.assertEqual(c.post("/equipe/securite", json={"exiger_double_auth": True}).status_code, 400)  # pas sur soi
        self._activer(c)
        self.assertTrue(c.post("/equipe/securite", json={"exiger_double_auth": True}).get_json()["ok"])
        c.post("/equipe/creer", json={"identifiant": "thomas", "nom": "Thomas", "role": "technicien", "mdp": "tech-solide-12"})
        t = self.o.app.test_client()
        self.assertEqual(t.post("/login", data={"identifiant": "thomas", "password": "tech-solide-12"}).status_code, 302)
        r = t.get("/fs/demandes")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.get_json()["double_auth_requise"])
        self.assertEqual(t.get("/equipe").status_code, 200)                    # de quoi l'activer
        self._activer(t)
        self.assertEqual(t.get("/fs/demandes").status_code, 200)
        self.assertEqual(t.post("/equipe/2fa/desactiver", json={"mdp": "tech-solide-12"}).status_code, 400)  # obligatoire
        tid = next(x["id"] for x in c.get("/equipe").get_json()["techniciens"] if x["identifiant"] == "thomas")
        c.post("/equipe/modifier", json={"id": tid, "nom": "Thomas", "role": "technicien", "actif": True,
                                         "retirer_double_auth": True})
        self.assertFalse(equipe.get(self.o.FS_DB, tid)["double_auth"])
        self.assertEqual(t.post("/equipe/securite", json={"exiger_double_auth": False}).status_code, 403)   # admin seulement

    def test_production_sans_compte(self):
        self.o.PROD = True
        c = self.o.app.test_client()
        r = c.get("/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("outils_prod.py admin", r.get_data(as_text=True))
        self.assertEqual(c.post("/equipe/creer", json={"identifiant": "pirate", "nom": "P", "mdp": "0123456789ab"}).status_code, 503)
        self.assertFalse(equipe.existe(self.o.FS_DB))
        self.assertEqual(c.post("/login", data={"password": ""}).status_code, 503)


class SauvegardeExterneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.db = _base(t)
        self.files = os.path.join(t, "fichiers")
        cid = _client(self.db)
        _creer(self.db, self.files, cid)

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive(self):
        nom, z = sauvegarde_externe.archive(self.db, self.files, avec_fichiers=True)
        self.assertTrue(nom.startswith("fileservice-") and nom.endswith(".zip"))
        with zipfile.ZipFile(__import__("io").BytesIO(z)) as zf:
            noms = zf.namelist()
        self.assertIn("fileservice.db", noms)
        self.assertTrue(any(n.startswith("fileservice_fichiers/1/original_") for n in noms))

    def test_ftp(self):
        envoyes = {}

        class FauxFTP:
            def __init__(self, *a, **k):
                self.fichiers = ["fileservice-20200101-000000.zip", "fileservice-20200102-000000.zip", "autre.txt"]

            def connect(self, host, port):
                envoyes["hote"] = (host, port)

            def login(self, u, p):
                envoyes["login"] = (u, p)

            def prot_p(self):
                envoyes["chiffre"] = True

            def cwd(self, d):
                pass

            def mkd(self, d):
                pass

            def storbinary(self, cmd, fh):
                envoyes["fichier"] = cmd.split(" ", 1)[1]
                self.fichiers.append(envoyes["fichier"])

            def nlst(self):
                return list(self.fichiers)

            def delete(self, n):
                envoyes.setdefault("supprimes", []).append(n)

            def quit(self):
                pass

        cfg = {"sauvegarde_externe": {"mode": "ftp", "garder": 2,
                                      "ftp": {"host": "ftp.exemple.fr", "user": "u", "password": "p", "dossier": "/sauv"}}}
        with mock.patch("ftplib.FTP_TLS", FauxFTP):
            r = sauvegarde_externe.executer(cfg, self.db, self.files, self.tmp.name)
        self.assertTrue(r["ok"], r)
        self.assertTrue(envoyes["chiffre"])
        self.assertEqual(envoyes["supprimes"], ["fileservice-20200101-000000.zip"])   # 2 gardées
        self.assertTrue(sauvegarde_externe.derniere(self.tmp.name)["ok"])
        self.assertNotIn("password", sauvegarde_externe.reglages(cfg)["ftp"])          # jamais renvoyé à l'interface

    def test_email_sans_smtp_note_l_echec(self):
        r = sauvegarde_externe.executer({"sauvegarde_externe": {"mode": "email", "email": "a@b.fr"}},
                                        self.db, self.files, self.tmp.name)
        self.assertFalse(r["ok"])
        self.assertIn("SMTP", r["message"])
        self.assertFalse(sauvegarde_externe.derniere(self.tmp.name)["ok"])


class SanteTachesTests(unittest.TestCase):
    def setUp(self):
        maj = mock.patch("mise_a_jour.automatique", return_value=None)   # pas d'appel à GitHub pendant les tests
        maj.start()
        self.addCleanup(maj.stop)
        import portal
        self.portal = portal
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.cfg = os.path.join(t, "portal_config.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"smtp": {"atelier": "atelier@exemple.fr"}}, fh)
        app = portal.app
        self.saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG")}
        app.config.update(FS_DB=_base(t), FS_FILES=os.path.join(t, "fichiers"), FS_DATA_DIR=t, FS_CONFIG=self.cfg)

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def test_sante_publique(self):
        c = self.portal.app.test_client()
        r = c.get("/sante")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.get_json()), {"ok", "version"})              # aucun détail exposé
        self.portal.app.config["FS_DB"] = os.path.join(self.tmp.name, "absent", "x", "..", "..")   # dossier : base illisible
        self.assertEqual(c.get("/sante").status_code, 503)

    def test_verifier_et_taches(self):
        c = self.portal.app.config
        s = sante.verifier(c["FS_DB"], c["FS_DATA_DIR"], {})
        self.assertIn("Aucune sauvegarde de la base.", s["problemes"])
        envois = []
        with mock.patch("mailer.envoyer", lambda cfg, a, sujet, texte, **k: envois.append((a, sujet)) or (True, "")):
            r = taches.executer(self.portal.app)
            self.assertIn("sauvegarde", r["fait"])
            self.assertTrue(r["sante"]["ok"], r["sante"])
            self.assertEqual(taches.executer(self.portal.app)["fait"], [])     # déjà fait aujourd'hui
            # problème : e-mails en échec -> une alerte, pas deux
            mailer._noter_echec(c["FS_DATA_DIR"], "x@y.fr", "Sujet", "texte", "Envoi impossible : refusé")
            taches.executer(self.portal.app)
            taches.executer(self.portal.app)
        alertes = [e for e in envois if e[1].startswith("⚠")]
        self.assertEqual(len(alertes), 1)
        self.assertEqual(alertes[0][0], "atelier@exemple.fr")
        self.assertIsNotNone(taches.derniere_execution(c["FS_DATA_DIR"]))

    def test_echec_smtp_journalise(self):
        d = self.tmp.name
        ok, err = mailer.envoyer({"host": "127.0.0.1", "port": 1, "user": "u", "password": "p"}, "a@b.fr", "Sujet",
                                 "Texte", journal_dir=d)
        self.assertFalse(ok)
        self.assertEqual(mailer.echecs(d)[0]["a"], "a@b.fr")
        mailer.acquitter_echecs(d)
        self.assertEqual(mailer.echecs(d), [])


class OutilSanteTests(unittest.TestCase):
    def setUp(self):
        import app as outil
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR)
        outil.FS_DB, outil.FS_FILES = _base(t), os.path.join(t, "f")
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(t, "c.json"), t
        self.c = outil.app.test_client()

    def tearDown(self):
        self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR = self.saved
        self.tmp.cleanup()

    def test_reglages_et_etat(self):
        r = self.c.post("/fs/reglages", json={"sauvegarde_externe": {"mode": "ftp", "ftp": {"host": "ftp.x.fr", "password": "secret"}}})
        self.assertTrue(r.get_json()["ok"])
        g = self.c.get("/fs/reglages").get_json()["sauvegarde_externe"]
        self.assertTrue(g["ftp"]["password_set"])
        self.assertNotIn("password", g["ftp"])
        self.c.post("/fs/reglages", json={"sauvegarde_externe": {"mode": "ftp", "ftp": {"host": "ftp.x.fr", "password": ""}}})
        with open(self.o.PORTAL_CONFIG_PATH, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["sauvegarde_externe"]["ftp"]["password"], "secret")   # vide = inchangé
        self.assertEqual(self.c.post("/fs/reglages", json={"sauvegarde_externe": {"mode": "email", "email": "pas-un-mail"}}).status_code, 400)
        mailer._noter_echec(self.tmp.name, "x@y.fr", "S", "T", "refusé")
        self.assertEqual(self.c.get("/fs/alertes").get_json()["mails_echecs"], 1)
        s = self.c.get("/fs/sante").get_json()
        self.assertFalse(s["ok"])
        self.c.post("/fs/sante/acquitter", json={})
        self.assertEqual(self.c.get("/fs/alertes").get_json()["mails_echecs"], 0)


class DeploiementTests(unittest.TestCase):
    def test_points_d_entree(self):
        import runpy
        with mock.patch.dict(os.environ, {}):
            self.assertEqual(runpy.run_path(os.path.join(ROOT, "passenger_wsgi.py"))["application"].name, "portal")
            self.assertEqual(runpy.run_path(os.path.join(ROOT, "deploy", "atelier", "passenger_wsgi.py"))["application"].name, "app")
            self.assertEqual(runpy.run_path(os.path.join(ROOT, "wsgi_portail.py"))["application"].name, "portal")
            self.assertEqual(runpy.run_path(os.path.join(ROOT, "deploy", "atelier", "wsgi_atelier.py"))["application"].name, "app")
            # modèle écrit par cPanel (« Setup Python App ») avec nos fichiers de démarrage : pas de récursion
            for dossier, fichier, nom in ((ROOT, "wsgi_portail.py", "portal"),
                                          (os.path.join(ROOT, "deploy", "atelier"), "wsgi_atelier.py", "app")):
                ancien = os.getcwd()
                os.chdir(dossier)
                try:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("wsgi", fichier)
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)
                    self.assertEqual(m.application.name, nom)
                finally:
                    os.chdir(ancien)


if __name__ == "__main__":
    unittest.main()


class FinitionsTests(unittest.TestCase):
    def setUp(self):
        import portal
        self.portal = portal
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        app = portal.app
        self.saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG", "FS_DETECT")}
        cfg = os.path.join(t, "c.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"api_active": True, "societe": {"raison_sociale": "E85 SAS", "siret": "73282932000074"}}, fh)
        app.config.update(FS_DB=_base(t), FS_FILES=os.path.join(t, "f"), FS_DATA_DIR=t, FS_CONFIG=cfg, FS_DETECT=None)
        import api
        api.init_db(app.config["FS_DB"])
        self.cid = _client(app.config["FS_DB"])
        self.did = _creer(app.config["FS_DB"], app.config["FS_FILES"], self.cid)
        portal.fileservice.LIMITEUR._echecs.clear()
        self.c = app.test_client()
        self.c.post("/connexion", data={"csrf": self._tok("/connexion"), "email": "jean@garage.fr", "password": "motdepasse-solide"})

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def _tok(self, p):
        return re.search(r'name="csrf" value="([^"]+)"', self.c.get(p).get_data(as_text=True)).group(1)

    def test_recapitulatif(self):
        h = self.c.get("/fichiers/F-00001/recapitulatif").get_data(as_text=True)
        self.assertIn("Récapitulatif de demande", h)
        self.assertIn("E85 SAS", h)
        self.assertIn("SHA-256", h)
        self.assertIn("147,50 €", h)                       # 59 crédits × 2,50 € HT
        self.c.set_cookie("lang", "en")
        self.assertIn("Request summary", self.c.get("/fichiers/F-00001/recapitulatif").get_data(as_text=True))
        autre = _client(self.portal.app.config["FS_DB"], email="autre@garage.fr")
        _creer(self.portal.app.config["FS_DB"], self.portal.app.config["FS_FILES"], autre)
        self.assertEqual(self.c.get("/fichiers/F-00002/recapitulatif").status_code, 404)   # pas la sienne

    def test_anglais_messages_et_api(self):
        self.c.set_cookie("lang", "en")
        h = self.c.get("/parametres/api").get_data(as_text=True)
        for fr in ("Mes clés", "Générer une clé", "Points d'accès", "Prise en main"):
            self.assertNotIn(fr, h)
        self.assertIn("Generate a key", h)
        r = self.c.post("/nouveau", data={"csrf": self._tok("/nouveau"), "categorie": "vl", "prestas": ["stage1"],
                                          "file": (__import__("io").BytesIO(b"\x02" * 4096), "lecture.bin")},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("Request F-00002 sent: 59 credits charged.", r.get_data(as_text=True))
        h = self.c.get("/").get_data(as_text=True)
        self.assertIn("Monday", h)
        self.assertNotIn("Lundi", h)
        self.assertIn("% bonus", self.c.get("/credits").get_data(as_text=True))


import catalogue
import demandes
import sms
import traductions


class ExpressAnnexesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _base(self.tmp.name)
        self.files = os.path.join(self.tmp.name, "f")
        self.cid = _client(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_devis_et_reglages(self):
        d = catalogue.devis("vl", ["stage1"], express=20, remise=10, niveau="VIP")
        self.assertEqual(d["lignes"][-1]["nom"], catalogue.EXPRESS_NOM)
        self.assertEqual(d["total"], 59 - 6 + 20)                      # la remise ne touche pas l'express
        self.assertEqual(catalogue.supplement_express({}, True), 0)     # désactivée par défaut
        self.assertEqual(catalogue.supplement_express({"express": {"actif": True, "credits": 25}}, True), 25)
        self.assertEqual(catalogue.supplement_express({"express": {"actif": True, "credits": 25}}, False), 0)

    def test_priorite_et_remboursement(self):
        normal = _creer(self.db, self.files, self.cid)
        urgent = _creer(self.db, self.files, self.cid, express=20)
        self.assertEqual([d["id"] for d in demandes.lister(self.db)][:2], [urgent, normal])
        demandes.changer_statut(self.db, normal, "en_cours")
        ancien = _creer(self.db, self.files, self.cid)
        self.assertEqual(demandes.lister(self.db)[0]["id"], urgent)     # express en tête tant qu'il est ouvert
        self.assertEqual(demandes.get(self.db, urgent)["total"], 79)
        self.assertEqual(demandes.refuser(self.db, urgent, "Illisible"), 79)   # supplément remboursé
        self.assertEqual(demandes.lister(self.db)[0]["id"], ancien)

    def test_annexes(self):
        did = _creer(self.db, self.files, self.cid, annexes=[("../eeprom.bin", b"\x05" * 100), ("vide.bin", b"")])
        a = demandes.annexes(self.db, did)
        self.assertEqual(len(a), 1)                                     # fichier vide ignoré
        self.assertEqual(a[0]["nom"], "eeprom.bin")                     # pas de chemin
        self.assertIsNotNone(demandes.chemin(self.files, did, a[0]["fichier"]))
        with self.assertRaises(comptes.ErreurCompte):
            _creer(self.db, self.files, self.cid, annexes=[(f"a{i}.bin", b"1") for i in range(5)])
        self.assertEqual(demandes.export_client(self.db, self.cid)["demandes"][0]["fichiers_complementaires"], ["eeprom.bin"])


class PortailPart3Base(unittest.TestCase):
    def setUp(self):
        import portal
        self.portal = portal
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.cfg = os.path.join(t, "c.json")
        self.ecrire_cfg({"public_url": "https://fichiers.exemple.fr", "express": {"actif": True, "credits": 20},
                         "smtp": {"atelier": "atelier@exemple.fr"}})
        app = portal.app
        self.saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG", "FS_DETECT", "FS_SOLUTIONS_DB")}
        app.config.update(FS_DB=_base(t), FS_FILES=os.path.join(t, "f"), FS_DATA_DIR=t, FS_CONFIG=self.cfg, FS_DETECT=None,
                          FS_SOLUTIONS_DB=None)
        import api
        api.init_db(app.config["FS_DB"])
        self.db = app.config["FS_DB"]
        self.cid = _client(self.db)
        portal.fileservice.LIMITEUR._echecs.clear()
        self.c = app.test_client()
        self.login(self.c, "jean@garage.fr", "motdepasse-solide")

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def ecrire_cfg(self, d):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(d, fh)

    @staticmethod
    def tok(c, p):
        return re.search(r'name="csrf" value="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)

    def login(self, c, email, mdp):
        return c.post("/connexion", data={"csrf": self.tok(c, "/connexion"), "email": email, "password": mdp})

    def envoyer(self, c, **extra):
        data = {"csrf": self.tok(c, "/nouveau"), "categorie": "vl", "prestas": ["stage1"],
                "file": (__import__("io").BytesIO(b"\x02" * 4096), "lecture.bin")}
        data.update(extra)
        return c.post("/nouveau", data=data, content_type="multipart/form-data")

    def mails(self):
        with open(os.path.join(self.tmp.name, "mails_non_envoyes.log"), encoding="utf-8") as fh:
            return fh.read()


class ExpressPortailTests(PortailPart3Base):
    def test_express_et_annexes_depuis_le_site(self):
        self.assertIn("Traitement express", self.c.get("/nouveau").get_data(as_text=True))
        r = self.c.post("/tarif", json={"categorie": "vl", "prestations": ["stage1"], "express": True},
                        headers={"X-CSRF-Token": self.tok(self.c, "/nouveau")}).get_json()
        self.assertEqual(r["total"], 79)
        self.envoyer(self.c, express="1", annexes=[(__import__("io").BytesIO(b"\x07" * 64), "eeprom.bin")])
        d = demandes.lister(self.db, self.cid)[0]
        self.assertEqual((d["express"], d["total"]), (1, 79))
        h = self.c.get(f"/fichiers/{d['numero']}").get_data(as_text=True)
        self.assertIn("eeprom.bin", h)
        a = demandes.annexes(self.db, d["id"])[0]
        self.assertEqual(self.c.get(f"/fichiers/{d['numero']}/annexe/{a['id']}").data, b"\x07" * 64)
        self.assertIn("EXPRESS", self.mails())
        # option désactivée par l'atelier : la case envoyée est ignorée
        self.ecrire_cfg({"express": {"actif": False, "credits": 20}})
        self.envoyer(self.c, express="1")
        self.assertEqual(demandes.lister(self.db, self.cid)[0]["total"], 59)
        self.assertNotIn("Traitement express", self.c.get("/nouveau").get_data(as_text=True))


class SousComptesTests(PortailPart3Base):
    def ajouter(self, nom="Thomas", email="thomas@garage.fr", achat=False):
        data = {"csrf": self.tok(self.c, "/parametres"), "action": "utilisateur_ajout", "nom": nom, "email": email}
        if achat:
            data["peut_acheter"] = "1"
        return self.c.post("/parametres", data=data)

    def activer(self, email="thomas@garage.fr", mdp="mdp-du-technicien"):
        lien = re.findall(r"https://fichiers\.exemple\.fr(/reinitialiser/[\w-]+)", self.mails())[-1]
        m = self.portal.app.test_client()
        m.post(lien, data={"csrf": self.tok(m, lien), "password": mdp, "password2": mdp})
        self.login(m, email, mdp)
        return m

    def test_parcours_utilisateur(self):
        self.ajouter()
        self.assertIn("vous a ajouté au compte fileservice de Garage Test", self.mails())
        m = self.activer()
        self.assertIsNotNone(comptes.authentifier(self.db, "jean@garage.fr", "motdepasse-solide"))  # titulaire intact
        self.assertIn("Thomas", m.get("/").get_data(as_text=True))
        self.envoyer(m)
        d = demandes.lister(self.db, self.cid)[0]
        self.assertEqual(d["envoye_par"], "Thomas")
        self.assertIn("par Thomas", self.c.get(f"/fichiers/{d['numero']}").get_data(as_text=True))   # le titulaire voit tout
        # droits : pas d'achat, pas de factures, pas de réglages du compte
        self.assertIn("titulaire du compte", m.get("/credits").get_data(as_text=True))
        self.assertEqual(m.post("/credits/acheter/0", data={"csrf": self.tok(m, "/credits")}).status_code, 403)
        self.assertEqual(m.get("/parametres/mes-donnees").status_code, 403)
        self.assertEqual(m.get("/parametres/api").status_code, 302)
        self.assertEqual(m.post("/parametres", data={"csrf": self.tok(m, "/parametres"), "action": "profil",
                                                     "contact": "Pirate"}).status_code, 403)
        self.assertEqual(m.post("/parametres", data={"csrf": self.tok(m, "/parametres"), "action": "utilisateur_ajout",
                                                     "nom": "X", "email": "x@x.fr"}).status_code, 403)
        # son mot de passe à lui
        m.post("/parametres", data={"csrf": self.tok(m, "/parametres"), "action": "mdp", "actuel": "mdp-du-technicien",
                                    "nouveau": "nouveau-mdp-tech", "nouveau2": "nouveau-mdp-tech"})
        self.assertIsNotNone(comptes.authentifier_compte(self.db, "thomas@garage.fr", "nouveau-mdp-tech"))
        # le titulaire désactive puis retire l'utilisateur : sa session tombe
        uid = comptes.lister_utilisateurs(self.db, self.cid)[0]["id"]
        self.c.post("/parametres", data={"csrf": self.tok(self.c, "/parametres"), "action": "utilisateur_actif",
                                         "id": uid, "valeur": "0"})
        self.assertEqual(m.get("/").status_code, 302)
        self.c.post("/parametres", data={"csrf": self.tok(self.c, "/parametres"), "action": "utilisateur_suppr", "id": uid})
        self.assertIsNone(comptes.utilisateur_par_email(self.db, "thomas@garage.fr"))
        self.assertEqual(demandes.get(self.db, d["id"])["envoye_par"], "Thomas")        # l'historique reste

    def test_achat_autorise_et_emails(self):
        self.ajouter(achat=True)
        m = self.activer()
        self.assertNotIn("titulaire du compte", m.get("/credits").get_data(as_text=True))
        r = self.ajouter(nom="Doublon", email="Jean@Garage.fr")                         # e-mail du titulaire
        self.assertIn("existe déjà", self.c.get(r.headers["Location"]).get_data(as_text=True))
        with self.assertRaises(comptes.ErreurCompte):
            comptes.creer_client(self.db, societe="X", siret="73282932000074", tva="", contact="", email="thomas@garage.fr",
                                 tel="", mdp="0123456789ab")
        # mot de passe oublié pour un utilisateur : le lien ne touche que lui
        self.c.post("/mot-de-passe-oublie", data={"csrf": self.tok(self.c, "/mot-de-passe-oublie"), "email": "thomas@garage.fr"})
        lien = re.findall(r"https://fichiers\.exemple\.fr(/reinitialiser/[\w-]+)", self.mails())[-1]
        self.assertIsNotNone(comptes.jeton_utilisateur(self.db, lien.rsplit("/", 1)[1]))
        m2 = self.portal.app.test_client()
        m2.post(lien, data={"csrf": self.tok(m2, lien), "password": "encore-un-mdp", "password2": "encore-un-mdp"})
        self.assertIsNotNone(comptes.authentifier_compte(self.db, "thomas@garage.fr", "encore-un-mdp"))
        self.assertIsNotNone(comptes.authentifier(self.db, "jean@garage.fr", "motdepasse-solide"))
        # e-mail « fichier prêt » : au titulaire ET à l'utilisateur qui a envoyé
        self.envoyer(m)
        d = demandes.lister(self.db, self.cid)[0]
        with open(self.cfg, encoding="utf-8") as fh:
            cfg = json.load(fh)
        from fileservice import prevenir
        prevenir(cfg, demandes.get(self.db, d["id"]), "fichier_pret", "https://x/f", journal_dir=self.tmp.name)
        log = self.mails()
        self.assertIn("à jean@garage.fr · E85-FRANCE — F-00001 : fichier prêt", log)
        self.assertIn("à thomas@garage.fr · E85-FRANCE — F-00001 : fichier prêt", log)

    def test_rgpd(self):
        self.ajouter()
        demandes.anonymiser_client(self.db, self.portal.app.config["FS_FILES"], self.cid)
        self.assertIsNone(comptes.utilisateur_par_email(self.db, "thomas@garage.fr"))


class SmsTests(PortailPart3Base):
    def test_preferences_client(self):
        r = self.c.post("/parametres", data={"csrf": self.tok(self.c, "/parametres"), "action": "sms",
                                             "mobile": "06 12 34 56 78", "sms_actif": "1"})
        self.assertEqual(r.status_code, 302)
        c = comptes.get_client(self.db, self.cid)
        self.assertEqual((c["sms_mobile"], c["sms_actif"]), ("+33612345678", 1))
        with self.assertRaises(comptes.ErreurCompte):
            comptes.changer_sms(self.db, self.cid, "12", True)
        with self.assertRaises(comptes.ErreurCompte):
            comptes.changer_sms(self.db, self.cid, "", True)

    def test_envoi_brevo_et_twilio(self):
        appels = []

        class Rep:
            def __init__(self, *a):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"messageId": 1}'

        def faux_urlopen(req, timeout=0):
            appels.append((req.full_url, dict(req.header_items()), req.data))
            return Rep()

        brevo = {"sms": {"actif": True, "fournisseur": "brevo", "cle": "xkeysib-1", "expediteur": "E85FRANCE"}}
        twilio = {"sms": {"actif": True, "fournisseur": "twilio", "cle": "tok", "compte": "AC123", "expediteur": "+33700000000"}}
        self.assertFalse(sms.configure({"sms": {"actif": True, "fournisseur": "twilio", "cle": "tok", "expediteur": "E85"}}))
        with mock.patch("urllib.request.urlopen", faux_urlopen):
            self.assertEqual(sms.envoyer(brevo, "+33612345678", "Bonjour")[0], True)
            self.assertEqual(sms.envoyer(twilio, "+33612345678", "Bonjour")[0], True)
        self.assertIn("brevo.com", appels[0][0])
        self.assertEqual(json.loads(appels[0][2])["recipient"], "33612345678")
        self.assertIn("/Accounts/AC123/Messages.json", appels[1][0])
        self.assertTrue(appels[1][1]["Authorization"].startswith("Basic "))
        self.assertEqual(sms.envoyer({}, "+33612345678", "x"), (False, "SMS non configurés."))

    def test_prevenir_envoie_le_sms(self):
        comptes.changer_sms(self.db, self.cid, "0612345678", True)
        did = _creer(self.db, self.portal.app.config["FS_FILES"], self.cid)
        envoyes = []
        cfg = {"sms": {"actif": True, "fournisseur": "brevo", "cle": "k", "expediteur": "E85FRANCE"}}
        from fileservice import prevenir
        with mock.patch("sms.envoyer", lambda cfg, num, txt: envoyes.append((num, txt)) or (True, "")):
            info = prevenir(cfg, demandes.get(self.db, did), "fichier_pret", "https://x/f/F-00001")
            prevenir(cfg, demandes.get(self.db, did), "message", "https://x")        # pas de SMS pour un simple message
        self.assertEqual(envoyes, [("+33612345678", "E85-FRANCE : votre fichier F-00001 est prêt. https://x/f/F-00001")])
        self.assertIn("SMS envoyé", info)
        self.assertEqual(traductions.sms("info_requise", "en", atelier="A", numero="F-1", lien="L"),
                         "A: we need more information for your file F-1. L")


class AtelierPart3Tests(unittest.TestCase):
    def setUp(self):
        import app as outil
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR)
        outil.FS_DB, outil.FS_FILES = _base(t), os.path.join(t, "f")
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(t, "c.json"), t
        self.c = outil.app.test_client()
        self.cid = _client(outil.FS_DB)

    def tearDown(self):
        self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR = self.saved
        self.tmp.cleanup()

    def test_reglages_express_sms(self):
        self.assertEqual(self.c.post("/fs/reglages", json={"express": {"actif": True, "credits": "0"}}).status_code, 400)
        self.assertTrue(self.c.post("/fs/reglages", json={"express": {"actif": True, "credits": "25"}}).get_json()["ok"])
        self.assertEqual(self.c.get("/fs/reglages").get_json()["express"], {"actif": True, "credits": 25})
        r = self.c.post("/fs/reglages", json={"sms": {"actif": True, "fournisseur": "brevo", "cle": "k", "expediteur": "E85 FRANCE"}})
        self.assertEqual(r.status_code, 400)                                # espace interdit chez Brevo
        self.assertTrue(self.c.post("/fs/reglages", json={"sms": {"actif": True, "fournisseur": "brevo", "cle": "xkeysib-9",
                                                                  "expediteur": "E85FRANCE"}}).get_json()["ok"])
        g = self.c.get("/fs/reglages").get_json()["sms"]
        self.assertTrue(g["cle_set"])
        self.assertNotIn("cle", g)
        self.assertEqual(self.c.post("/fs/sms/test", json={"numero": "123"}).status_code, 400)

    def test_detail_annexes_et_express(self):
        did = _creer(self.o.FS_DB, self.o.FS_FILES, self.cid, express=20, annexes=[("gearbox.bin", b"\x08" * 10)])
        x = self.c.get(f"/fs/demandes/{did}").get_json()
        self.assertEqual(x["demande"]["express"], 1)
        aid = x["annexes"][0]["id"]
        r = self.c.get(f"/fs/demandes/{did}/annexe/{aid}")
        self.assertEqual(r.data, b"\x08" * 10)
        r.close()
        self.assertEqual(self.c.get(f"/fs/demandes/{did}/annexe/999").status_code, 404)
        self.assertIn("gearbox.bin", self.c.get(f"/fs/demandes/{did}/recapitulatif").get_data(as_text=True))
        comptes.creer_utilisateur(self.o.FS_DB, self.cid, nom="Thomas", email="t@garage.fr")
        self.assertEqual(self.c.get("/clients").get_json()["clients"][0]["utilisateurs"][0]["nom"], "Thomas")


import modeles
import statistiques


class StatistiquesModelesTests(unittest.TestCase):
    def setUp(self):
        import app as outil
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR)
        outil.FS_DB, outil.FS_FILES = _base(t), os.path.join(t, "f")
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(t, "c.json"), t
        self.c = outil.app.test_client()
        self.db = outil.FS_DB

    def tearDown(self):
        self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR = self.saved
        self.tmp.cleanup()

    def test_statistiques(self):
        import factures
        a = _client(self.db, credits=500)
        b = _client(self.db, email="b@garage.fr", credits=500)
        d1 = _creer(self.db, self.o.FS_FILES, a, prestations=("stage1", "e85"), garantie="g1")   # pack 99 + 20
        d2 = _creer(self.db, self.o.FS_FILES, b, express=20)                                      # 59 + 20
        d3 = _creer(self.db, self.o.FS_FILES, b)
        demandes.livrer(self.db, self.o.FS_FILES, d1, "x.bin", b"\x01")
        demandes.refuser(self.db, d3, "Illisible")
        with comptes.connect(self.db) as con:   # livré 3 h après la réception
            con.execute("UPDATE demandes SET cree_le = datetime(livre_le, '-3 hours') WHERE id = ?", (d1,))
        factures.enregistrer_manuel(self.db, comptes.get_client(self.db, a), credits=100, ht=250, designation="Pack",
                                    paiement="Virement", reference="V1", vendeur={})
        s = statistiques.calculer(self.db, 12)
        self.assertEqual(len(s["mois"]), 12)
        m = s["mois"][-1]
        self.assertEqual((m["demandes"], m["refusees"], m["express"], m["credits"], m["ca_ht"]), (3, 1, 1, 119 + 79, 250.0))
        self.assertAlmostEqual(m["delai_moyen_h"], 3.0, places=1)
        self.assertEqual(s["totaux"]["express_pct"], 33)
        noms = [p["nom"] for p in s["prestations"]]
        self.assertIn("Stage 1", noms)
        self.assertNotIn("Traitement express (prioritaire)", noms)
        self.assertFalse(any(n.startswith("Garantie") for n in noms))
        self.assertEqual(s["clients"][0]["credits"], 119)                  # le refus ne compte pas pour b
        self.assertEqual(self.c.get("/fs/statistiques?mois=6").get_json()["mois"].__len__(), 6)
        self.assertEqual(statistiques._mois(3, __import__("datetime").date(2026, 1, 15)), ["2025-11", "2025-12", "2026-01"])

    def test_modeles(self):
        l = self.c.get("/fs/modeles").get_json()["modeles"]
        self.assertTrue(len(l) >= 5)                                        # exemples proposés
        self.assertTrue(all("{" not in m["titre"] for m in l))
        r = self.c.post("/fs/modeles", json={"titre": "Relance", "texte": "Bonjour {contact}, …", "attente": True}).get_json()
        self.assertTrue(r["ok"])
        self.c.post("/fs/modeles", json={"id": r["id"], "titre": "Relance client", "texte": "Bonjour {contact} !"})
        m = next(x for x in self.c.get("/fs/modeles").get_json()["modeles"] if x["id"] == r["id"])
        self.assertEqual((m["titre"], m["attente"]), ("Relance client", 0))
        self.assertEqual(self.c.post("/fs/modeles", json={"titre": "", "texte": "x"}).status_code, 400)
        for x in self.c.get("/fs/modeles").get_json()["modeles"]:
            self.c.post("/fs/modeles/supprimer", json={"id": x["id"]})
        modeles.init_db(self.db)
        self.assertEqual(modeles.lister(self.db), [])                       # les exemples ne reviennent pas
        self.assertTrue(any(j["action"] == "Réponse type enregistrée" for j in
                            self.c.get("/equipe/journal").get_json()["journal"]))
