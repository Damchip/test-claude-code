"""Tests v1.57 — application (PWA) et notifications push, mise à jour du logiciel."""
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import comptes
import demandes
import mise_a_jour
import push
import traductions
from test_demandes import _base, _client, _creer

if push.DISPONIBLE:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def appareil():
    """Clés d'un faux navigateur : (clé privée, abonnement JSON)."""
    prive = ec.generate_private_key(ec.SECP256R1())
    pub = prive.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
    return prive, auth, {"endpoint": "https://fcm.googleapis.com/fcm/send/" + b(os.urandom(12)), "keys": {"p256dh": b(pub), "auth": b(auth)}}


def dechiffrer(corps, prive, auth):
    """Côté navigateur (RFC 8291) : pour vérifier que le message chiffré se relit."""
    sel, idlen = corps[:16], corps[20]
    serveur_pub = corps[21:21 + idlen]
    ua_pub = prive.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    partage = prive.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), serveur_pub))
    h = lambda s, k, i, n: HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=i).derive(k)
    ikm = h(auth, partage, b"WebPush: info\x00" + ua_pub + serveur_pub, 32)
    clair = AESGCM(h(sel, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(
        h(sel, ikm, b"Content-Encoding: nonce\x00", 12), corps[21 + idlen:], None)
    return clair.rstrip(b"\x00")[:-1]


@unittest.skipUnless(push.DISPONIBLE, "cryptography absent")
class ChiffrementTests(unittest.TestCase):
    def test_vecteur_rfc8291(self):
        serveur = ec.derive_private_key(int.from_bytes(push._unb64("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                        ec.SECP256R1())
        corps = push.chiffrer(b"When I grow up, I want to be a watermelon",
                              "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                              "BTBZMqHH6r4Tts7J_aSIgg", _prive_serveur=serveur, _sel=push._unb64("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(push._b64(corps),
                         "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_aller_retour_et_vapid(self):
        prive, auth, ab = appareil()
        corps = push.chiffrer("Fichier prêt".encode(), ab["keys"]["p256dh"], ab["keys"]["auth"])
        self.assertEqual(dechiffrer(corps, prive, auth).decode(), "Fichier prêt")
        with tempfile.TemporaryDirectory() as t:
            cfg = os.path.join(t, "c.json")
            cle, pub = push.cles(cfg)
            self.assertEqual(push.cles(cfg)[1], pub)                                  # gardée, pas recréée
            entete = push.jeton_vapid(cle, "https://fcm.googleapis.com/fcm/send/abc", "mailto:a@b.fr")
        jwt, k = re.fullmatch(r"vapid t=([^,]+), k=(\S+)", entete).groups()
        self.assertEqual(k, pub)
        h, c, sig = jwt.split(".")
        self.assertEqual(json.loads(push._unb64(c))["aud"], "https://fcm.googleapis.com")
        sig = push._unb64(sig)
        cle.public_key().verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                                f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))       # lève si la signature est fausse


@unittest.skipUnless(push.DISPONIBLE, "cryptography absent")
class AbonnementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _base(self.tmp.name)
        self.cfg = os.path.join(self.tmp.name, "c.json")
        push.cles(self.cfg)
        self.cid = _client(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_abonner_envoyer_nettoyer(self):
        p1, a1, ab1 = appareil()
        _, _, ab2 = appareil()
        push.abonner(self.db, self.cid, ab1)
        push.abonner(self.db, self.cid, ab1)                                         # même appareil : une seule ligne
        push.abonner(self.db, self.cid, ab2)
        self.assertEqual(push.nombre(self.db, self.cid), 2)
        for mauvais in ({"endpoint": "http://x", "keys": ab1["keys"]}, {"endpoint": "https://127.0.0.1/admin", "keys": ab1["keys"]},
                        {"endpoint": "https://fcm.googleapis.com.pirate.fr/x", "keys": ab1["keys"]}, {"endpoint": ab1["endpoint"], "keys": {"p256dh": "x", "auth": "y"}}, "x"):
            with self.assertRaises(push.ErreurPush):
                push.abonner(self.db, self.cid, mauvais)
        envois = []

        def poster(url, corps, entetes):
            envois.append((url, corps, entetes))
            return 201 if url == ab1["endpoint"] else 410                               # le 2e appareil a été désinstallé
        with mock.patch("push._poster", poster):
            n = push.envoyer(self.db, self.cfg, self.cid, {"titre": "Fichier F-00001 prêt", "url": "/fichiers/F-00001"})
        self.assertEqual(n, 1)
        self.assertEqual(push.nombre(self.db, self.cid), 1)                            # abonnement expiré retiré
        url, corps, entetes = next(e for e in envois if e[0] == ab1["endpoint"])
        self.assertEqual(entetes["Content-Encoding"], "aes128gcm")
        self.assertEqual(json.loads(dechiffrer(corps, p1, a1))["url"], "/fichiers/F-00001")

    def test_utilisateurs_et_rgpd(self):
        uid = comptes.creer_utilisateur(self.db, self.cid, nom="Thomas", email="t@garage.fr")
        _, _, ab = appareil()
        push.abonner(self.db, self.cid, ab, utilisateur_id=uid)
        self.assertEqual(push.nombre(self.db, self.cid), 1)
        comptes.modifier_utilisateur(self.db, self.cid, uid, actif=False)
        self.assertEqual(push.nombre(self.db, self.cid), 0)                            # désactivé : plus rien
        comptes.modifier_utilisateur(self.db, self.cid, uid, actif=True)
        comptes.supprimer_utilisateur(self.db, self.cid, uid)
        self.assertEqual(push.abonnements(self.db, self.cid), [])
        _, _, ab = appareil()
        push.abonner(self.db, self.cid, ab)
        demandes.anonymiser_client(self.db, os.path.join(self.tmp.name, "f"), self.cid)
        self.assertEqual(push.abonnements(self.db, self.cid), [])

    def test_sans_cle_rien_ne_part(self):
        self.assertEqual(push.envoyer(self.db, os.path.join(self.tmp.name, "autre.json"), self.cid, {"titre": "x"}), 0)

    def test_textes(self):
        self.assertEqual(traductions.push("fichier_pret", "fr", numero="F-00012", vehicule="Peugeot 308"),
                         ("Fichier F-00012 prêt", "Peugeot 308 · touchez pour le télécharger."))
        self.assertEqual(traductions.push("refus", "en", numero="F-1")[1], "your vehicle · your credits have been refunded.")


@unittest.skipUnless(push.DISPONIBLE, "cryptography absent")
class PwaPortailTests(unittest.TestCase):
    def setUp(self):
        import portal
        self.portal = portal
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.cfg = os.path.join(t, "c.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"societe": {"email": "contact@e85.fr"}}, fh)
        app = portal.app
        self.saved = {k: app.config.get(k) for k in ("FS_DB", "FS_FILES", "FS_DATA_DIR", "FS_CONFIG", "FS_DETECT")}
        app.config.update(FS_DB=_base(t), FS_FILES=os.path.join(t, "f"), FS_DATA_DIR=t, FS_CONFIG=self.cfg, FS_DETECT=None)
        self.db = app.config["FS_DB"]
        self.cid = _client(self.db)
        portal.fileservice.LIMITEUR._echecs.clear()
        self.c = app.test_client()

    def tearDown(self):
        self.portal.app.config.update(self.saved)
        self.tmp.cleanup()

    def tok(self, p="/parametres"):
        return re.search(r'name="csrf" value="([^"]+)"', self.c.get(p).get_data(as_text=True)).group(1)

    def connecter(self):
        self.c.post("/connexion", data={"csrf": self.tok("/connexion"), "email": "jean@garage.fr", "password": "motdepasse-solide"})

    def test_fichiers_publics(self):
        m = self.c.get("/manifest.webmanifest")
        self.assertEqual(m.mimetype, "application/manifest+json")
        d = m.get_json(force=True)
        self.assertEqual((d["display"], d["scope"], d["start_url"][:1]), ("standalone", "/", "/"))
        self.assertTrue(any(i["purpose"] == "maskable" for i in d["icons"]))
        for i in d["icons"]:
            r = self.c.get(i["src"])
            self.assertEqual(r.status_code, 200, i["src"])
            r.close()
        sw = self.c.get("/sw.js")
        self.assertEqual((sw.status_code, sw.mimetype, sw.headers["Service-Worker-Allowed"]), (200, "application/javascript", "/"))
        js = sw.get_data(as_text=True)
        self.assertIn("fs-" + self.portal.APP_VERSION, js)                           # cache lié à la version
        self.assertIn("showNotification", js)
        self.assertIn("hors-ligne", js)
        self.assertEqual(self.c.get("/hors-ligne").status_code, 200)
        self.assertIn('rel="manifest"', self.c.get("/connexion").get_data(as_text=True))

    def test_abonnement_depuis_l_espace_client(self):
        self.assertEqual(self.c.get("/push/cle").status_code, 302)                   # connexion requise
        self.connecter()
        cle = self.c.get("/push/cle").get_json()
        self.assertTrue(cle["disponible"])
        with open(self.cfg, encoding="utf-8") as fh:
            self.assertIn("vapid_prive", json.load(fh)["push"])                        # créée et gardée hors git
        _, _, ab = appareil()
        self.assertEqual(self.c.post("/push/abonnement", json={"abonnement": ab}).status_code, 400)   # sans jeton CSRF
        tok = self.tok()
        r = self.c.post("/push/abonnement", json={"abonnement": ab}, headers={"X-CSRF-Token": tok})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(push.nombre(self.db, self.cid), 1)
        with mock.patch("push._poster", lambda *a: 201):
            self.assertEqual(self.c.post("/push/test", headers={"X-CSRF-Token": tok}).get_json()["appareils"], 1)
        # renouvellement par le service worker (sans session) : l'ancienne adresse fait foi
        _, _, ab2 = appareil()
        anon = self.portal.app.test_client()
        self.assertEqual(anon.post("/push/renouveler", json={"ancien": "https://inconnu", "nouveau": ab2}).status_code, 404)
        self.assertTrue(anon.post("/push/renouveler", json={"ancien": ab["endpoint"], "nouveau": ab2}).get_json()["ok"])
        self.assertEqual([a["endpoint"] for a in push.abonnements(self.db, self.cid)], [ab2["endpoint"]])
        self.c.post("/push/desabonnement", json={"endpoint": ab2["endpoint"]}, headers={"X-CSRF-Token": tok})
        self.assertEqual(push.nombre(self.db, self.cid), 0)
        self.assertIn("data-push-root", self.c.get("/parametres").get_data(as_text=True))

    def test_prevenir_envoie_la_notification(self):
        from fileservice import prevenir
        did = _creer(self.db, self.portal.app.config["FS_FILES"], self.cid)
        envois = []
        with mock.patch("push.envoyer", lambda db, cfg, cid, notif, sujet="": envois.append((cid, notif, sujet)) or 2):
            with open(self.cfg, encoding="utf-8") as fh:
                cfg = json.load(fh)
            info = prevenir(cfg, demandes.get(self.db, did), "fichier_pret", "https://x/f", db_path=self.db, config_path=self.cfg)
            prevenir(cfg, demandes.get(self.db, did), "facture", "https://x", db_path=self.db, config_path=self.cfg)
        self.assertEqual(len(envois), 1)                                              # pas de notification pour une facture
        cid, notif, sujet = envois[0]
        self.assertEqual((cid, notif["titre"], notif["url"], sujet), (self.cid, "Fichier F-00001 prêt", "/fichiers/F-00001",
                                                                      "mailto:contact@e85.fr"))
        self.assertIn("Audi A3", notif["texte"])
        self.assertIn("2 appareil(s)", info)


# --- Mise à jour ------------------------------------------------------------------

def fichiers_du_projet():
    r = subprocess.run(["git", "-C", ROOT, "ls-files", "--cached", "--others", "--exclude-standard"],
                       capture_output=True, text=True, check=True)
    return [f for f in r.stdout.split("\n") if f and not f.startswith(("tests/", ".github/", "docs/")) and os.path.isfile(os.path.join(ROOT, f))]


def archive(version=None, modifier=None, retirer=(), ajouter=None, prefixe="carto_matcher/"):
    """Zip de release fabriqué depuis le projet (comme le workflow), éventuellement modifié."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in fichiers_du_projet():
            if f in retirer:
                continue
            with open(os.path.join(ROOT, f), "rb") as fh:
                data = fh.read()
            if f == "app.py" and version:
                data = re.sub(rb'APP_VERSION = "[\d.]+"', f'APP_VERSION = "{version}"'.encode(), data)
            if modifier and f in modifier:
                data = modifier[f](data)
            z.writestr(prefixe + f, data)
        for nom, data in (ajouter or {}).items():
            z.writestr(prefixe + nom, data)
    return buf.getvalue()


class ControleArchiveTests(unittest.TestCase):
    def test_archive_valide(self):
        v, fichiers = mise_a_jour.controler_zip(archive(version="9.9.9"))
        self.assertEqual(v, "9.9.9")
        self.assertIn("mise_a_jour.py", fichiers)

    def test_archives_refusees(self):
        cas = {
            "pas un zip": b"bonjour",
            "chemin": archive(ajouter={"../evil.py": b"x"}),
            "data": archive(ajouter={"data/fileservice.db": b"x"}),
            "incomplète": archive(retirer=("portal.py",)),
            "sans dossier": archive(prefixe=""),
        }
        for nom, z in cas.items():
            with self.subTest(nom), self.assertRaises(mise_a_jour.ErreurMaj):
                mise_a_jour.controler_zip(z)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            info = zipfile.ZipInfo("carto_matcher/lien")
            info.external_attr = (0o120777 << 16)
            z.writestr(info, "/etc/passwd")
        with self.assertRaises(mise_a_jour.ErreurMaj):
            mise_a_jour.controler_zip(buf.getvalue())

    def test_versions(self):
        self.assertTrue(mise_a_jour.plus_recente("v1.10.0", "1.9.9"))
        self.assertFalse(mise_a_jour.plus_recente("1.57.0", "1.57.0"))


class InstallationTests(unittest.TestCase):
    """Installe réellement dans un dossier temporaire (jamais dans le projet)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.racine = os.path.join(self.tmp.name, "carto_matcher")
        with zipfile.ZipFile(io.BytesIO(archive())) as z:
            z.extractall(self.tmp.name)
        self.data = os.path.join(self.racine, "data")
        os.makedirs(self.data)
        with open(os.path.join(self.data, "fileservice.db"), "wb") as fh:
            fh.write(b"mes donnees")
        with open(os.path.join(self.racine, "reglage_perso.txt"), "w") as fh:
            fh.write("fichier ajouté par l'atelier")

    def tearDown(self):
        self.tmp.cleanup()

    def lire(self, *p):
        with open(os.path.join(self.racine, *p), encoding="utf-8") as fh:
            return fh.read()

    def test_installer_puis_revenir(self):
        avant = mise_a_jour.version_locale(self.racine)
        z1 = archive(version="9.0.0", ajouter={"nouveau_module.py": b"X = 1\n"})
        r = mise_a_jour.installer_zip(z1, self.data, self.racine)
        self.assertEqual((r["version"], r["precedente"]), ("9.0.0", avant))
        self.assertEqual(mise_a_jour.version_locale(self.racine), "9.0.0")
        self.assertTrue(os.path.isfile(os.path.join(self.racine, "nouveau_module.py")))
        self.assertEqual(open(os.path.join(self.data, "fileservice.db"), "rb").read(), b"mes donnees")   # données intactes
        self.assertTrue(os.path.isfile(os.path.join(self.racine, "tmp", "restart.txt")))                  # Passenger
        self.assertTrue(os.path.isfile(os.path.join(self.racine, "deploy", "atelier", "tmp", "restart.txt")))
        # 2e mise à jour : un fichier retiré par la nouvelle version disparaît, pas ceux de l'atelier
        mise_a_jour.installer_zip(archive(version="9.1.0"), self.data, self.racine, essai=False)
        self.assertFalse(os.path.exists(os.path.join(self.racine, "nouveau_module.py")))
        self.assertTrue(os.path.exists(os.path.join(self.racine, "reglage_perso.txt")))
        self.assertEqual(len([f for f in os.listdir(os.path.join(self.data, "mises_a_jour")) if f.startswith("code-")]), 2)
        res = mise_a_jour.revenir(self.data, self.racine)
        self.assertEqual(res["version"], "9.0.0")
        self.assertEqual(mise_a_jour.version_locale(self.racine), "9.0.0")
        self.assertIn("installée", mise_a_jour.etat(self.data)["journal"][-2]["texte"])

    def test_nouvelle_version_cassee_rien_ne_change(self):
        avant = self.lire("portal.py")
        casse = archive(version="9.0.0", modifier={"portal.py": lambda d: d + b"\nimport module_qui_n_existe_pas\n"})
        with self.assertRaises(mise_a_jour.ErreurMaj) as e:
            mise_a_jour.installer_zip(casse, self.data, self.racine)
        self.assertIn("ne démarre pas", str(e.exception))
        self.assertEqual(self.lire("portal.py"), avant)
        self.assertNotEqual(mise_a_jour.version_locale(self.racine), "9.0.0")
        self.assertFalse([d for d in os.listdir(os.path.join(self.data, "mises_a_jour")) if d.startswith("staging-")])

    def test_dependances_en_echec(self):
        z = archive(version="9.0.0", modifier={"requirements.txt": lambda d: d + b"paquet-imaginaire==0\n"})
        faux = subprocess.CompletedProcess([], 1, stdout="", stderr="No matching distribution found")
        with mock.patch("subprocess.run", return_value=faux) as run:
            with self.assertRaises(mise_a_jour.ErreurMaj):
                mise_a_jour.installer_zip(z, self.data, self.racine)
        self.assertIn("pip", run.call_args[0][0])
        self.assertNotEqual(mise_a_jour.version_locale(self.racine), "9.0.0")


class EnvironnementsTests(unittest.TestCase):
    def test_virtualenvs_cpanel(self):
        with tempfile.TemporaryDirectory() as maison:
            racine = os.path.join(maison, "carto_matcher")
            for app_rel in ("carto_matcher", "carto_matcher/deploy/atelier"):
                py = os.path.join(maison, "virtualenv", app_rel, "3.11", "bin", "python")
                os.makedirs(os.path.dirname(py))
                open(py, "w").close()
            with mock.patch.dict(os.environ, {"HOME": maison}):
                envs = mise_a_jour.environnements_python(racine)
        self.assertEqual(envs[0], sys.executable)
        self.assertEqual(len(envs), 3)                                   # le courant + portail + atelier
        self.assertTrue(any("deploy/atelier/3.11" in e.replace(os.sep, "/") for e in envs))


class GithubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def faux_github(self, zip_contenu, sha=None, version="9.9.9"):
        release = {"tag_name": f"v{version}", "body": "## v9.9.9\n- nouveautés", "html_url": "https://github.com/x/y/releases/v9",
                   "published_at": "2026-10-01T10:00:00Z",
                   "assets": [{"name": f"carto_matcher_v{version}.zip", "url": "https://api.github.com/assets/1"},
                              {"name": f"carto_matcher_v{version}.zip.sha256", "url": "https://api.github.com/assets/2"}]}

        def requete(url, jeton="", accept="", taille_max=0):
            if url.endswith("/releases/latest"):
                return json.dumps(release).encode()
            if url.endswith("/1"):
                return zip_contenu
            return ((sha or hashlib.sha256(zip_contenu).hexdigest()) + "  carto_matcher.zip\n").encode()
        return mock.patch("mise_a_jour._requete", requete)

    def test_verifier_et_empreinte(self):
        with self.faux_github(b"contenu"):
            v = mise_a_jour.verifier({}, self.data)
            self.assertTrue(v["nouvelle"])
            self.assertEqual(mise_a_jour.telecharger({}, v["release"]), b"contenu")
        with self.faux_github(b"contenu", sha="0" * 64):
            with self.assertRaises(mise_a_jour.ErreurMaj):
                mise_a_jour.telecharger({}, mise_a_jour.verifier({}, self.data)["release"])
        self.assertEqual(mise_a_jour.etat(self.data)["derniere_version"], "9.9.9")
        with self.assertRaises(mise_a_jour.ErreurMaj):
            mise_a_jour.derniere_release("pas un dépôt")

    def test_automatique(self):
        envois = []
        envoyer = lambda sujet, texte: envois.append(sujet)
        with self.faux_github(b"z"), mock.patch("mise_a_jour.mode_git", return_value=False):
            self.assertEqual(mise_a_jour.automatique({}, self.data, heure=14, envoyer=envoyer), "disponible")
            self.assertIsNone(mise_a_jour.automatique({}, self.data, heure=15, envoyer=envoyer))   # une vérification par jour
            with mock.patch("mise_a_jour.installer_zip", return_value={"version": "9.9.9", "precedente": "1.57.0"}) as inst:
                cfg = {"mise_a_jour": {"auto": True}}
                self.assertEqual(mise_a_jour.automatique(cfg, self.data, heure=3, envoyer=envoyer), "installee")
                inst.assert_called_once()
        self.assertEqual(envois, ["Nouvelle version 9.9.9 disponible", "Version 9.9.9 installée automatiquement"])


class RoutesMajTests(unittest.TestCase):
    def setUp(self):
        import app as outil
        self.o = outil
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = (outil.FS_DB, outil.FS_FILES, outil.PORTAL_CONFIG_PATH, outil.DATA_DIR, outil.CONFIG_PATH)
        outil.FS_DB, outil.FS_FILES = _base(t), os.path.join(t, "f")
        outil.PORTAL_CONFIG_PATH, outil.DATA_DIR = os.path.join(t, "c.json"), t
        outil.CONFIG_PATH = os.path.join(t, "config.json")
        with open(outil.CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"secret_key": "x"}, fh)
        outil.LIMITEUR_OUTIL._echecs.clear()
        self.racine = os.path.join(t, "installation")
        with zipfile.ZipFile(io.BytesIO(archive())) as z:
            z.extractall(t)
        os.rename(os.path.join(t, "carto_matcher"), self.racine)
        p = mock.patch("mise_a_jour.RACINE", self.racine)     # jamais le vrai dossier du projet
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.o.FS_DB, self.o.FS_FILES, self.o.PORTAL_CONFIG_PATH, self.o.DATA_DIR, self.o.CONFIG_PATH = self.saved
        self.tmp.cleanup()

    def test_droits_et_installation_zip(self):
        c = self.o.app.test_client()
        c.post("/equipe/creer", json={"identifiant": "damien", "nom": "Damien", "mdp": "admin-solide-1"})
        c.post("/equipe/creer", json={"identifiant": "thomas", "nom": "Thomas", "role": "technicien", "mdp": "tech-solide-12"})
        t = self.o.app.test_client()
        t.post("/login", data={"identifiant": "thomas", "password": "tech-solide-12"})
        self.assertEqual(t.get("/maj").status_code, 403)                               # administrateurs seulement
        d = c.get("/maj").get_json()
        self.assertEqual(d["reglages"]["depot"], mise_a_jour.DEPOT_DEFAUT)
        z = archive(version="9.0.0")
        r = c.post("/maj/zip", data={"zip": (io.BytesIO(z), "maj.zip"), "mdp": "mauvais"}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)                                           # mot de passe redemandé
        r = c.post("/maj/zip", data={"zip": (io.BytesIO(z), "maj.zip"), "mdp": "admin-solide-1"}, content_type="multipart/form-data")
        self.assertTrue(r.get_json()["ok"], r.get_json())
        self.assertEqual(mise_a_jour.version_locale(self.racine), "9.0.0")
        self.assertEqual(mise_a_jour.version_locale(ROOT), self.o.APP_VERSION)         # le vrai projet n'a pas bougé
        self.assertTrue(any(j["action"] == "Mise à jour du logiciel (fichier .zip)" for j in c.get("/equipe/journal").get_json()["journal"]))
        r = c.post("/maj/revenir", json={"mdp": "admin-solide-1"}).get_json()
        self.assertEqual(r["version"], self.o.APP_VERSION)

    def test_reglages(self):
        c = self.o.app.test_client()
        self.assertEqual(c.post("/maj/reglages", json={"depot": "pas bon"}).status_code, 400)
        c.post("/maj/reglages", json={"depot": "E85/fileservice", "jeton": "github_pat_secret", "auto": True})
        r = c.get("/maj").get_json()["reglages"]
        self.assertEqual((r["depot"], r["auto"], r["jeton_set"]), ("E85/fileservice", True, True))
        self.assertNotIn("jeton", r)
        c.post("/maj/reglages", json={"depot": "E85/fileservice", "jeton": ""})
        with open(self.o.PORTAL_CONFIG_PATH, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["mise_a_jour"]["jeton"], "github_pat_secret")  # vide = inchangé


if __name__ == "__main__":
    unittest.main()
