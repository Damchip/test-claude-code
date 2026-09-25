"""Tests des demandes, factures et paiement Stripe du fileservice."""
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import catalogue
import comptes
import demandes
import factures
import stripe_api

SIRET_OK = "73282932000074"


def _base(tmp):
    db = os.path.join(tmp, "fs.db")
    demandes.init_db(db)
    factures.init_db(db)
    return db


def _client(db, email="jean@garage.fr", credits=200, tva=""):
    cid = comptes.creer_client(db, societe="Garage Test", siret=SIRET_OK, tva=tva, contact="Jean Test",
                               email=email, tel="", mdp="motdepasse-solide", adresse="1 rue du Test",
                               code_postal="75001", ville="Paris")
    comptes.changer_statut(db, cid, "actif")
    if credits:
        comptes.mouvement(db, cid, credits, "Crédits de test")
    return cid


def _creer(db, files, cid, prestations=("stage1",), **kw):
    data = dict(categorie="vl", prestations=list(prestations), vehicule={"marque": "Audi", "modele": "A3"},
                fichier_nom="ori.bin", contenu=b"\x01" * 2048)
    data.update(kw)
    return demandes.creer(db, files, cid, **data)


class DemandesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _base(self.tmp.name)
        self.files = os.path.join(self.tmp.name, "fichiers")
        self.cid = _client(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_creation_debite_et_stocke(self):
        did = _creer(self.db, self.files, self.cid, prestations=["e85", "stage1"])
        d = demandes.get(self.db, did)
        self.assertEqual(d["numero"], f"F-{did:05d}")
        self.assertEqual(d["total"], 99)                       # tarif pack recalculé côté serveur
        self.assertEqual(comptes.get_client(self.db, self.cid)["credits"], 101)
        self.assertIsNotNone(demandes.chemin(self.files, did, "original_ori.bin"))
        self.assertEqual(comptes.mouvements(self.db, self.cid)[0]["montant"], -99)

    def test_solde_insuffisant_rien_garde(self):
        with self.assertRaises(comptes.ErreurCompte):
            # pack E85 + Stage 1 (99) + IMMO 59 + volet 39 + Start & Stop 29 = 226 > 200
            _creer(self.db, self.files, self.cid, prestations=["stage1", "immo", "e85", "volet_adm", "startstop"])
        # refusé : aucun débit, aucune demande
        self.assertEqual(comptes.get_client(self.db, self.cid)["credits"], 200)
        self.assertEqual(demandes.lister(self.db, self.cid), [])

    def test_refus_prestation_hors_categorie_et_fichier_vide(self):
        with self.assertRaises(comptes.ErreurCompte):
            _creer(self.db, self.files, self.cid, categorie="pl", prestations=["e85"])
        with self.assertRaises(comptes.ErreurCompte):
            _creer(self.db, self.files, self.cid, contenu=b"")
        with self.assertRaises(comptes.ErreurCompte):
            _creer(self.db, self.files, self.cid, prestations=[])

    def test_message_client_relance_une_demande_en_attente(self):
        did = _creer(self.db, self.files, self.cid)
        demandes.changer_statut(self.db, did, "attente")
        demandes.ajouter_message(self.db, self.files, did, "atelier", "Thomas", "Il manque l'EEPROM")
        self.assertEqual(demandes.lister(self.db, self.cid)[0]["non_lus"], 1)
        demandes.messages(self.db, did, marquer_lus_pour="client")
        self.assertEqual(demandes.lister(self.db, self.cid)[0]["non_lus"], 0)
        demandes.ajouter_message(self.db, self.files, did, "client", "Jean", "Voici", "eeprom.bin", b"\x00" * 10)
        self.assertEqual(demandes.get(self.db, did)["statut"], "en_cours")
        m = demandes.messages(self.db, did)[-1]
        self.assertTrue(m["pj_fichier"].startswith("pj_"))
        self.assertIsNotNone(demandes.chemin(self.files, did, m["pj_fichier"]))

    def test_livraison_versions_et_revision(self):
        did = _creer(self.db, self.files, self.cid)
        self.assertEqual(demandes.livrer(self.db, self.files, did, "mod.bin", b"\x02" * 2048), 1)
        d = demandes.get(self.db, did)
        self.assertEqual(d["statut"], "pret")
        self.assertIsNotNone(d["livre_le"])
        demandes.demander_revision(self.db, self.files, did, self.cid, "Jean", "Trou à 2000 tr/min")
        self.assertEqual(demandes.get(self.db, did)["statut"], "en_cours")
        self.assertEqual(demandes.livrer(self.db, self.files, did, "mod.bin", b"\x03" * 2048), 2)
        self.assertEqual([l["version"] for l in demandes.livrables(self.db, did)], [2, 1])

    def test_revision_hors_delai_refusee(self):
        did = _creer(self.db, self.files, self.cid)
        demandes.livrer(self.db, self.files, did, "mod.bin", b"\x02")
        with comptes.connect(self.db) as con:
            con.execute("UPDATE livrables SET cree_le = '2020-01-01 10:00:00'")
        with self.assertRaises(comptes.ErreurCompte):
            demandes.demander_revision(self.db, self.files, did, self.cid, "Jean", "Trop tard")

    def test_refus_rembourse_une_seule_fois(self):
        did = _creer(self.db, self.files, self.cid)
        demandes.refuser(self.db, did, "Lecture incomplète")
        demandes.refuser(self.db, did, "Encore")
        self.assertEqual(comptes.get_client(self.db, self.cid)["credits"], 200)
        with self.assertRaises(comptes.ErreurCompte):
            demandes.livrer(self.db, self.files, did, "mod.bin", b"\x02")
        with self.assertRaises(comptes.ErreurCompte):
            demandes.refuser(self.db, did, "")

    def test_refus_impossible_apres_livraison(self):
        did = _creer(self.db, self.files, self.cid)
        demandes.livrer(self.db, self.files, did, "mod.bin", b"\x02")
        with self.assertRaises(comptes.ErreurCompte):
            demandes.refuser(self.db, did, "trop tard")
        demandes.demander_revision(self.db, self.files, did, self.cid, "Jean", "à revoir")
        with self.assertRaises(comptes.ErreurCompte):      # en révision : toujours pas de remboursement
            demandes.refuser(self.db, did, "trop tard")
        self.assertEqual(comptes.get_client(self.db, self.cid)["credits"], 141)

    def test_chemin_ne_sort_pas_du_dossier(self):
        did = _creer(self.db, self.files, self.cid)
        self.assertIsNone(demandes.chemin(self.files, did, "../../fs.db"))
        self.assertEqual(demandes.nom_sur("../../etc/passwd"), "passwd")
        self.assertEqual(demandes.nom_sur("C:\\temp\\lecture ori.bin"), "lecture ori.bin")

    def test_stats(self):
        a = _creer(self.db, self.files, self.cid)
        _creer(self.db, self.files, self.cid)
        demandes.livrer(self.db, self.files, a, "mod.bin", b"\x02")
        st = demandes.stats(self.db, self.cid)
        self.assertEqual(st["counts"]["pret"], 1)
        self.assertEqual(st["ouverts"], 1)
        self.assertEqual(st["livres_mois"], 1)
        self.assertIsNotNone(st["delai_moyen"])


class FacturesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _base(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_achat_idempotent_et_numerotation(self):
        cid = _client(self.db, credits=0)
        c = comptes.get_client(self.db, cid)
        pack = catalogue.PACKS_CREDITS[3]  # 400 + 40
        f1 = factures.enregistrer_achat(self.db, c, pack, paiement="CB", reference="cs_1", vendeur={}, total_centimes=120000)
        self.assertTrue(f1["nouvelle"])
        self.assertEqual((f1["ht"], f1["tva"], f1["ttc"]), (1000, 200, 1200))
        again = factures.enregistrer_achat(self.db, c, pack, paiement="CB", reference="cs_1", vendeur={})
        self.assertFalse(again["nouvelle"])
        self.assertEqual(comptes.get_client(self.db, cid)["credits"], 440)   # crédité une seule fois
        f2 = factures.enregistrer_manuel(self.db, c, credits=50, ht=125, designation="Pack 50", paiement="Virement",
                                         reference="VIR-2", vendeur={})
        n1, n2 = int(f1["numero"][-4:]), int(f2["numero"][-4:])
        self.assertEqual(n2, n1 + 1)

    def test_montant_inattendu_refuse(self):
        c = comptes.get_client(self.db, _client(self.db, credits=0))
        with self.assertRaises(comptes.ErreurCompte):
            factures.enregistrer_achat(self.db, c, catalogue.PACKS_CREDITS[0], paiement="CB", reference="cs_x",
                                       vendeur={}, total_centimes=100)
        self.assertEqual(comptes.get_client(self.db, c["id"])["credits"], 0)

    def test_autoliquidation(self):
        self.assertFalse(factures.autoliquidation({"tva": "FR12345678901"}))
        self.assertFalse(factures.autoliquidation({"tva": ""}))
        self.assertTrue(factures.autoliquidation({"tva": "BE0123456789"}))
        c = comptes.get_client(self.db, _client(self.db, credits=0, tva="BE0123456789"))
        f = factures.enregistrer_manuel(self.db, c, credits=50, ht=125, designation="Pack", paiement="Virement",
                                        reference=None, vendeur={})
        self.assertEqual((f["tva"], f["ttc"]), (0, 125))
        self.assertIn("Autoliquidation", f["mention"])


class StripeTests(unittest.TestCase):
    CFG = {"secret_key": "sk_test_x", "webhook_secret": "whsec_test"}

    def test_signature_webhook(self):
        payload = b'{"type": "checkout.session.completed"}'
        t = str(int(time.time()))
        sig = stripe_api.signer("whsec_test", payload, t)
        ev = stripe_api.verifier_webhook(self.CFG, payload, f"t={t},v1={sig}")
        self.assertEqual(ev["type"], "checkout.session.completed")
        for entete in (f"t={t},v1={'0' * 64}", f"t={int(t) - 1000},v1={sig}", "", "v1=abc"):
            with self.assertRaises(stripe_api.ErreurStripe):
                stripe_api.verifier_webhook(self.CFG, payload, entete)
        with self.assertRaises(stripe_api.ErreurStripe):
            stripe_api.verifier_webhook(self.CFG, payload + b" ", f"t={t},v1={sig}")   # contenu modifié


class ParcoursPortailTests(unittest.TestCase):
    """Envoi d'un fichier, suivi, messages, téléchargements, isolation entre clients, paiement."""

    @classmethod
    def setUpClass(cls):
        import portal
        cls.portal = portal

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app = self.portal.app
        self.cfg_path = os.path.join(self.tmp.name, "portal_config.json")
        self.saved = {k: app.config[k] for k in ("FS_DB", "FS_DATA_DIR", "FS_CONFIG", "FS_PUBLIC_URL", "FS_FILES", "FS_DETECT")}
        app.config.update(FS_DB=_base(self.tmp.name), FS_DATA_DIR=self.tmp.name, FS_CONFIG=self.cfg_path,
                          FS_PUBLIC_URL="https://portail.exemple.fr", FS_FILES=os.path.join(self.tmp.name, "fichiers"),
                          FS_DETECT=lambda data, name: {"plateforme": "EDC17C64", "verdict": "compatible"})
        self.db = app.config["FS_DB"]
        self.portal.fileservice.LIMITEUR._echecs.clear()
        self.cid = _client(self.db)
        self.c = self._connecte("jean@garage.fr")

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def _connecte(self, email):
        c = self.portal.app.test_client()
        tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/connexion").get_data(as_text=True)).group(1)
        r = c.post("/connexion", data={"csrf": tok, "email": email, "password": "motdepasse-solide"})
        self.assertEqual(r.status_code, 302)
        return c

    def _csrf(self, c, path="/nouveau"):
        return re.search(r'name="csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)

    def _envoyer(self, prestas=("stage1",)):
        return self.c.post("/nouveau", data={
            "csrf": self._csrf(self.c), "categorie": "vl", "prestas": list(prestas), "marque": "Audi",
            "modele": "A3", "outil": "KESS3", "methode": "OBD",
            "file": (io.BytesIO(b"\x01" * 4096), "lecture_ori.bin")}, content_type="multipart/form-data")

    def test_envoi_et_suivi(self):
        r = self._envoyer(["e85", "stage1"])
        self.assertEqual(r.status_code, 302)
        d = demandes.lister(self.db, self.cid)[0]
        self.assertEqual(d["total"], 99)
        self.assertEqual(d["detection"]["plateforme"], "EDC17C64")
        page = self.c.get(f"/fichiers/{d['numero']}").get_data(as_text=True)
        self.assertIn("Pack E85 + débridage moteur", page)
        self.assertIn("EDC17C64", page)                 # calculateur détecté affiché
        self.assertNotIn("compatible", page)            # le verdict bibliothèque reste interne
        self.assertEqual(self.c.get(f"/fichiers/{d['numero']}/original").data, b"\x01" * 4096)
        self.assertIn(d["numero"], self.c.get("/").get_data(as_text=True))
        self.assertIn("101", self.c.get("/credits").get_data(as_text=True))

    def test_message_et_livraison(self):
        self._envoyer()
        d = demandes.lister(self.db, self.cid)[0]
        url = f"/fichiers/{d['numero']}"
        r = self.c.post(url + "/message", data={"csrf": self._csrf(self.c, url), "texte": "Échappement d'origine",
                                                "pj": (io.BytesIO(b"pj"), "photo.jpg")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        m = demandes.messages(self.db, d["id"])[0]
        self.assertEqual(self.c.get(f"{url}/pj/{m['id']}").data, b"pj")
        demandes.livrer(self.db, self.portal.app.config["FS_FILES"], d["id"], "mod.bin", b"\x09" * 100)
        self.assertEqual(self.c.get(f"{url}/livre/1").data, b"\x09" * 100)
        self.assertIsNotNone(demandes.get(self.db, d["id"])["telecharge_le"])
        self.assertIn("Demander une révision", self.c.get(url).get_data(as_text=True))

    def test_solde_insuffisant(self):
        comptes.mouvement(self.db, self.cid, -150, "vidage")
        r = self._envoyer()
        self.assertEqual(r.status_code, 302)
        self.assertIn("Solde insuffisant", self.c.get("/nouveau").get_data(as_text=True))
        self.assertEqual(demandes.lister(self.db, self.cid), [])

    def test_un_client_ne_voit_pas_les_fichiers_d_un_autre(self):
        self._envoyer()
        numero = demandes.lister(self.db, self.cid)[0]["numero"]
        _client(self.db, email="autre@garage.fr")
        autre = self._connecte("autre@garage.fr")
        for suffixe in ("", "/original", "/livre/1"):
            self.assertEqual(autre.get(f"/fichiers/{numero}{suffixe}").status_code, 404)
        self.assertNotIn(numero, autre.get("/fichiers").get_data(as_text=True))

    def test_parametres(self):
        tok = self._csrf(self.c, "/parametres")
        self.c.post("/parametres", data={"csrf": tok, "action": "profil", "contact": "Jean Nouveau",
                                                "tel": "01", "tva": "", "adresse": "2 rue Neuve",
                                                "code_postal": "69001", "ville": "Lyon", "pays": "France"})
        self.assertEqual(comptes.get_client(self.db, self.cid)["ville"], "Lyon")
        self.c.post("/parametres", data={"csrf": tok, "action": "mdp", "actuel": "motdepasse-solide",
                                                "nouveau": "encore-plus-solide", "nouveau2": "encore-plus-solide"})
        self.assertIsNotNone(comptes.authentifier(self.db, "jean@garage.fr", "encore-plus-solide"))
        self.assertEqual(self.c.get("/support").status_code, 200)

    def test_paiement_stripe(self):
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"stripe": {"secret_key": "sk_test_x", "webhook_secret": "whsec_test"},
                       "societe": {"raison_sociale": "E85 SAS", "siret": SIRET_OK}}, fh)
        tok = self._csrf(self.c, "/credits")
        with mock.patch.object(stripe_api, "creer_session", return_value="https://checkout.stripe.com/c/pay/x") as cs:
            r = self.c.post("/credits/acheter/3", data={"csrf": tok})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(cs.call_args.kwargs["montant_centimes"], 120000)     # 1000 € HT + 20 %
        self.assertEqual(cs.call_args.kwargs["reference"], f"{self.cid}:3")

        sess = {"id": "cs_test_1", "payment_status": "paid", "client_reference_id": f"{self.cid}:3",
                "amount_total": 120000}
        payload = json.dumps({"type": "checkout.session.completed", "data": {"object": sess}}).encode()
        t = str(int(time.time()))
        entete = f"t={t},v1={stripe_api.signer('whsec_test', payload, t)}"
        anon = self.portal.app.test_client()
        self.assertEqual(anon.post("/stripe/webhook", data=payload,
                                   headers={"Stripe-Signature": "t=1,v1=00"}).status_code, 400)
        for _ in range(2):   # Stripe peut renvoyer le même événement
            r = anon.post("/stripe/webhook", data=payload, headers={"Stripe-Signature": entete})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(comptes.get_client(self.db, self.cid)["credits"], 200 + 440)
        with mock.patch.object(stripe_api, "lire_session", return_value=sess):
            self.c.get("/credits/merci?session_id=cs_test_1")    # retour navigateur : pas de double crédit
        self.assertEqual(comptes.get_client(self.db, self.cid)["credits"], 640)
        fac = factures.lister(self.db, self.cid)[0]
        page = self.c.get(f"/factures/{fac['numero']}").get_data(as_text=True)
        self.assertIn("E85 SAS", page)
        self.assertIn("1\u202f200,00 €", page)


class OngletFileserviceTests(unittest.TestCase):
    """Routes /fs et /clients/facture de l'outil interne."""

    @classmethod
    def setUpClass(cls):
        import app as outil
        cls.o = outil

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        o = self.o
        self.saved = (o.FS_DB, o.FS_FILES, o.PORTAL_CONFIG_PATH, o.DATA_DIR)
        o.FS_DB = _base(self.tmp.name)
        o.FS_FILES = os.path.join(self.tmp.name, "fichiers")
        o.PORTAL_CONFIG_PATH = os.path.join(self.tmp.name, "portal_config.json")
        o.DATA_DIR = self.tmp.name
        self.cid = _client(o.FS_DB)
        self.did = _creer(o.FS_DB, o.FS_FILES, self.cid)
        self.c = o.app.test_client()

    def tearDown(self):
        self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR = self.saved
        self.tmp.cleanup()

    def _mails(self):
        p = os.path.join(self.tmp.name, "mails_non_envoyes.log")
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def test_traitement_complet(self):
        d = self.c.get("/fs/demandes").get_json()
        self.assertEqual(d["demandes"][0]["numero"], f"F-{self.did:05d}")
        self.assertEqual(self.c.get(f"/fs/demandes/{self.did}/original").data, b"\x01" * 2048)

        r = self.c.post(f"/fs/demandes/{self.did}/message", data={"texte": "Il manque l'EEPROM", "attente": "1",
                                                                   "auteur": "Thomas"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(demandes.get(self.o.FS_DB, self.did)["statut"], "attente")
        self.assertIn("information requise", self._mails())

        demandes.ajouter_message(self.o.FS_DB, self.o.FS_FILES, self.did, "client", "Jean", "Voilà")
        self.assertEqual(self.c.get("/fs/demandes").get_json()["demandes"][0]["non_lus"], 1)
        self.c.get(f"/fs/demandes/{self.did}")       # ouvrir la demande marque lu
        self.assertEqual(self.c.get("/fs/demandes").get_json()["demandes"][0]["non_lus"], 0)

        r = self.c.post(f"/fs/demandes/{self.did}/livrer", data={"file": (io.BytesIO(b"\x07" * 64), "mod.bin"),
                                                                  "note": "Stage 1 + checksum"},
                        content_type="multipart/form-data")
        self.assertEqual(r.get_json()["version"], 1)
        self.assertEqual(demandes.get(self.o.FS_DB, self.did)["statut"], "pret")
        self.assertIn("fichier prêt", self._mails())
        self.assertEqual(self.c.get(f"/fs/demandes/{self.did}/livre/1").data, b"\x07" * 64)
        r = self.c.post(f"/fs/demandes/{self.did}/refuser", json={"motif": "trop tard"})
        self.assertEqual(r.status_code, 400)          # déjà livré

    def test_refus_rembourse(self):
        r = self.c.post(f"/fs/demandes/{self.did}/refuser", json={"motif": "Lecture incomplète"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(comptes.get_client(self.o.FS_DB, self.cid)["credits"], 200)
        self.assertIn("remboursés", self._mails())
        self.assertEqual(self.c.post(f"/fs/demandes/{self.did}/refuser", json={"motif": ""}).status_code, 400)

    def test_facture_manuelle(self):
        body = {"id": self.cid, "credits": "440", "ht": "1000", "designation": "Pack 400", "paiement": "Virement",
                "reference": "VIR-1"}
        d = self.c.post("/clients/facture", json=body).get_json()
        self.assertTrue(d["nouvelle"])
        self.assertFalse(self.c.post("/clients/facture", json=body).get_json()["nouvelle"])   # même référence
        self.assertEqual(comptes.get_client(self.o.FS_DB, self.cid)["credits"], 141 + 440)
        page = self.c.get(f"/fs/factures/{d['numero']}").get_data(as_text=True)
        self.assertIn("1\u202f200,00 €", page)
        self.assertEqual(len(self.c.get(f"/clients/{self.cid}/factures").get_json()["factures"]), 1)
        self.assertEqual(self.c.post("/clients/facture", json={**body, "ht": "abc", "reference": "x"}).status_code, 400)

    def test_reglages(self):
        r = self.c.post("/fs/reglages", json={
            "societe": {"raison_sociale": "E85 SAS", "siret": SIRET_OK},
            "horaires": {"0": [9, 18], "6": None},
            "stripe": {"secret_key": "sk_test_abc", "webhook_secret": "whsec_abc"}})
        self.assertTrue(r.get_json()["ok"])
        d = self.c.get("/fs/reglages").get_json()
        self.assertEqual(d["societe"]["raison_sociale"], "E85 SAS")
        self.assertEqual(d["horaires"]["0"], [9, 18])
        self.assertEqual(d["stripe"]["mode"], "test")
        for url in ("/fs/reglages", "/portal-config"):
            txt = self.c.get(url).get_data(as_text=True)
            self.assertNotIn("sk_test_abc", txt)
            self.assertNotIn("whsec_abc", txt)
        self.assertEqual(self.c.post("/fs/reglages", json={"horaires": {"0": [18, 9]}}).status_code, 400)
        self.assertEqual(self.c.post("/fs/reglages", json={"stripe": {"secret_key": "pk_live_x"}}).status_code, 400)
        self.c.post("/fs/reglages", json={"stripe": {"secret_key": "-", "webhook_secret": "-"}})
        self.assertEqual(self.c.get("/fs/reglages").get_json()["stripe"]["mode"], "")


if __name__ == "__main__":
    unittest.main()
