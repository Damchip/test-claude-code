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

v1.59 — consultation depuis l'outil en ligne :
  - le PC envoie sa base de solutions (.db, SANS les fichiers) à chaque changement : la liste,
    la recherche et les fiches sont visibles sur l'outil en ligne ;
  - un technicien peut demander le fichier d'une fiche : le PC (s'il est allumé) envoie ce seul
    fichier, gardé sur le serveur le temps du téléchargement (10 minutes au plus) puis effacé.

Côté serveur : table `passerelle_cles` (empreinte SHA-256 seulement, jamais la clé en clair).
Côté PC : `Client` (urllib, aucune dépendance), réglages dans data/config.json du PC.
"""
import datetime as dt
import gzip
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
import zlib
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
CREATE TABLE IF NOT EXISTS passerelle_fichiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_id INTEGER NOT NULL,
    quoi TEXT NOT NULL DEFAULT 'solution',
    libelle TEXT NOT NULL DEFAULT '',
    demandeur_id INTEGER,
    demandeur TEXT NOT NULL DEFAULT '',
    statut TEXT NOT NULL DEFAULT 'attente',
    message TEXT NOT NULL DEFAULT '',
    nom TEXT NOT NULL DEFAULT '',
    fichier TEXT NOT NULL DEFAULT '',
    taille INTEGER NOT NULL DEFAULT 0,
    poste TEXT NOT NULL DEFAULT '',
    cree_le REAL NOT NULL,
    pret_le REAL
);
"""
QUOI = ("solution", "original")
DUREE_FICHIER = 600          # secondes : un fichier envoyé par le PC ne reste pas plus longtemps sur le serveur
PC_EN_LIGNE = 90             # secondes depuis le dernier contact pour considérer le PC connecté
BASE_MAX = 512 * 1024 * 1024  # taille maximale de la base décompressée


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


def pc_connecte(db_path, delai=PC_EN_LIGNE):
    """Un PC atelier a-t-il contacté le serveur récemment ? (il interroge toutes les quelques secondes)"""
    limite = (dt.datetime.now() - dt.timedelta(seconds=delai)).strftime("%Y-%m-%d %H:%M:%S")
    with connect(db_path) as con:
        return con.execute("SELECT COUNT(*) n FROM passerelle_cles WHERE revoquee = 0 AND derniere_utilisation >= ?",
                           (limite,)).fetchone()["n"] > 0


# --- Serveur : fichiers demandés au PC ------------------------------------------------

def _dossier_tmp(data_dir):
    d = os.path.join(data_dir, "passerelle_tmp")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def purger_fichiers(db_path, data_dir, maintenant=None):
    """Efface les fichiers et demandes échus (10 minutes). Appelé à chaque échange : rien ne s'accumule."""
    now = maintenant or time.time()
    with connect(db_path) as con:
        vieux = con.execute("SELECT id, fichier FROM passerelle_fichiers WHERE cree_le < ? AND statut IN ('attente', 'pret')",
                            (now - DUREE_FICHIER,)).fetchall()
        for r in vieux:
            _effacer(data_dir, r["fichier"])
            con.execute("UPDATE passerelle_fichiers SET statut = 'expire', fichier = '' WHERE id = ?", (r["id"],))
        con.execute("DELETE FROM passerelle_fichiers WHERE cree_le < ?", (now - 30 * 86400,))
    d = os.path.join(data_dir, "passerelle_tmp")
    if os.path.isdir(d):   # orphelins (arrêt brutal pendant un envoi…)
        for f in os.listdir(d):
            p = os.path.join(d, f)
            try:
                if os.path.getmtime(p) < now - DUREE_FICHIER:
                    os.remove(p)
            except OSError:
                pass


def _effacer(data_dir, fichier):
    if fichier:
        try:
            os.remove(os.path.join(_dossier_tmp(data_dir), os.path.basename(fichier)))
        except OSError:
            pass


