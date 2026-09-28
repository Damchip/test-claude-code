"""
Mise à jour du logiciel depuis l'outil atelier (onglet Fileservice → Mise à jour, administrateurs).

Deux sources :
  - GitHub : la dernière release du dépôt (réglable ; jeton d'accès si le dépôt est privé).
    L'archive est vérifiée avec son empreinte SHA-256 publiée à côté d'elle ;
  - un fichier .zip de release envoyé depuis le navigateur.

Installation, dans cet ordre (rien n'est modifié tant qu'une étape échoue) :
  1. contrôle de l'archive : un seul dossier racine, pas de chemin dangereux, fichiers attendus,
     jamais de dossier data/ ;
  2. dépendances : pip install -r requirements.txt si la liste a changé ;
  3. essai : la nouvelle version est démarrée à part, sur des données vides ;
  4. sauvegarde du code actuel (data/mises_a_jour/, 3 gardées) pour revenir en arrière ;
  5. copie des fichiers (data/, .git/ et les environnements Python ne sont jamais touchés) ;
     les fichiers retirés par la nouvelle version sont supprimés ;
  6. redémarrage : automatique chez un hébergeur Passenger (O2switch) ; en local, relancer les .bat.

Si le logiciel a été installé avec Git (dossier .git), la mise à jour GitHub se fait par
« git fetch » puis passage sur l'étiquette de la version.
"""
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

RACINE = os.path.dirname(os.path.abspath(__file__))
DEPOT_DEFAUT = "Damchip/test-claude-code"
PREFIXE = "carto_matcher/"
OBLIGATOIRES = ("app.py", "portal.py", "fileservice.py", "requirements.txt")
PROTEGES = {"data", ".git", "tmp", "venv", ".venv", "env", "__pycache__", "node_modules"}
MANIFESTE = ".fichiers_version"
TAILLE_MAX = 150 * 1024 * 1024
DECOMPRESSE_MAX = 400 * 1024 * 1024
SAUVEGARDES_GARDEES = 3


class ErreurMaj(RuntimeError):
    pass


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- Réglages et état ------------------------------------------------------------

def reglages(cfg, masquer=True):
    r = {"depot": DEPOT_DEFAUT, "jeton": "", "auto": False}
    r.update({k: v for k, v in (cfg.get("mise_a_jour") or {}).items() if k in r})
    if masquer:
        r["jeton_set"] = bool(r.pop("jeton"))
    return r


def _chemin_etat(data_dir):
    return os.path.join(data_dir, "mise_a_jour.json")


def etat(data_dir):
    try:
        with open(_chemin_etat(data_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _noter(data_dir, **champs):
    e = etat(data_dir)
    e.update(champs)
    os.makedirs(data_dir, exist_ok=True)
    with open(_chemin_etat(data_dir), "w", encoding="utf-8") as fh:
        json.dump(e, fh, ensure_ascii=False, indent=2)
    return e


def _journal(data_dir, texte):
    e = etat(data_dir)
    j = (e.get("journal") or [])[-29:] + [{"date": _now(), "texte": texte}]
    _noter(data_dir, journal=j)


# --- Versions --------------------------------------------------------------------

def version_de(source_app_py):
    m = re.search(r'^APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', source_app_py, re.M)
    return m.group(1) if m else None


def version_locale(racine=None):
    racine = racine or RACINE
    try:
        with open(os.path.join(racine, "app.py"), encoding="utf-8") as fh:
            return version_de(fh.read()) or "0.0.0"
    except OSError:
        return "0.0.0"


def plus_recente(a, b):
    """Vrai si la version a (« 1.57.0 » ou « v1.57.0 ») est strictement plus récente que b."""
    def t(v):
        return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])
    return t(a) > t(b)


def mode_git(racine=None):
    racine = racine or RACINE
    return os.path.isdir(os.path.join(racine, ".git")) and shutil.which("git") is not None


# --- GitHub ----------------------------------------------------------------------

