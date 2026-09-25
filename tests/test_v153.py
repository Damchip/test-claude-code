"""Tests v1.53 — pages légales, sauvegardes, comptabilité, RGPD."""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import catalogue
import comptes
import demandes
import factures
import pages_legales
from test_demandes import SIRET_OK, _base, _client, _creer


class PagesLegalesTests(unittest.TestCase):
    def test_modele_rempli_et_echappe(self):
        cfg = {"societe": {"raison_sociale": "E85 <SAS>", "siret": SIRET_OK}}
        html = str(pages_legales.rendre(cfg, "cgv"))
        self.assertIn("E85 &lt;SAS&gt;", html)            # jamais de HTML injecté
        self.assertIn("<h2>1. Objet et clientèle</h2>", html)
        self.assertIn("<ul><li>", html)
        self.assertIn("[capital à compléter]", html)
        self.assertTrue(pages_legales.a_completer(cfg, "cgv"))

    def test_texte_personnalise(self):
        cfg = {"pages": {"mentions-legales": "## Éditeur\n<script>x</script> {raison_sociale}"},
               "societe": {"raison_sociale": "E85"}}
        html = str(pages_legales.rendre(cfg, "mentions-legales"))
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("E85", html)

    def test_pages_publiques(self):
        import portal
        c = portal.app.test_client()
        for k in pages_legales.PAGES:
            r = c.get(f"/espace/legal/{k}")
            self.assertEqual(r.status_code, 200, k)
        self.assertEqual(c.get("/espace/legal/inconnue").status_code, 404)
        self.assertIn("/espace/legal/cgv", c.get("/espace/inscription").get_data(as_text=True))


class SauvegardeTests(unittest.TestCase):
    def test_copie_coherente_et_rotation(self):
        with tempfile.TemporaryDirectory() as t:
            db = _base(t)
            cid = _client(db)
            for _ in range(4):
                p = comptes.sauvegarder(db, garder=3)
            self.assertEqual(len(comptes.dernieres_sauvegardes(db, n=10)), 3)
            con = sqlite3.connect(p)
            self.assertEqual(con.execute("SELECT credits FROM clients WHERE id = ?", (cid,)).fetchone()[0], 200)
            con.close()
            self.assertIsNone(comptes.sauvegarder(os.path.join(t, "absente.db")))


class ComptaTests(unittest.TestCase):
    def test_export_et_synthese(self):
        with tempfile.TemporaryDirectory() as t:
            db = _base(t)
            c = comptes.get_client(db, _client(db, credits=0))
            factures.enregistrer_achat(db, c, catalogue.PACKS_CREDITS[3], paiement="Virement", reference="V1", vendeur={})
            files = os.path.join(t, "f")
            a = _creer(db, files, c["id"])
            b = _creer(db, files, c["id"])
            demandes.refuser(db, b, "illisible")            # remboursé : pas compté comme consommé
            csv = factures.export_csv(db)
            self.assertTrue(csv.startswith("﻿Numéro;"))
            self.assertIn(";1000,00;20 %;200,00;1200,00;", csv)
            self.assertEqual(factures.export_csv(db, "2000-01", "2000-12").count("\r\n"), 1)   # en-tête seul
            s = factures.synthese(db)
            m = s["mois"][0]
            self.assertEqual((m["factures"], m["ht"], m["credits_vendus"], m["credits_consommes"]), (1, 1000, 440, 59))
            self.assertEqual(s["credits_en_circulation"], 440 - 59)
            self.assertIsNotNone(a)