def demander_fichier(db_path, solution_id, quoi="solution", libelle="", demandeur_id=None, demandeur=""):
    if quoi not in QUOI:
        raise ErreurCompte("Type de fichier inconnu.")
    with connect(db_path) as con:
        en_cours = con.execute("SELECT COUNT(*) n FROM passerelle_fichiers WHERE statut = 'attente' AND cree_le >= ?",
                               (time.time() - DUREE_FICHIER,)).fetchone()["n"]
        if en_cours >= 20:
            raise ErreurCompte("Trop de fichiers déjà demandés au PC : patiente une minute.")
        cur = con.execute("INSERT INTO passerelle_fichiers (solution_id, quoi, libelle, demandeur_id, demandeur, cree_le)"
                          " VALUES (?, ?, ?, ?, ?, ?)",
                          (int(solution_id), quoi, (libelle or "")[:120], demandeur_id, (demandeur or "")[:60], time.time()))
        return cur.lastrowid


def fichier_demande(db_path, rid):
    with connect(db_path) as con:
        r = con.execute("SELECT * FROM passerelle_fichiers WHERE id = ?", (rid,)).fetchone()
    return dict(r) if r else None


def fichiers_en_attente(db_path):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT id, solution_id, quoi FROM passerelle_fichiers WHERE statut = 'attente' AND cree_le >= ? ORDER BY id",
            (time.time() - DUREE_FICHIER,))]


def deposer_fichier(db_path, data_dir, rid, nom, contenu, poste=""):
    """Le PC envoie le fichier demandé : gardé sous un nom aléatoire dans data/passerelle_tmp/."""
    d = fichier_demande(db_path, rid)
    if not d or d["statut"] != "attente":
        raise ErreurCompte("Demande de fichier inconnue ou déjà traitée.")
    interne = f"{rid}_{secrets.token_hex(12)}.bin"
    with open(os.path.join(_dossier_tmp(data_dir), interne), "wb") as fh:
        fh.write(contenu)
    nom = os.path.basename((nom or "fichier.bin").replace("\\", "/"))[:150] or "fichier.bin"
    with connect(db_path) as con:
        con.execute("UPDATE passerelle_fichiers SET statut = 'pret', nom = ?, fichier = ?, taille = ?, poste = ?, pret_le = ?"
                    " WHERE id = ?", (nom, interne, len(contenu), (poste or "")[:60], time.time(), rid))
    return d


def refuser_fichier(db_path, rid, message, poste=""):
    with connect(db_path) as con:
        con.execute("UPDATE passerelle_fichiers SET statut = 'erreur', message = ?, poste = ? WHERE id = ? AND statut = 'attente'",
                    ((message or "Fichier introuvable sur le PC.")[:300], (poste or "")[:60], rid))


def retirer_fichier(db_path, data_dir, rid):
    """Téléchargement par le technicien : renvoie (nom, contenu) et efface aussitôt le fichier du serveur."""
    d = fichier_demande(db_path, rid)
    if not d or d["statut"] != "pret" or not d["fichier"]:
        return None
    try:
        with open(os.path.join(_dossier_tmp(data_dir), os.path.basename(d["fichier"])), "rb") as fh:
            contenu = fh.read()
    except OSError:
        return None
    _effacer(data_dir, d["fichier"])
    with connect(db_path) as con:
        con.execute("UPDATE passerelle_fichiers SET statut = 'recupere', fichier = '' WHERE id = ?", (rid,))
    return d["nom"], contenu


# --- Serveur : base de solutions envoyée par le PC ------------------------------------

def _etat_base_chemin(data_dir):
    return os.path.join(data_dir, "passerelle_base.json")