def _requete(url, jeton="", accept="application/vnd.github+json", taille_max=TAILLE_MAX):
    h = {"Accept": accept, "User-Agent": "carto-matcher-mise-a-jour", "X-GitHub-Api-Version": "2022-11-28"}
    if jeton:
        h["Authorization"] = "Bearer " + jeton
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
            data = r.read(taille_max + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise ErreurMaj("GitHub refuse l'accès (dépôt privé ? vérifie le jeton d'accès)." if e.code != 404 else
                            "Aucune release trouvée sur ce dépôt (ou dépôt privé sans jeton).")
        raise ErreurMaj(f"GitHub a répondu {e.code}.")
    except (urllib.error.URLError, OSError) as e:
        raise ErreurMaj(f"GitHub injoignable : {e}")
    if len(data) > taille_max:
        raise ErreurMaj("Fichier trop volumineux.")
    return data


def derniere_release(depot, jeton=""):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", depot or ""):
        raise ErreurMaj("Dépôt GitHub invalide (format propriétaire/nom).")
    r = json.loads(_requete(f"https://api.github.com/repos/{depot}/releases/latest", jeton))
    tag = r.get("tag_name") or ""
    assets = {a.get("name"): a for a in r.get("assets") or []}
    zipa = assets.get(f"carto_matcher_{tag}.zip")
    sha = assets.get(f"carto_matcher_{tag}.zip.sha256")
    return {"version": tag.lstrip("v"), "tag": tag, "notes": (r.get("body") or "")[:20000],
            "publiee_le": (r.get("published_at") or "")[:10], "page": r.get("html_url"),
            "zip": zipa and zipa.get("url"), "sha256": sha and sha.get("url")}


def verifier(cfg, data_dir, racine=None):
    """Interroge GitHub et note le résultat. Renvoie {locale, disponible, a_jour, release}."""
    racine = racine or RACINE
    r = reglages(cfg, masquer=False)
    rel = derniere_release(r["depot"], r["jeton"])
    locale = version_locale(racine)
    nouvelle = plus_recente(rel["version"], locale)
    _noter(data_dir, derniere_verification=_now(), derniere_version=rel["version"], notes=rel["notes"],
           page=rel["page"], publiee_le=rel["publiee_le"])
    return {"locale": locale, "disponible": rel["version"], "nouvelle": nouvelle, "release": rel}


def telecharger(cfg, rel):
    """Télécharge l'archive de la release et vérifie son empreinte SHA-256."""
    r = reglages(cfg, masquer=False)
    if not rel.get("zip"):
        raise ErreurMaj("Cette release ne contient pas d'archive carto_matcher_vX.Y.Z.zip.")
    if not rel.get("sha256"):
        raise ErreurMaj("Cette release n'a pas d'empreinte SHA-256 : installe-la par un fichier .zip.")
    contenu = _requete(rel["zip"], r["jeton"], accept="application/octet-stream")
    attendue = _requete(rel["sha256"], r["jeton"], accept="application/octet-stream", taille_max=4096).decode().split()[0].lower()
    if hashlib.sha256(contenu).hexdigest() != attendue:
        raise ErreurMaj("Empreinte SHA-256 incorrecte : archive corrompue ou modifiée, installation annulée.")
    return contenu


# --- Contrôle et installation d'une archive ---------------------------------------

def _protege(chemin_relatif):
    parties = chemin_relatif.replace("\\", "/").split("/")
    return (parties[0] in PROTEGES or any(p in ("__pycache__", "tmp", ".git") for p in parties)
            or chemin_relatif.endswith(".pyc") or parties[-1] == MANIFESTE)


def controler_zip(contenu):
    """Vérifie une archive de release. Renvoie (version, [chemins relatifs des fichiers])."""
    if len(contenu) > TAILLE_MAX:
        raise ErreurMaj("Archive trop volumineuse.")
    try:
        z = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile:
        raise ErreurMaj("Ce fichier n'est pas une archive .zip valide.")
    fichiers, total = [], 0
    with z:
        for info in z.infolist():
            nom = info.filename
            if "\\" in nom or nom.startswith("/") or ".." in nom.split("/") or ":" in nom:
                raise ErreurMaj(f"Chemin refusé dans l'archive : {nom}")
            if not nom.startswith(PREFIXE):
                raise ErreurMaj("Archive inattendue : tout doit être dans un dossier carto_matcher/ (zip de release).")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ErreurMaj(f"Lien symbolique refusé : {nom}")
            rel = nom[len(PREFIXE):]
            if not rel or nom.endswith("/"):
                continue
            if rel.split("/", 1)[0] == "data":
                raise ErreurMaj("L'archive contient un dossier data/ : refusée (tes données ne doivent jamais être écrasées).")
            total += info.file_size
            if total > DECOMPRESSE_MAX:
                raise ErreurMaj("Archive trop volumineuse une fois décompressée.")
            fichiers.append(rel)
        manquants = [f for f in OBLIGATOIRES if f not in fichiers]
        if manquants:
            raise ErreurMaj("Archive incomplète : " + ", ".join(manquants) + " manquant(s).")
        version = version_de(z.read(PREFIXE + "app.py").decode("utf-8", "replace"))
    if not version:
        raise ErreurMaj("Version introuvable dans l'archive.")
    return version, fichiers


def _extraire(contenu, dest):
    with zipfile.ZipFile(io.BytesIO(contenu)) as z:
        for info in z.infolist():
            rel = info.filename[len(PREFIXE):]
            if not rel or info.filename.endswith("/"):
                continue
            cible = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(cible), exist_ok=True)
            with z.open(info) as src, open(cible, "wb") as out:
                shutil.copyfileobj(src, out)


