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


if __name__ == "__main__":
    unittest.main()
