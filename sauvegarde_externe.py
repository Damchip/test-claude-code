"""
Sauvegarde externe du fileservice : une copie hors du serveur, chaque nuit.

Réglages atelier (clé "sauvegarde_externe" de portal_config.json, fichier hors git) :

    mode      : "" (désactivée) | "ftp" | "email"
    ftp       : {host, port, user, password, dossier, tls}  — FTPS explicite par défaut
                (un espace de stockage, un NAS, un second hébergement…)
    email     : adresse qui reçoit l'archive (limitée à MAX_EMAIL octets : la base seule)
    fichiers  : true = ajoute aussi les fichiers clients à l'archive (FTP uniquement : volumineux)
    garder    : nombre d'archives gardées sur le FTP (les plus anciennes sont supprimées)

Le résultat de la dernière exécution est noté dans data/sauvegarde_externe.json
(affiché dans l'outil atelier et contrôlé par la page /sante).
"""
import datetime as dt
import ftplib
import io
import json
import os
import ssl
import zipfile

import comptes
import mailer

MAX_EMAIL = 20 * 1024 * 1024
ETAT = "sauvegarde_externe.json"
DEFAUT = {"mode": "", "ftp": {"host": "", "port": 21, "user": "", "dossier": "/sauvegardes-fileservice", "tls": True},
          "email": "", "fichiers": False, "garder": 14}


def reglages(cfg, masquer=True):
    r = json.loads(json.dumps(DEFAUT))
    brut = cfg.get("sauvegarde_externe") or {}
    r.update({k: v for k, v in brut.items() if k in DEFAUT and k != "ftp"})
    r["ftp"].update(brut.get("ftp") or {})
    if masquer:
        r["ftp"]["password_set"] = bool(r["ftp"].pop("password", ""))
    return r


def archive(db_path, files_dir, avec_fichiers=False):
    """(nom, octets) : zip contenant une copie cohérente de la base (+ fichiers clients si demandé)."""
    copie = comptes.sauvegarder(db_path)
    if not copie:
        raise RuntimeError("Base du fileservice introuvable.")
    horodatage = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(copie, "fileservice.db")
        if avec_fichiers and files_dir and os.path.isdir(files_dir):
            for racine, _, noms in os.walk(files_dir):
                for n in noms:
                    p = os.path.join(racine, n)
                    z.write(p, os.path.join("fileservice_fichiers", os.path.relpath(p, files_dir)))
    return f"fileservice-{horodatage}.zip", buf.getvalue()


def _ftp(conf):
    port = int(conf.get("port") or 21)
    if conf.get("tls", True):
        f = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=60)
        f.connect(conf["host"], port)
        f.login(conf.get("user", ""), conf.get("password", ""))
        f.prot_p()   # données chiffrées aussi
    else:
        f = ftplib.FTP(timeout=60)
        f.connect(conf["host"], port)
        f.login(conf.get("user", ""), conf.get("password", ""))
    return f


def _aller_dossier(f, dossier):
    for part in [p for p in (dossier or "").split("/") if p]:
        try:
            f.cwd(part)
        except ftplib.error_perm:
            f.mkd(part)
            f.cwd(part)


def envoyer_ftp(conf, nom, contenu, garder=14):
    if not conf.get("host"):
        raise RuntimeError("Serveur FTP non renseigné.")
    f = _ftp(conf)
    try:
        if (conf.get("dossier") or "").startswith("/"):
            f.cwd("/")
        _aller_dossier(f, conf.get("dossier"))
        f.storbinary(f"STOR {nom}", io.BytesIO(contenu))
        anciennes = sorted(n for n in f.nlst() if n.startswith("fileservice-") and n.endswith(".zip"))
        for n in anciennes[:-max(1, int(garder or 14))]:
            try:
                f.delete(n)
            except ftplib.all_errors:
                pass
    finally:
        try:
            f.quit()
        except ftplib.all_errors:
            f.close()


def executer(cfg, db_path, files_dir, data_dir, nom_atelier="E85-FRANCE"):
    """Fait la sauvegarde externe selon les réglages. Renvoie l'état noté ({ok, date, message, taille})."""
    r = reglages(cfg, masquer=False)
    etat = {"date": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", "mode": r["mode"], "ok": False, "message": "", "taille": 0}
    try:
        if r["mode"] == "ftp":
            nom, contenu = archive(db_path, files_dir, bool(r["fichiers"]))
            envoyer_ftp(r["ftp"], nom, contenu, r["garder"])
            etat.update(ok=True, taille=len(contenu), message=f"{nom} envoyé sur {r['ftp']['host']}")
        elif r["mode"] == "email":
            if not r["email"]:
                raise RuntimeError("Adresse de réception non renseignée.")
            nom, contenu = archive(db_path, files_dir, False)
            if len(contenu) > MAX_EMAIL:
                raise RuntimeError("Archive trop lourde pour un e-mail : passe en mode FTP.")
            ok, err = mailer.envoyer(cfg.get("smtp") or {}, r["email"], f"Sauvegarde fileservice {nom_atelier} · {etat['date'][:10]}",
                                     "Sauvegarde automatique de la base du fileservice (comptes, demandes, factures).\n"
                                     "À conserver en lieu sûr. Pour restaurer : dézipper et remplacer data/fileservice.db.",
                                     nom_expediteur=nom_atelier, journal_dir=data_dir,
                                     pieces=[(nom, contenu, "application/zip")])
            if not ok:
                raise RuntimeError(err)
            etat.update(ok=True, taille=len(contenu), message=f"{nom} envoyé à {r['email']}")
        else:
            etat["message"] = "Sauvegarde externe désactivée."
            return etat
    except Exception as e:   # noté et remonté à l'atelier, jamais propagé
        etat["message"] = f"{e.__class__.__name__}: {e}"
    try:
        with open(os.path.join(data_dir, ETAT), "w", encoding="utf-8") as fh:
            json.dump(etat, fh, ensure_ascii=False)
    except OSError:
        pass
    return etat


def derniere(data_dir):
    try:
        with open(os.path.join(data_dir, ETAT), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