def _empreinte_fichier(p):
    try:
        with open(p, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def environnements_python(racine):
    """Interpréteurs à mettre à jour : celui qui tourne, plus ceux de cPanel « Setup Python App »
    (~/virtualenv/<racine de l'application>/<version>/bin/python) des deux applications (portail et atelier)."""
    import glob
    vus = [sys.executable]
    maison = os.path.expanduser("~")
    try:
        rel = os.path.relpath(racine, maison)
    except ValueError:
        return vus
    if not rel.startswith(".."):
        for app_rel in (rel, os.path.join(rel, "deploy", "atelier")):
            for py in sorted(glob.glob(os.path.join(maison, "virtualenv", app_rel, "*", "bin", "python"))):
                if os.path.realpath(py) not in {os.path.realpath(v) for v in vus}:
                    vus.append(py)
    return vus


def _installer_dependances(staging, racine, journal):
    """pip install -r requirements.txt si la liste change, dans chaque environnement Python concerné."""
    if _empreinte_fichier(os.path.join(staging, "requirements.txt")) == _empreinte_fichier(os.path.join(racine, "requirements.txt")):
        return
    for py in environnements_python(racine):
        journal(f"Installation des dépendances (requirements.txt a changé) : {py}")
        r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                            os.path.join(staging, "requirements.txt")], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ErreurMaj("Installation des dépendances impossible : " + (r.stderr or r.stdout)[-600:])


