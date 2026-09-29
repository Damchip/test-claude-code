"""
Passerelle PC atelier ↔ fileservice en ligne.

La bibliothèque de solutions et ses fichiers .bin restent sur le PC de l'atelier (OneDrive…). Le
Carto Matcher du PC se connecte au fileservice en ligne (connexion SORTANTE en HTTPS : aucun port à
ouvrir) avec une clé créée dans l'outil atelier en ligne, puis :
  - liste les demandes à traiter ;
  - télécharge la lecture d'origine du client ;
  - prépare le fichier EN LOCAL (livraison en un clic, Auto-patch…) avec la bibliothèque du PC ;
  - renvoie uniquement le fichier modifié, livré au client comme depuis l'outil en ligne.
Option : le PC livre tout seul les demandes « propres » dès qu'elles arrivent.

Côté serveur : table `passerelle_cles` (empreinte SHA-256 seulement, jamais la clé en clair).
Côté PC : `Client` (urllib, aucune dépendance), réglages dans data/config.json du PC.
"""
import datetime as dt
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from comptes import ErreurCompte, connect

PREFIXE = "e85pc_"
MAX_CLES = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS passerelle_cles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL,
    empreinte TEXT NOT NULL UNIQUE,
    prefixe TEXT NOT NULL,
    cree_le TEXT NOT NULL,
    derniere_utilisation TEXT,
    derniere_ip TEXT NOT NULL DEFAULT '',
    revoquee INTEGER NOT NULL DEFAULT 0
);
"""


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _empreinte(cle):
    return hashlib.sha256((cle or "").encode()).hexdigest()


# --- Serveur : clés ----------------------------------------------------------------

def init_db(db_path):
    with connect(db_path) as con:
        con.executescript(SCHEMA)


def creer_cle(db_path, nom):
    nom = (nom or "").strip()[:60] or "PC atelier"
    with connect(db_path) as con:
        n = con.execute("SELECT COUNT(*) n FROM passerelle_cles WHERE revoquee = 0").fetchone()["n"]
        if n >= MAX_CLES:
            raise ErreurCompte(f"{MAX_CLES} postes connectés au maximum : révoque une clé.")
        cle = PREFIXE + secrets.token_urlsafe(32)
        con.execute("INSERT INTO passerelle_cles (nom, empreinte, prefixe, cree_le) VALUES (?, ?, ?, ?)",
                    (nom, _empreinte(cle), cle[:12], _now()))
    return cle


def lister_cles(db_path):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT id, nom, prefixe, cree_le, derniere_utilisation, derniere_ip FROM passerelle_cles"
            " WHERE revoquee = 0 ORDER BY id")]


def revoquer_cle(db_path, cle_id):
    with connect(db_path) as con:
        con.execute("UPDATE passerelle_cles SET revoquee = 1 WHERE id = ?", (cle_id,))


def verifier_cle(db_path, cle, ip=""):
    """Renvoie la clé (dict) si valide, sinon None. Note la dernière utilisation."""
    if not (cle or "").startswith(PREFIXE):
        return None
    with connect(db_path) as con:
        r = con.execute("SELECT id, nom FROM passerelle_cles WHERE empreinte = ? AND revoquee = 0",
                        (_empreinte(cle),)).fetchone()
        if not r:
            return None
        con.execute("UPDATE passerelle_cles SET derniere_utilisation = ?, derniere_ip = ? WHERE id = ?",
                    (_now(), (ip or "")[:60], r["id"]))
    return dict(r)


def demande_publique(d, nb_livrables=0):
    """Ce que le PC reçoit d'une demande : de quoi préparer le fichier, rien de plus (pas d'e-mail, de tél.)."""
    return {"id": d["id"], "numero": d["numero"], "societe": d["societe"], "statut": d["statut"],
            "statut_label": d.get("statut_label"), "cree_le": d["cree_le"], "categorie": d["categorie"],
            "prestations": d["prestations"], "lignes": d["lignes"], "siege": d["siege"], "express": d.get("express", 0),
            "vehicule": d["vehicule"], "lecture": d["lecture"], "detection": d.get("detection") or {},
            "commentaire": d.get("commentaire", ""), "fichier_nom": d["fichier_nom"], "fichier_taille": d["fichier_taille"],
            "livrables": nb_livrables, "non_lus": d.get("non_lus", 0)}


# --- PC : client -------------------------------------------------------------------

class ErreurPasserelle(RuntimeError):
    pass


class Client:
    def __init__(self, url, cle, timeout=60):
        url = (url or "").strip().rstrip("/")
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("https", "http") or not u.netloc:
            raise ErreurPasserelle("Adresse de l'outil en ligne invalide (ex. https://atelier.e85france.fr).")
        if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost"):
            raise ErreurPasserelle("L'adresse doit commencer par https:// (la clé ne doit jamais circuler en clair).")
        if not (cle or "").startswith(PREFIXE):
            raise ErreurPasserelle("Clé de passerelle invalide (elle commence par e85pc_).")
        self.url, self.cle, self.timeout = url, cle, timeout

    def _req(self, methode, chemin, corps=None, entetes=None, brut=False):
        h = {"Authorization": "Bearer " + self.cle, "User-Agent": "carto-matcher-passerelle", "Accept": "application/json"}
        h.update(entetes or {})
        req = urllib.request.Request(self.url + "/passerelle/v1" + chemin, data=corps, headers=h, method=methode)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error")
            except ValueError:
                msg = None
            raise ErreurPasserelle(msg or {401: "Clé refusée par le serveur (révoquée ?).", 404: "Introuvable (adresse ?)."}
                                   .get(e.code, f"Le serveur a répondu {e.code}."))
        except (urllib.error.URLError, OSError) as e:
            raise ErreurPasserelle(f"Serveur injoignable : {getattr(e, 'reason', e)}")
        if brut:
            return data
        try:
            return json.loads(data)
        except ValueError:
            raise ErreurPasserelle("Réponse inattendue du serveur (adresse de l'outil atelier ?).")

    def etat(self):
        return self._req("GET", "/etat")

    def demandes(self, tous=False):
        return self._req("GET", "/demandes" + ("?tous=1" if tous else ""))["demandes"]

    def demande(self, did):
        return self._req("GET", f"/demandes/{int(did)}")

    def original(self, did):
        return self._req("GET", f"/demandes/{int(did)}/original", brut=True)

    def livrer(self, did, nom, contenu, note="", auteur=""):
        limite = f"----carto{uuid.uuid4().hex}"
        parties = []
        for k, v in (("note", note), ("auteur", auteur)):
            parties.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        nom_sur = (nom or "fichier.bin").replace('"', "_").replace("\r", "").replace("\n", "")
        parties.append(f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="{nom_sur}"\r\n'
                       "Content-Type: application/octet-stream\r\n\r\n".encode() + contenu + b"\r\n")
        corps = b"".join(parties) + f"--{limite}--\r\n".encode()
        return self._req("POST", f"/demandes/{int(did)}/livrer", corps,
                         {"Content-Type": f"multipart/form-data; boundary={limite}"})

    def en_traitement(self, did):
        return self._req("POST", f"/demandes/{int(did)}/statut", json.dumps({"statut": "en_cours"}).encode(),
                         {"Content-Type": "application/json"})


# --- PC : livraison automatique ---------------------------------------------------

class Automate:
    """Sur le PC : toutes les `intervalle` secondes, prépare et livre les nouvelles demandes « propres ».
    Chaque demande n'est tentée qu'une fois (état dans data/passerelle_auto.json)."""

    def __init__(self, reglages, db_path, data_dir, preparer, nom_fichier, journaliser, intervalle=60):
        self.reglages, self.db_path, self.data_dir = reglages, db_path, data_dir
        self.preparer, self.nom_fichier, self.journaliser = preparer, nom_fichier, journaliser
        self.intervalle = intervalle
        self.derniere = {"date": None, "message": "", "livrees": 0}

    def _etat(self):
        try:
            with open(os.path.join(self.data_dir, "passerelle_auto.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"tentees": {}}

    def _sauver(self, e):
        e["tentees"] = dict(list(e["tentees"].items())[-2000:])
        with open(os.path.join(self.data_dir, "passerelle_auto.json"), "w", encoding="utf-8") as fh:
            json.dump(e, fh, ensure_ascii=False)

    def tour(self):
        """Un passage. Renvoie la liste des (numéro, résultat)."""
        r = self.reglages()
        if not (r.get("url") and r.get("cle") and r.get("auto")):
            return []
        client = Client(r["url"], r["cle"])
        e = self._etat()
        faits = []
        for d in client.demandes():
            cle = f"{d['id']}:{d['livrables']}"
            if d["statut"] != "recu" or d["livrables"] or cle in e["tentees"]:
                continue
            prep = self.preparer(d, client.original(d["id"]), self.db_path)
            if prep["ok"]:
                cr = prep["compte_rendu"]
                client.livrer(d["id"], self.nom_fichier(d), prep["patched"],
                              note=f"{' + '.join(cr['types'])} · checksum {cr['checksum'] or 'OK'}", auteur="PC atelier (auto)")
                self.journaliser(self.db_path, d, prep, "PC atelier (auto)")
                e["tentees"][cle] = "livree"
                faits.append((d["numero"], "livrée"))
            else:
                e["tentees"][cle] = prep["raison"][:200]
                faits.append((d["numero"], prep["raison"]))
        self._sauver(e)
        return faits

    def boucle(self):
        while True:
            try:
                faits = self.tour()
                livrees = sum(1 for _, x in faits if x == "livrée")
                self.derniere = {"date": _now(), "livrees": self.derniere["livrees"] + livrees,
                                 "message": "; ".join(f"{n} : {x}" for n, x in faits)[:500] or "rien à livrer"}
            except Exception as ex:   # réseau coupé, serveur arrêté… : on réessaie au tour suivant
                self.derniere = dict(self.derniere, date=_now(), message=f"erreur : {ex}"[:300])
            time.sleep(self.intervalle)