class RgpdTests(unittest.TestCase):
    def test_export_et_anonymisation(self):
        with tempfile.TemporaryDirectory() as t:
            db = _base(t)
            files = os.path.join(t, "fichiers")
            cid = _client(db)
            c = comptes.get_client(db, cid)
            fac = factures.enregistrer_manuel(db, c, credits=50, ht=125, designation="Pack", paiement="Virement",
                                              reference=None, vendeur={})
            did = _creer(db, files, cid, vehicule={"marque": "Audi", "vin": "WAUZZZ123"})
            demandes.ajouter_message(db, files, did, "client", "Jean", "secret", "pj.bin", b"x")

            data = demandes.export_client(db, cid)
            self.assertNotIn("mdp_hash", data["compte"])
            self.assertEqual(data["demandes"][0]["vehicule"]["vin"], "WAUZZZ123")
            self.assertEqual(data["demandes"][0]["messages"][0]["texte"], "secret")
            json.dumps(data)                                   # sérialisable

            demandes.anonymiser_client(db, files, cid)
            c2 = comptes.get_client(db, cid)
            self.assertEqual((c2["email"], c2["siret"], c2["credits"], c2["statut"]),
                             (f"supprime-{cid}@invalid", "", 0, "bloque"))
            self.assertIsNone(comptes.authentifier(db, "jean@garage.fr", "motdepasse-solide"))
            self.assertIsNone(comptes.authentifier(db, c2["email"], "!"))       # pas d'erreur sur le hash
            d = demandes.get(db, did)
            self.assertEqual(d["vehicule"], {})
            self.assertEqual(demandes.messages(db, did)[0]["texte"], "")
            self.assertFalse(os.path.exists(os.path.join(files, str(did))))
            f2 = factures.get(db, fac["numero"])                 # facture conservée telle quelle
            self.assertEqual(f2["client"]["societe"], "Garage Test")

    def test_routes(self):
        import app as outil
        import portal
        with tempfile.TemporaryDirectory() as t:
            saved_p = {k: portal.app.config[k] for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG")}
            saved_o = (outil.FS_DB, outil.FS_FILES)
            db = _base(t)
            files = os.path.join(t, "fichiers")
            portal.app.config.update(FS_DB=db, FS_FILES=files, FS_DATA_DIR=t, FS_CONFIG=os.path.join(t, "c.json"))
            outil.FS_DB, outil.FS_FILES = db, files
            try:
                portal.fileservice.LIMITEUR._echecs.clear()
                cid = _client(db)
                c = portal.app.test_client()
                tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/espace/connexion").get_data(as_text=True)).group(1)
                c.post("/espace/connexion", data={"csrf": tok, "email": "jean@garage.fr", "password": "motdepasse-solide"})
                r = c.get("/espace/parametres/mes-donnees")
                self.assertEqual(r.get_json()["compte"]["email"], "jean@garage.fr")
                self.assertIn("attachment", r.headers["Content-Disposition"])

                o = outil.app.test_client()
                self.assertEqual(o.post("/clients/supprimer", json={"id": cid}).status_code, 400)
                self.assertTrue(o.post("/clients/supprimer", json={"id": cid, "confirmation": "SUPPRIMER"}).get_json()["ok"])
                self.assertEqual(c.get("/espace/").status_code, 302)     # session du client tombée
                self.assertIn("text/csv", o.get("/fs/factures.csv?debut=2026-01&fin=2026-12").headers["Content-Type"])
                self.assertIn("credits_en_circulation", o.get("/fs/synthese").get_json())
                self.assertEqual(len(o.post("/fs/sauvegarde").get_json()["sauvegardes"]), 1)
            finally:
                portal.app.config.update(saved_p)
                outil.FS_DB, outil.FS_FILES = saved_o


class SecuriteTests(unittest.TestCase):
    def test_outil_interne_refuse_les_requetes_inter_sites(self):
        import app as outil
        c = outil.app.test_client()
        for h in ({"Origin": "https://pirate.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}):
            r = c.post("/fs/demandes/1/livrer", headers=h, data={})
            self.assertEqual(r.status_code, 403, h)
        # même origine (ou sans en-tête, ex. script local) : la requête passe au traitement normal
        self.assertNotEqual(c.post("/fs/demandes/999999/refuser", json={"motif": "x"},
                                   headers={"Origin": "http://localhost"}).status_code, 403)
        self.assertEqual(c.get("/fs/synthese", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 200)

    def test_sujet_mail_sans_retour_ligne(self):
        import mailer
        with tempfile.TemporaryDirectory() as t:
            ok, err = mailer.envoyer({}, "a@b.fr", "Inscription : Garage\r\nBcc: x@y.fr", "corps", journal_dir=t)
            self.assertFalse(ok)                                  # SMTP non configuré -> journal
            with open(os.path.join(t, "mails_non_envoyes.log"), encoding="utf-8") as fh:
                self.assertIn("Inscription : Garage Bcc: x@y.fr", fh.read())
            self.assertEqual(mailer.envoyer({}, "a@b.fr, c@d.fr", "s", "t")[1], "Destinataire invalide.")


if __name__ == "__main__":
    unittest.main()