def _essayer(staging):
    """Démarre la nouvelle version à part (données vides) : erreur de syntaxe ou d'import = installation annulée."""
    with tempfile.TemporaryDirectory() as vide:
        env = dict(os.environ, CARTO_DB=os.path.join(vide, "solutions.db"), CARTO_PROD="0", PYTHONDONTWRITEBYTECODE="1")
        for k in ("CARTO_FS_DB", "CARTO_FS_FILES"):
            env.pop(k, None)
        r = subprocess.run([sys.executable, "-c", "import app, portal"], cwd=staging, env=env,
                           capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise ErreurMaj("La nouvelle version ne démarre pas, rien n'a été modifié : " + (r.stderr or r.stdout)[-800:])


def _fichiers_installes(racine):
    try:
        with open(os.path.join(racine, MANIFESTE), encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip()]
    except OSError:
        return None


def sauvegarder_code(racine, data_dir, version):
    """Archive le code actuel (sans data/) au format release, pour revenir en arrière."""
    dossier = os.path.join(data_dir, "mises_a_jour")
    os.makedirs(dossier, exist_ok=True)
    dest = os.path.join(dossier, f"code-v{version}-{dt.datetime.now():%Y%m%d-%H%M%S}.zip")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dossiers, noms in os.walk(racine):
            rel_base = os.path.relpath(base, racine)
            dossiers[:] = [d for d in dossiers if not _protege(os.path.join(rel_base, d) if rel_base != "." else d)]
            for n in noms:
                rel = os.path.normpath(os.path.join(rel_base, n)) if rel_base != "." else n
                if not _protege(rel):
                    z.write(os.path.join(base, n), PREFIXE + rel.replace(os.sep, "/"))
    anciennes = sorted(f for f in os.listdir(dossier) if f.startswith("code-") and f.endswith(".zip"))
    for f in anciennes[:-SAUVEGARDES_GARDEES]:
        os.remove(os.path.join(dossier, f))
    return dest


def redemarrer(racine=None):
    """Passenger (O2switch) redémarre une application quand tmp/restart.txt change : les deux sont concernées."""
    racine = racine or RACINE
    touches = []
    for app_racine in (racine, os.path.join(racine, "deploy", "atelier")):
        d = os.path.join(app_racine, "tmp")
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "restart.txt"), "a"):
                os.utime(os.path.join(d, "restart.txt"))
            touches.append(d)
        except OSError:
            pass
    return touches