def etat_base(data_dir):
    try:
        with open(_etat_base_chemin(data_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def recevoir_base(flux, dest_tmp):
    """Décompresse (gzip) la base envoyée par le PC dans `dest_tmp`, taille bornée. Renvoie l'empreinte SHA-256."""
    h = hashlib.sha256()
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    with open(dest_tmp, "wb") as out:
        while True:
            bloc = flux.read(1024 * 1024)
            if not bloc:
                break
            while bloc:
                morceau = dec.decompress(bloc, 4 * 1024 * 1024)
                bloc = dec.unconsumed_tail
                total += len(morceau)
                if total > BASE_MAX:
                    raise ErreurCompte("Base trop volumineuse.")
                h.update(morceau)
                out.write(morceau)
        reste = dec.flush()
        total += len(reste)
        if total > BASE_MAX:
            raise ErreurCompte("Base trop volumineuse.")
        h.update(reste)
        out.write(reste)
    if not dec.eof:
        raise ErreurCompte("Base incomplète (envoi interrompu ?).")
    return h.hexdigest()


def noter_base(data_dir, empreinte, fiches, poste):
    with open(_etat_base_chemin(data_dir), "w", encoding="utf-8") as fh:
        json.dump({"empreinte": empreinte, "fiches": fiches, "poste": poste, "date": _now()}, fh)


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

    def _multipart(self, chemin, nom, contenu, champs=()):
        limite = f"----carto{uuid.uuid4().hex}"
        parties = []
        for k, v in champs:
            parties.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        nom_sur = (nom or "fichier.bin").replace('"', "_").replace("\r", "").replace("\n", "")
        parties.append(f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="{nom_sur}"\r\n'
                       "Content-Type: application/octet-stream\r\n\r\n".encode() + contenu + b"\r\n")
        corps = b"".join(parties) + f"--{limite}--\r\n".encode()
        return self._req("POST", chemin, corps, {"Content-Type": f"multipart/form-data; boundary={limite}"})

    def livrer(self, did, nom, contenu, note="", auteur=""):
        return self._multipart(f"/demandes/{int(did)}/livrer", nom, contenu, (("note", note), ("auteur", auteur)))

    def fichiers(self):
        return self._req("GET", "/fichiers")["fichiers"]

    def envoyer_fichier(self, rid, nom, contenu):
        return self._multipart(f"/fichiers/{int(rid)}", nom, contenu)

    def fichier_introuvable(self, rid, message):
        return self._req("POST", f"/fichiers/{int(rid)}/echec", json.dumps({"message": message}).encode(),
                         {"Content-Type": "application/json"})

    def envoyer_base(self, chemin_gz, empreinte):
        with open(chemin_gz, "rb") as fh:
            corps = fh.read()
        return self._req("POST", "/base", corps, {"Content-Type": "application/gzip", "X-Empreinte": empreinte})

    def en_traitement(self, did):
        return self._req("POST", f"/demandes/{int(did)}/statut", json.dumps({"statut": "en_cours"}).encode(),
                         {"Content-Type": "application/json"})


# --- PC : livraison automatique ---------------------------------------------------

def instantane_base(db_path, dest):
    """Copie cohérente de la base SQLite (même ouverte, journal WAL compris). Renvoie son empreinte SHA-256."""
    src = sqlite3.connect(db_path, timeout=10)
    try:
        cible = sqlite3.connect(dest)
        with cible:
            src.backup(cible)
        cible.close()
    finally:
        src.close()
    h = hashlib.sha256()
    with open(dest, "rb") as fh:
        for bloc in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


def _signature(db_path):
    sig = []
    for suffixe in ("", "-wal"):
        try:
            st = os.stat(db_path + suffixe)
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


class Automate:
    """Sur le PC, tant que la passerelle est configurée :
      - toutes les `tic` secondes : envoie les fichiers demandés depuis l'outil en ligne (si autorisé) ;
      - toutes les `intervalle` secondes : envoie la base de solutions si elle a changé (sans les fichiers),
        puis prépare et livre les nouvelles demandes « propres » (si la livraison automatique est cochée).
    Chaque demande n'est tentée qu'une fois (état dans data/passerelle_auto.json)."""

    def __init__(self, reglages, db_path, data_dir, preparer, nom_fichier, journaliser, intervalle=60,
                 chemin_fichier=None, tic=5):
        self.reglages, self.db_path, self.data_dir = reglages, db_path, data_dir
        self.preparer, self.nom_fichier, self.journaliser = preparer, nom_fichier, journaliser
        self.chemin_fichier = chemin_fichier
        self.intervalle, self.tic = intervalle, tic
        self.derniere = {"date": None, "message": "", "livrees": 0}
        self.base = {"date": None, "message": ""}
        self.fichiers = {"date": None, "message": "", "envoyes": 0}
        self._sig_base = None
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

    def servir_fichiers(self, client=None):
        """Envoie les fichiers de fiches demandés depuis l'outil en ligne. Renvoie la liste des (id, résultat)."""
        r = self.reglages()
        if not (r.get("url") and r.get("cle") and r.get("fichiers", True) and self.chemin_fichier):
            return []
        client = client or Client(r["url"], r["cle"])
        faits = []
        for f in client.fichiers():
            chemin = None
            try:
                chemin = self.chemin_fichier(self.db_path, f["solution_id"], f["quoi"])
            except Exception:
                pass
            if not chemin or not os.path.isfile(chemin):
                client.fichier_introuvable(f["id"], f"Fichier de la fiche {f['solution_id']} introuvable sur le PC"
                                                    " (OneDrive pas synchronisé ? chemin changé ?).")
                faits.append((f["id"], "introuvable"))
                continue
            with open(chemin, "rb") as fh:
                contenu = fh.read()
            client.envoyer_fichier(f["id"], os.path.basename(chemin), contenu)
            faits.append((f["id"], "envoyé"))
        return faits

    def synchroniser_base(self, client=None, forcer=False):
        """Envoie la base de solutions (sans les fichiers) si elle a changé. Renvoie le nombre de fiches envoyées, ou None."""
        r = self.reglages()
        if not (r.get("url") and r.get("cle") and r.get("base", True)) or not os.path.isfile(self.db_path):
            return None
        sig = _signature(self.db_path)
        if sig == self._sig_base and not forcer:
            return None
        client = client or Client(r["url"], r["cle"])
        tmp = tempfile.mkdtemp(prefix="carto-base-")
        try:
            copie = os.path.join(tmp, "solutions.db")
            empreinte = instantane_base(self.db_path, copie)
            if client.etat().get("base_empreinte") == empreinte:
                self._sig_base = sig
                return None
            with sqlite3.connect(copie) as con:
                fiches = con.execute("SELECT COUNT(*) FROM solutions").fetchone()[0]
            if not fiches:
                self._sig_base = sig   # une base vide n'écrase jamais celle du serveur
                return None
            gz = copie + ".gz"
            with open(copie, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            client.envoyer_base(gz, empreinte)
            self._sig_base = sig
            return fiches
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def boucle(self):
        prochain = 0
        while True:
            r = self.reglages()
            if r.get("url") and r.get("cle"):
                try:
                    faits = self.servir_fichiers()
                    if faits:
                        n = sum(1 for _, x in faits if x == "envoyé")
                        self.fichiers = {"date": _now(), "envoyes": self.fichiers["envoyes"] + n,
                                         "message": f"{n} envoyé(s)" + (f", {len(faits) - n} introuvable(s)" if len(faits) > n else "")}
                except Exception as ex:
                    self.fichiers = dict(self.fichiers, date=_now(), message=f"erreur : {ex}"[:300])
                if time.time() >= prochain:
                    prochain = time.time() + self.intervalle
                    try:
                        n = self.synchroniser_base()
                        if n is not None:
                            self.base = {"date": _now(), "message": f"{n} fiche(s) envoyée(s)"}
                        elif r.get("base", True) and (not self.base["date"] or self.base["message"].startswith("erreur")):
                            self.base = {"date": _now(), "message": "à jour"}
                    except Exception as ex:
                        self.base = {"date": _now(), "message": f"erreur : {ex}"[:300]}
                    try:
                        faits = self.tour()
                        livrees = sum(1 for _, x in faits if x == "livrée")
                        self.derniere = {"date": _now(), "livrees": self.derniere["livrees"] + livrees,
                                         "message": "; ".join(f"{n} : {x}" for n, x in faits)[:500] or "rien à livrer"}
                    except Exception as ex:   # réseau coupé, serveur arrêté… : on réessaie au tour suivant
                        self.derniere = dict(self.derniere, date=_now(), message=f"erreur : {ex}"[:300])
            time.sleep(self.tic)