def installer_zip(contenu, data_dir, racine=None, essai=True):
    """Installe une archive de release. Renvoie {version, precedente, sauvegarde, journal}."""
    racine = racine or RACINE
    lignes = []
    journal = lignes.append
    version, fichiers = controler_zip(contenu)
    precedente = version_locale(racine)
    journal(f"Archive valide : version {version} ({len(fichiers)} fichiers).")
    parent = os.path.join(data_dir, "mises_a_jour")
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="staging-", dir=parent)
    try:
        _extraire(contenu, staging)
        _installer_dependances(staging, racine, journal)
        if essai:
            _essayer(staging)
            journal("Essai de démarrage réussi.")
        sauvegarde = sauvegarder_code(racine, data_dir, precedente)
        journal(f"Code actuel sauvegardé : {os.path.basename(sauvegarde)}.")
        for rel in fichiers:
            if _protege(rel):
                continue
            cible = os.path.join(racine, *rel.split("/"))
            os.makedirs(os.path.dirname(cible), exist_ok=True)
            shutil.copy2(os.path.join(staging, *rel.split("/")), cible)
        anciens = _fichiers_installes(racine)
        retires = 0
        if anciens:
            for rel in set(anciens) - set(fichiers):
                p = os.path.join(racine, *rel.split("/"))
                if not _protege(rel) and os.path.isfile(p) and os.path.realpath(p).startswith(os.path.realpath(racine) + os.sep):
                    os.remove(p)
                    retires += 1
        with open(os.path.join(racine, MANIFESTE), "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(fichiers)) + "\n")
        journal(f"{len(fichiers)} fichiers installés" + (f", {retires} retiré(s)." if retires else "."))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    redemarrer(racine)
    _noter(data_dir, installee_le=_now(), version_installee=version, precedente=precedente, sauvegarde=sauvegarde)
    _journal(data_dir, f"Version {version} installée (depuis {precedente}).")
    return {"version": version, "precedente": precedente, "sauvegarde": os.path.basename(sauvegarde), "journal": lignes}


def installer_git(tag, data_dir, racine=None):
    """Dépôt Git : récupère les étiquettes et passe sur celle de la version (fichiers non suivis, dont data/, intacts)."""
    racine = racine or RACINE
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag or ""):
        raise ErreurMaj("Version invalide.")

    def git(*args):
        r = subprocess.run(["git", "-C", racine, *args], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise ErreurMaj("git " + args[0] + " : " + (r.stderr or r.stdout)[-600:])
        return r.stdout.strip()

    avant = git("rev-parse", "HEAD")
    precedente = version_locale(racine)
    git("fetch", "--tags", "--force", "origin")
    git("-c", "advice.detachedHead=false", "checkout", "--force", tag)
    redemarrer(racine)
    _noter(data_dir, installee_le=_now(), version_installee=tag.lstrip("v"), precedente=precedente, commit_precedent=avant,
           sauvegarde=None)
    _journal(data_dir, f"Version {tag} installée par Git (depuis {precedente}).")
    return {"version": tag.lstrip("v"), "precedente": precedente, "journal": [f"Passage sur {tag} (Git)."]}


def revenir(data_dir, racine=None):
    """Revient à la version d'avant la dernière mise à jour."""
    racine = racine or RACINE
    e = etat(data_dir)
    if e.get("commit_precedent") and mode_git(racine):
        r = subprocess.run(["git", "-C", racine, "-c", "advice.detachedHead=false", "checkout", "--force",
                            e["commit_precedent"]], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise ErreurMaj("Retour arrière impossible : " + r.stderr[-400:])
        redemarrer(racine)
        _noter(data_dir, commit_precedent=None)
        _journal(data_dir, f"Retour à la version {e.get('precedente')} (Git).")
        return {"version": e.get("precedente")}
    nom = e.get("sauvegarde")
    p = os.path.join(data_dir, "mises_a_jour", os.path.basename(nom or ""))
    if not nom or not os.path.isfile(p):
        raise ErreurMaj("Aucune sauvegarde de la version précédente.")
    with open(p, "rb") as fh:
        res = installer_zip(fh.read(), data_dir, racine, essai=False)
    _journal(data_dir, f"Retour à la version {res['version']}.")
    return res


def automatique(cfg, data_dir, racine=None, heure=None, envoyer=None):
    """Appelé par les tâches planifiées : vérifie une fois par jour ; installe la nuit (2 h – 5 h) si l'option est
    active. `envoyer(sujet, texte)` prévient l'atelier. Ne lève jamais."""
    racine = racine or RACINE
    r = reglages(cfg, masquer=False)
    e = etat(data_dir)
    heure = dt.datetime.now().hour if heure is None else heure
    try:
        derniere = dt.datetime.strptime(e.get("derniere_verification", "2000-01-01 00:00:00"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        derniere = dt.datetime(2000, 1, 1)
    if dt.datetime.now() - derniere < dt.timedelta(hours=20) and not (r["auto"] and 2 <= heure < 5):
        return None
    try:
        v = verifier(cfg, data_dir, racine)
        if not v["nouvelle"]:
            return None
        if not (r["auto"] and 2 <= heure < 5):
            if e.get("signalee") != v["disponible"] and envoyer:
                envoyer(f"Nouvelle version {v['disponible']} disponible",
                        f"La version {v['disponible']} du logiciel est disponible (installée : {v['locale']}).\n"
                        "Installe-la depuis l'outil atelier : Fileservice → Mise à jour du logiciel.\n\n" + v["release"]["notes"][:3000])
                _noter(data_dir, signalee=v["disponible"])
            return "disponible"
        if mode_git(racine):
            res = installer_git(v["release"]["tag"], data_dir, racine)
        else:
            res = installer_zip(telecharger(cfg, v["release"]), data_dir, racine)
        if envoyer:
            envoyer(f"Version {res['version']} installée automatiquement",
                    f"Le logiciel est passé de {res['precedente']} à {res['version']} cette nuit.\n"
                    "En cas de souci : Fileservice → Mise à jour du logiciel → Revenir à la version précédente.")
        return "installee"
    except Exception as ex:
        _journal(data_dir, f"Mise à jour automatique impossible : {ex}")
        if envoyer:
            envoyer("Mise à jour automatique impossible", str(ex)[:2000])
        return "erreur"
