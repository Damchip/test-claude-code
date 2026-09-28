"""
Espace client du fileservice, monté par portal.py à la racine du portail.

  comptes.py   : comptes pro, connexion, crédits
  demandes.py  : fichiers envoyés, suivi, messages, livraisons
  factures.py  : factures des packs de crédits
  stripe_api.py: paiement en ligne des packs (Stripe Checkout)

Réglages atelier (horaires, identité légale, Stripe, SMTP) : data/portal_config.json,
modifiables depuis l'outil interne.
"""
import datetime as dt
import hmac
import json
import secrets
from zoneinfo import ZoneInfo

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

import catalogue
import comptes
import demandes
import factures
import livraison_auto
import mailer
import pages_legales
import push
import relances
import sms
import stripe_api
import traductions

bp = Blueprint("fs", __name__)

TZ = ZoneInfo("Europe/Paris")
DAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
# Horaires par défaut (jour 0 = lundi) ; remplacés par « horaires » de portal_config.json
HOURS_DEFAUT = {0: (8, 19), 1: (8, 19), 2: (8, 19), 3: (8, 19), 4: (8, 19), 5: (9, 13)}
TOOLS = ["KESS3", "Autotuner", "Flex", "CMD Flash", "MagicMotorsport", "KTAG", "PCMFlash", "Autre"]
PUBLIC_ENDPOINTS = {"fs.login", "fs.register", "fs.forgot", "fs.reset", "fs.stripe_webhook", "fs.legal", "fs.set_langue",
                    "fs.manifest", "fs.service_worker", "fs.hors_ligne", "fs.push_renouveler"}
SANS_CSRF = {"fs.stripe_webhook", "fs.push_renouveler"}   # appelés par Stripe / le service worker, sans session
LIMITEUR = comptes.Limiteur(max_echecs=5, fenetre=900)


# --- Réglages & utilitaires --------------------------------------------------

def _db():
    return current_app.config["FS_DB"]


def _files():
    return current_app.config["FS_FILES"]


def reglages():
    return mailer.lire_config(current_app.config["FS_CONFIG"])


def shop():
    cfg = reglages()
    soc = cfg.get("societe") or {}
    return {"name": cfg.get("shop_name") or "E85-FRANCE", "tel": soc.get("tel", ""),
            "email": soc.get("email", "") or cfg.get("contact", "")}


def _hours():
    raw = reglages().get("horaires")
    if not isinstance(raw, dict):
        return HOURS_DEFAUT
    out = {}
    for k, v in raw.items():
        try:
            if v:
                out[int(k)] = (int(v[0]), int(v[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def service_status(now=None):
    """Ouvert / fermé selon les horaires, et prochaine ouverture si fermé."""
    now = now or dt.datetime.now(TZ)
    hours = _hours()
    today = hours.get(now.weekday())
    if today and today[0] <= now.hour < today[1]:
        return {"open": True, "next_open": ""}
    for offset in range(0, 8):
        day = now + dt.timedelta(days=offset)
        hrs = hours.get(day.weekday())
        if not hrs or (offset == 0 and now.hour >= hrs[0]):
            continue
        when = "aujourd'hui" if offset == 0 else ("demain" if offset == 1 else DAY_NAMES[day.weekday()].lower())
        return {"open": False, "next_open": f"réouverture {when} à {hrs[0]} h"}
    return {"open": False, "next_open": ""}


def hours_table(now=None):
    now = now or dt.datetime.now(TZ)
    hours = _hours()
    return [{"jour": DAY_NAMES[d], "today": d == now.weekday(),
             "plage": f"{hours[d][0]} h – {hours[d][1]} h" if d in hours else "Fermé"} for d in range(7)]


def _smtp():
    return reglages().get("smtp") or {}


def _mail_client_lang(cle_sujet, cle_texte, **fmt):
    """(sujet, texte) traduits dans la langue préférée du client connecté, pour ses e-mails."""
    lg = langue()
    return traductions.traduire(cle_sujet, lg).format(**fmt), traductions.traduire(cle_texte, lg).format(**fmt)


def _mail(a, sujet, texte):
    ok, err = mailer.envoyer(_smtp(), a, sujet, texte, nom_expediteur=shop()["name"],
                             journal_dir=current_app.config["FS_DATA_DIR"])
    if not ok:
        current_app.logger.warning("E-mail non envoyé à %s : %s", a, err)
    return ok


def _mail_atelier(sujet, texte):
    atelier = _smtp().get("atelier")
    if atelier:
        _mail(atelier, sujet, texte)


def _url_publique(endpoint, **kw):
    """Lien absolu pour les e-mails. public_url (portal_config.json) prime sur l'hôte de la requête."""
    base = (current_app.config.get("FS_PUBLIC_URL") or reglages().get("public_url") or "").rstrip("/")
    if base:
        return base + url_for(endpoint, **kw)
    return url_for(endpoint, _external=True, **kw)


def _ip():
    return current_app.config["FS_CLIENT_IP"]()


def csrf_token():
    tok = session.get("csrf")
    if not tok:
        tok = session["csrf"] = secrets.token_urlsafe(24)
    return tok


def _initiales(nom):
    parts = [p for p in (nom or "?").replace("-", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def _nom_client():
    if g.utilisateur:
        return g.utilisateur["nom"]
    return g.client["contact"] or g.client["societe"]


def titulaire():
    """Vrai pour le titulaire du compte, faux pour un utilisateur rattaché."""
    return g.utilisateur is None


def peut_acheter():
    return titulaire() or bool(g.utilisateur["peut_acheter"])


def _reserve_titulaire():
    if not titulaire():
        flash("Réservé au titulaire du compte.", "erreur")
        return redirect(url_for("fs.dashboard"))
    return None


@bp.app_template_filter("euros")
def _fmt_euros(v):
    """1234.5 -> « 1 234,50 € » (espace fine insécable pour les milliers)."""
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + " €"


MVT_EN = [("Fichier ", "File "), ("Remboursement ", "Refund "), ("Solde annulé (compte supprimé)", "Balance cancelled"),
          ("crédits fileservice", "file service credits"), ("crédits offerts", "bonus credits"), (" facture ", " invoice "),
          ("Ajustement par l'atelier", "Adjustment by the shop")]


@bp.app_template_filter("libelle")
def _fmt_libelle(txt):
    """Libellé d'un mouvement de crédits, traduit pour les préfixes générés par le logiciel."""
    if langue() == "fr":
        return txt
    for fr, en in MVT_EN:
        txt = txt.replace(fr, en)
    return txt


@bp.app_template_filter("credits")
def _fmt_credits(n):
    return f"{n:+d}" if isinstance(n, int) else n


@bp.app_template_filter("date_fr")
def _fmt_date(s, heure=True):
    """'2026-09-24 14:02:11' -> '24/09/2026 · 14:02'."""
    if not s:
        return ""
    txt = f"{s[8:10]}/{s[5:7]}/{s[:4]}"
    return txt + (f" · {s[11:16]}" if heure and len(s) >= 16 else "")


@bp.app_template_filter("taille")
def _fmt_taille(n):
    return f"{n / 1024 / 1024:.2f} Mo".replace(".", ",") if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} Ko"


# --- Sécurité : CSRF + session ----------------------------------------------

@bp.before_request
def _securite():
    if request.method == "POST" and request.endpoint not in SANS_CSRF:
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
        attendu = session.get("csrf") or ""
        if not attendu or not hmac.compare_digest(sent, attendu):
            if request.is_json:
                return jsonify({"erreur": "Session expirée, rechargez la page."}), 400
            flash("Session expirée, merci de réessayer.")
            return redirect(request.url)

    g.client = g.utilisateur = None
    cid = session.get("client_id")
    if cid:
        c = comptes.get_client(_db(), cid)
        uid = session.get("utilisateur_id")
        u = comptes.get_utilisateur(_db(), uid, cid) if uid else None
        if c and c["statut"] == "actif" and (not uid or (u and u["actif"])):
            g.client, g.utilisateur = c, u
        else:
            session.pop("client_id", None)
            session.pop("utilisateur_id", None)
    if g.client is None and request.endpoint not in PUBLIC_ENDPOINTS:
        if request.is_json:
            return jsonify({"erreur": "Connexion requise."}), 401
        suite = request.full_path.rstrip("?")
        return redirect(url_for("fs.login", next=suite) if suite != "/" else url_for("fs.login"))
    return None


def langue():
    return traductions.normaliser(request.cookies.get("lang", traductions.DEFAUT))


def t(texte):
    return traductions.traduire(texte, langue())


@bp.route("/langue/<code>")
def set_langue(code):
    code = traductions.normaliser(code)
    cible = _safe_next(request.args.get("next"))
    resp = redirect(cible)
    resp.set_cookie("lang", code, max_age=31536000, samesite="Lax")
    if g.utilisateur:
        with comptes.connect(_db()) as con:
            con.execute("UPDATE utilisateurs SET langue = ? WHERE id = ?", (code, g.utilisateur["id"]))
    elif g.client:
        comptes.changer_langue(_db(), g.client["id"], code)   # ses e-mails suivront cette langue
    return resp


@bp.context_processor
def _inject():
    user = None
    c = getattr(g, "client", None)
    if c:
        st = demandes.stats(_db(), c["id"])
        nom = g.utilisateur["nom"] if g.utilisateur else (c["contact"] or c["societe"])
        user = {"company": c["societe"], "contact": nom, "initials": _initiales(nom), "credits": c["credits"],
                "level": c["niveau"], "open_count": st["ouverts"], "titulaire": titulaire(),
                "peut_acheter": peut_acheter()}
    return {"shop": shop(), "user": user, "service": service_status(), "statuses": demandes.STATUTS,
            "csrf_token": csrf_token, "t": t, "langue": langue(), "langues": traductions.LANGUES,
            "version": current_app.config.get("APP_VERSION", "")}


def _safe_next(raw):
    raw = (raw or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or ":" in raw:
        return url_for("fs.dashboard")
    return raw


# --- Comptes -----------------------------------------------------------------

@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if g.client:
        return redirect(url_for("fs.dashboard"))
    erreur, email = None, ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        cles = ("ip:" + _ip(), "email:" + email)
        if LIMITEUR.bloque(*cles):
            erreur = "Trop de tentatives. Réessayez dans 15 minutes ou réinitialisez votre mot de passe."
        else:
            res = comptes.authentifier_compte(_db(), email, request.form.get("password", ""))
            c, u = res if res else (None, None)
            if not c:
                LIMITEUR.echec(*cles)
                erreur = "E-mail ou mot de passe incorrect."
            elif c["statut"] == "en_attente":
                erreur = "Votre compte est en attente de validation par l'atelier. Vous recevrez un e-mail dès son ouverture."
            elif c["statut"] == "bloque":
                erreur = "Ce compte est suspendu. Contactez l'atelier."
            else:
                LIMITEUR.reussite(*cles)
                session.clear()
                session["client_id"] = c["id"]
                if u:
                    session["utilisateur_id"] = u["id"]
                session.permanent = bool(request.form.get("remember"))
                return redirect(_safe_next(request.args.get("next")))
    return render_template("fs/login.html", mode="login", erreur=erreur, email=email)


CHAMPS_INSCRIPTION = ("societe", "siret", "tva", "contact", "email", "tel", "adresse", "code_postal", "ville", "pays")


@bp.route("/inscription", methods=["GET", "POST"])
def register():
    if g.client:
        return redirect(url_for("fs.dashboard"))
    erreur, form = None, {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in CHAMPS_INSCRIPTION}
        if not request.form.get("cgv"):
            erreur = "Merci d'accepter les conditions générales de vente."
        elif LIMITEUR.bloque("inscription:" + _ip()):
            erreur = "Trop de demandes depuis votre connexion. Réessayez plus tard."
        else:
            try:
                cid = comptes.creer_client(_db(), mdp=request.form.get("password", ""), **form)
                comptes.changer_langue(_db(), cid, langue())
            except comptes.ErreurCompte as e:
                erreur = str(e)
            else:
                LIMITEUR.echec("inscription:" + _ip())   # compte les inscriptions, pas seulement les erreurs
                _mail(form["email"], *traductions.mail("inscription", langue(), atelier=shop()["name"],
                                                       societe=form["societe"]))
                _mail_atelier(f"Nouvelle inscription fileservice : {form['societe']}",
                              f"Société : {form['societe']}\nSIRET : {form['siret']}\nTVA : {form['tva'] or '—'}\n"
                              f"Contact : {form['contact'] or '—'}\nE-mail : {form['email']}\nTél. : {form['tel'] or '—'}\n"
                              f"Adresse : {form['adresse']} {form['code_postal']} {form['ville']} {form['pays']}\n\n"
                              "À valider dans l'outil interne, onglet Clients.")
                return render_template("fs/login.html", mode="registered", societe=form["societe"])
    return render_template("fs/login.html", mode="register", erreur=erreur, form=form)


@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot():
    envoye = False
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not LIMITEUR.bloque("oubli:" + _ip()):
            LIMITEUR.echec("oubli:" + _ip())
            c = comptes.client_par_email(_db(), email)
            u = None if c else comptes.utilisateur_par_email(_db(), email)
            if u and u["actif"]:
                c = comptes.get_client(_db(), u["client_id"])
            if c and c["statut"] != "bloque":
                jeton = comptes.creer_jeton(_db(), c["id"], utilisateur_id=u["id"] if u else None)
                dest, lg = (u["email"], u["langue"]) if u else (c["email"], c.get("langue"))
                _mail(dest, *traductions.mail("reset", lg, atelier=shop()["name"], lien=_url_publique("fs.reset", jeton=jeton)))
        envoye = True   # même réponse que le compte existe ou non
    return render_template("fs/login.html", mode="forgot", envoye=envoye)


@bp.route("/reinitialiser/<jeton>", methods=["GET", "POST"])
def reset(jeton):
    if not comptes.jeton_valide(_db(), jeton):
        return render_template("fs/login.html", mode="reset", invalide=True)
    erreur = None
    if request.method == "POST":
        if request.form.get("password") != request.form.get("password2"):
            erreur = "Les deux mots de passe ne correspondent pas."
        else:
            try:
                comptes.reinitialiser_mdp(_db(), jeton, request.form.get("password", ""))
            except comptes.ErreurCompte as e:
                erreur = str(e)
            else:
                flash("Mot de passe modifié. Vous pouvez vous connecter.")
                return redirect(url_for("fs.login"))
    return render_template("fs/login.html", mode="reset", erreur=erreur, jeton=jeton)


@bp.route("/deconnexion", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("fs.login"))


# --- Tableau de bord ---------------------------------------------------------

@bp.route("/")
def dashboard():
    st = demandes.stats(_db(), g.client["id"])
    recents = demandes.lister(_db(), g.client["id"], limite=6)
    attente = next((d for d in recents if d["statut"] == "attente"), None)
    return render_template("fs/dashboard.html", nav="dashboard", files=recents, stats=st,
                           hours=hours_table(), attente=attente, remise=_remise_client()["remise"])


# --- Envoi d'un fichier ------------------------------------------------------

CHAMPS_VEHICULE = ("marque", "modele", "moteur", "annee", "boite", "km", "vin", "immat")
CHAMPS_LECTURE = ("outil", "ecu", "methode")


def creer_demande(champs, prestations, fichier_nom, contenu, source="site", annexes=None):
    """Crée une demande pour le client connecté (g.client) : site et API passent par ici.
    annexes : [(nom, octets)] fichiers complémentaires. Lève ErreurCompte.
    Renvoie (demande, livree_automatiquement)."""
    detection = {}
    detect = current_app.config.get("FS_DETECT")
    if detect and contenu:
        try:
            detection = detect(contenu, fichier_nom or "") or {}
        except Exception:
            current_app.logger.exception("Détection du calculateur impossible")
    did = demandes.creer(
        _db(), _files(), g.client["id"], categorie=champs.get("categorie", ""), prestations=prestations,
        siege=bool(champs.get("siege")), garantie=champs.get("garantie", ""), retour=champs.get("retour", ""),
        vehicule={k: champs.get(k, "") for k in CHAMPS_VEHICULE},
        lecture={k: champs.get(k, "") for k in CHAMPS_LECTURE},
        commentaire=champs.get("comment", "") or champs.get("commentaire", ""), fichier_nom=fichier_nom or "",
        contenu=contenu, detection=detection, annexes=annexes,
        express=catalogue.supplement_express(reglages(), _vrai(champs.get("express"))),
        utilisateur_id=g.utilisateur["id"] if getattr(g, "utilisateur", None) else None, **_remise_client())
    d = demandes.get(_db(), did)
    livre = _livraison_auto_reception(d, contenu)
    veh = " ".join(v for v in (d["vehicule"].get("marque"), d["vehicule"].get("modele"),
                               d["vehicule"].get("moteur")) if v)
    _mail_atelier(("⚡ EXPRESS · " if d["express"] else "") + f"Nouveau fichier {d['numero']} · {g.client['societe']}"
                  + (" (API)" if source == "api" else ""),
                  f"Client : {g.client['societe']} ({g.client['email']})"
                  + (f" · envoyé par {d['envoye_par']}" if d.get("envoye_par") else "") + f"\nVéhicule : {veh or '—'}\n"
                  f"Calculateur : {d['lecture'].get('ecu') or d['detection'].get('plateforme') or '—'}\n"
                  f"Prestations : {' + '.join(l['nom'] for l in d['lignes'])}\nCrédits : {d['total']}\n"
                  f"Commentaire : {d['commentaire'] or '—'}\n"
                  + (f"Fichiers complémentaires : {len(annexes)}\n" if annexes else "") + "\n"
                  + ("Livré AUTOMATIQUEMENT (solution même stock, patch propre)." if livre else
                     "À traiter dans l'outil interne, onglet Fileservice."))
    try:
        executer_relances(current_app._get_current_object(), request.host_url)   # solde bas éventuel
    except Exception:
        current_app.logger.exception("Relances impossibles")
    return demandes.get(_db(), did), livre


def _vrai(v):
    return str(v or "").strip().lower() in ("1", "true", "on", "oui", "yes")


def _annexes_envoyees(liste):
    return [(a.filename, a.read()) for a in liste if a and a.filename]


@bp.route("/nouveau", methods=["GET", "POST"])
def new_file():
    if request.method == "POST":
        f = request.files.get("file")
        try:
            d, livre = creer_demande(request.form, request.form.getlist("prestas"),
                                     f.filename if f else "", f.read() if f else b"",
                                     annexes=_annexes_envoyees(request.files.getlist("annexes")))
        except comptes.ErreurCompte as e:
            flash(str(e), "erreur")
            return redirect(url_for("fs.new_file"))
        if livre:
            flash(f"Demande {d['numero']} : votre fichier est déjà prêt ! {d['total']} crédits débités.")
        else:
            flash(f"Demande {d['numero']} envoyée : {d['total']} crédits débités. Vous serez prévenu par e-mail.")
        return redirect(url_for("fs.file_detail", numero=d["numero"]))
    return render_template("fs/new.html", nav="new", categories=catalogue.CATEGORIES,
                           prestations=catalogue.PRESTATIONS, services=catalogue.SERVICES,
                           garanties=catalogue.GARANTIES, retours=catalogue.RETOURS, tools=TOOLS,
                           express=catalogue.express(reglages()), annexes_max=demandes.ANNEXES_MAX)


def _livraison_auto_reception(d, contenu):
    """Option « livraison automatique à la réception » (réglages atelier). Ne lève jamais."""
    if not reglages().get("livraison_auto") or not current_app.config.get("FS_SOLUTIONS_DB"):
        return False
    try:
        prep = livraison_auto.preparer(d, contenu, current_app.config["FS_SOLUTIONS_DB"])
        if not prep["ok"]:
            return False
        cr = prep["compte_rendu"]
        demandes.livrer(_db(), _files(), d["id"], livraison_auto.nom_fichier(d), prep["patched"],
                        f"{' + '.join(cr['types'])} · checksum {cr['checksum'] or 'OK'}")
        livraison_auto.journaliser(current_app.config["FS_SOLUTIONS_DB"], d, prep, "automatique")
        prevenir(reglages(), d, "fichier_pret", _url_publique("fs.file_detail", numero=d["numero"]),
                 journal_dir=current_app.config["FS_DATA_DIR"], db_path=_db(), config_path=current_app.config["FS_CONFIG"])
        return True
    except Exception:
        current_app.logger.exception("Livraison automatique impossible pour %s", d["numero"])
        return False


@bp.route("/tarif", methods=["POST"])
def tarif():
    """Calcul du tarif côté serveur : seule source de vérité pour les prix."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    codes = data.get("prestations") or []
    if not isinstance(codes, list):
        codes = []
    d = catalogue.devis(str(data.get("categorie", "")), [str(c) for c in codes][:20],
                        siege=bool(data.get("siege")), garantie=str(data.get("garantie") or ""),
                        express=catalogue.supplement_express(reglages(), bool(data.get("express"))), **_remise_client())
    for ligne in d["lignes"]:
        ligne["nom"] = t(ligne["nom"]) if not ligne["nom"].startswith("Remise ") else \
            ligne["nom"].replace("Remise ", t("Remise") + " ", 1)
    return jsonify(d)


def _remise_client():
    """Remise du niveau du client connecté (réglages atelier)."""
    niveau = g.client["niveau"] if g.client else ""
    return {"remise": catalogue.remises(reglages()).get(niveau, 0), "niveau": niveau}


# --- Suivi des demandes ------------------------------------------------------

@bp.route("/fichiers")
def files():
    rows = demandes.lister(_db(), g.client["id"])
    st = demandes.stats(_db(), g.client["id"])
    return render_template("fs/files.html", nav="files", files=rows, counts=st["counts"])


def _demande_ou_404(numero):
    d = demandes.get_par_numero(_db(), numero, g.client["id"])
    if not d:
        abort(404)
    return d


@bp.route("/fichiers/<numero>")
def file_detail(numero):
    d = _demande_ou_404(numero)
    liv = demandes.livrables(_db(), d["id"])
    msgs = demandes.messages(_db(), d["id"], marquer_lus_pour="client")
    etapes = {"recu": 0, "en_cours": 1, "attente": 1, "refuse": 1, "pret": 2}
    reached = 3 if d["telecharge_le"] else etapes.get(d["statut"], 0)
    return render_template("fs/file.html", nav="files", f=d, livrables=liv, thread=msgs, reached=reached,
                           annexes=demandes.annexes(_db(), d["id"]),
                           revision=demandes.revision_possible(d, liv[0] if liv else None),
                           revision_jours=demandes.REVISION_JOURS,
                           categorie_nom=next((c["nom"] for c in catalogue.CATEGORIES if c["code"] == d["categorie"]), ""))


@bp.route("/fichiers/<numero>/recapitulatif")
def file_recap(numero):
    """Récapitulatif imprimable (PDF via le navigateur) d'une demande."""
    d = _demande_ou_404(numero)
    return render_template("fs/recapitulatif.html", f=d, livrables=demandes.livrables(_db(), d["id"]),
                           annexes=demandes.annexes(_db(), d["id"]), vendeur=reglages().get("societe") or {},
                           prix_credit=catalogue.PRIX_CREDIT_EUR)


@bp.route("/fichiers/<numero>/message", methods=["POST"])
def file_message(numero):
    d = _demande_ou_404(numero)
    pj = request.files.get("pj")
    try:
        demandes.ajouter_message(_db(), _files(), d["id"], "client", _nom_client(), request.form.get("texte", ""),
                                 pj_nom=pj.filename if pj else "", pj_contenu=(pj.read() or None) if pj else None)
    except comptes.ErreurCompte as e:
        flash(str(e), "erreur")
    else:
        _mail_atelier(f"Message client sur {d['numero']} · {g.client['societe']}",
                      f"{_nom_client()} a écrit sur {d['numero']} :\n\n{request.form.get('texte', '')}\n\n"
                      "Réponse dans l'outil interne, onglet Fileservice.")
    return redirect(url_for("fs.file_detail", numero=numero) + "#conversation")


@bp.route("/fichiers/<numero>/revision", methods=["POST"])
def file_revision(numero):
    d = _demande_ou_404(numero)
    try:
        demandes.demander_revision(_db(), _files(), d["id"], g.client["id"], _nom_client(),
                                   request.form.get("texte", ""))
    except comptes.ErreurCompte as e:
        flash(str(e), "erreur")
    else:
        flash("Demande de révision envoyée à l'atelier.")
        _mail_atelier(f"Révision demandée sur {d['numero']} · {g.client['societe']}",
                      f"{request.form.get('texte', '')}\n\nÀ traiter dans l'outil interne, onglet Fileservice.")
    return redirect(url_for("fs.file_detail", numero=numero))


def _envoyer(d, fichier, nom):
    p = demandes.chemin(_files(), d["id"], fichier)
    if not p:
        abort(404)
    return send_file(p, as_attachment=True, download_name=nom, mimetype="application/octet-stream")


@bp.route("/fichiers/<numero>/original")
def file_original(numero):
    d = _demande_ou_404(numero)
    return _envoyer(d, "original_" + d["fichier_nom"], d["fichier_nom"])


@bp.route("/fichiers/<numero>/livre/<int:version>")
def file_livre(numero, version):
    d = _demande_ou_404(numero)
    liv = next((l for l in demandes.livrables(_db(), d["id"]) if l["version"] == version), None)
    if not liv:
        abort(404)
    demandes.marquer_telecharge(_db(), d["id"])
    return _envoyer(d, liv["fichier"], f"{d['numero']}_v{version}_{liv['nom']}")


@bp.route("/fichiers/<numero>/annexe/<int:aid>")
def file_annexe(numero, aid):
    d = _demande_ou_404(numero)
    a = next((a for a in demandes.annexes(_db(), d["id"]) if a["id"] == aid and a["fichier"]), None)
    if not a:
        abort(404)
    return _envoyer(d, a["fichier"], a["nom"])


@bp.route("/fichiers/<numero>/pj/<int:mid>")
def file_pj(numero, mid):
    d = _demande_ou_404(numero)
    m = next((m for m in demandes.messages(_db(), d["id"]) if m["id"] == mid and m["pj_fichier"]), None)
    if not m:
        abort(404)
    return _envoyer(d, m["pj_fichier"], m["pj_nom"])


# --- Crédits, paiement, factures ---------------------------------------------

@bp.route("/credits")
def credits():
    if not peut_acheter():
        return render_template("fs/credits.html", nav="credits", packs=[], restreint=True,
                               prix_credit=catalogue.PRIX_CREDIT_EUR, tva=catalogue.TVA,
                               autoliquidation=factures.autoliquidation(g.client), paiement_en_ligne=False,
                               transactions=comptes.mouvements(_db(), g.client["id"]), factures=[])
    return render_template("fs/credits.html", nav="credits", packs=catalogue.PACKS_CREDITS,
                           prix_credit=catalogue.PRIX_CREDIT_EUR, tva=catalogue.TVA,
                           autoliquidation=factures.autoliquidation(g.client),
                           paiement_en_ligne=stripe_api.configure(reglages().get("stripe") or {}),
                           transactions=comptes.mouvements(_db(), g.client["id"]),
                           factures=factures.lister(_db(), g.client["id"]))


def _pack(index):
    try:
        return catalogue.PACKS_CREDITS[int(index)]
    except (ValueError, IndexError):
        abort(404)


@bp.route("/credits/acheter/<int:index>", methods=["POST"])
def buy(index):
    if not peut_acheter():
        abort(403)
    pack = _pack(index)
    cfg = reglages().get("stripe") or {}
    if not stripe_api.configure(cfg):
        flash("Le paiement en ligne n'est pas encore activé : contactez l'atelier pour un virement.", "erreur")
        return redirect(url_for("fs.credits"))
    montants = factures.montants(pack["prix_eur"], g.client)
    try:
        url = stripe_api.creer_session(
            cfg, montant_centimes=round(montants["ttc"] * 100),
            libelle=f"Pack {pack['credits']} crédits" + (f" + {pack['bonus']} offerts" if pack["bonus"] else ""),
            email=g.client["email"], reference=f"{g.client['id']}:{index}",
            succes=_url_publique("fs.buy_success") + "?session_id={CHECKOUT_SESSION_ID}",
            annulation=_url_publique("fs.credits"))
    except stripe_api.ErreurStripe as e:
        current_app.logger.error("Stripe : %s", e)
        flash("Le paiement est momentanément indisponible. Réessayez ou contactez l'atelier.", "erreur")
        return redirect(url_for("fs.credits"))
    return redirect(url, code=303)


def _crediter_session(sess):
    """Crédite le compte pour une session Stripe payée — idempotent (webhook + retour navigateur)."""
    if sess.get("payment_status") != "paid":
        return None
    try:
        client_id, index = (int(x) for x in (sess.get("client_reference_id") or "").split(":"))
        pack = catalogue.PACKS_CREDITS[index]
    except (ValueError, IndexError):
        current_app.logger.error("Session Stripe inattendue : %s", sess.get("id"))
        return None
    client = comptes.get_client(_db(), client_id)
    if not client:
        return None
    try:
        fac = factures.enregistrer_achat(_db(), client, pack, paiement="Carte bancaire (Stripe)",
                                         reference=sess["id"], total_centimes=sess.get("amount_total"),
                                         vendeur=reglages().get("societe") or {})
    except comptes.ErreurCompte as e:
        current_app.logger.error("Paiement %s non crédité : %s", sess.get("id"), e)
        _mail_atelier("Paiement Stripe à vérifier",
                      f"Session {sess.get('id')} ({client['societe']}) : {e}\nCrédits NON ajoutés : à traiter à la main.")
        return None
    if fac and fac.get("nouvelle"):
        _mail(client["email"], *traductions.mail("facture", client.get("langue"), atelier=shop()["name"],
                                                 numero=fac["numero"], credits=pack["credits"] + pack["bonus"]))
    return fac


@bp.route("/credits/merci")
def buy_success():
    cfg = reglages().get("stripe") or {}
    sid = request.args.get("session_id", "")
    try:
        sess = stripe_api.lire_session(cfg, sid) if sid.startswith("cs_") else None
    except stripe_api.ErreurStripe:
        sess = None
    fac = _crediter_session(sess) if sess else None
    if fac and fac["client_id"] == g.client["id"]:
        flash(f"Paiement reçu, merci ! Crédits ajoutés, facture {fac['numero']} disponible ci-dessous.")
    else:
        flash("Paiement en cours de confirmation : vos crédits apparaîtront dans quelques instants.")
    return redirect(url_for("fs.credits"))


@bp.route("/stripe/webhook", methods=["POST"])
@bp.route("/espace/stripe/webhook", methods=["POST"])   # ancienne adresse, si déjà déclarée chez Stripe
def stripe_webhook():
    cfg = reglages().get("stripe") or {}
    try:
        event = stripe_api.verifier_webhook(cfg, request.get_data(), request.headers.get("Stripe-Signature", ""))
    except stripe_api.ErreurStripe as e:
        current_app.logger.warning("Webhook Stripe refusé : %s", e)
        return jsonify({"erreur": "signature"}), 400
    if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        _crediter_session((event.get("data") or {}).get("object") or {})
    return jsonify({"ok": True})


@bp.route("/factures/<numero>")
def invoice(numero):
    if not peut_acheter():
        abort(403)
    fac = factures.get(_db(), numero, g.client["id"])
    if not fac:
        abort(404)
    return render_template("fs/facture.html", fac=fac)


# --- Paramètres & support ----------------------------------------------------

@bp.route("/parametres", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        action = request.form.get("action")
        if action != "mdp" and not titulaire():
            abort(403)
        try:
            if action == "mdp":
                if request.form.get("nouveau") != request.form.get("nouveau2"):
                    raise comptes.ErreurCompte("Les deux nouveaux mots de passe ne correspondent pas.")
                if g.utilisateur:
                    comptes.changer_mdp_utilisateur(_db(), g.utilisateur["id"], request.form.get("actuel", ""),
                                                    request.form.get("nouveau", ""))
                else:
                    comptes.changer_mdp(_db(), g.client["id"], request.form.get("actuel", ""),
                                        request.form.get("nouveau", ""))
                flash("Mot de passe modifié.")
            elif action == "sms":
                comptes.changer_sms(_db(), g.client["id"], request.form.get("mobile", ""), bool(request.form.get("sms_actif")))
                flash("Préférences SMS enregistrées.")
            elif action == "utilisateur_ajout":
                _ajouter_utilisateur()
            elif action in ("utilisateur_actif", "utilisateur_achat", "utilisateur_suppr"):
                uid = int(request.form.get("id") or 0)
                if action == "utilisateur_suppr":
                    comptes.supprimer_utilisateur(_db(), g.client["id"], uid)
                    flash("Utilisateur retiré : il ne peut plus se connecter.")
                elif action == "utilisateur_actif":
                    comptes.modifier_utilisateur(_db(), g.client["id"], uid, actif=_vrai(request.form.get("valeur")))
                    flash("Utilisateur mis à jour.")
                else:
                    comptes.modifier_utilisateur(_db(), g.client["id"], uid, peut_acheter=_vrai(request.form.get("valeur")))
                    flash("Utilisateur mis à jour.")
            else:
                comptes.modifier_profil(_db(), g.client["id"], **{k: request.form.get(k, "") for k in (
                    "contact", "tel", "tva", "adresse", "code_postal", "ville", "pays")})
                flash("Coordonnées enregistrées.")
        except comptes.ErreurCompte as e:
            flash(str(e), "erreur")
        return redirect(url_for("fs.settings"))
    return render_template("fs/settings.html", nav="settings", c=g.client, moi=g.utilisateur,
                           utilisateurs=comptes.lister_utilisateurs(_db(), g.client["id"]) if titulaire() else [],
                           utilisateurs_max=comptes.UTILISATEURS_MAX, sms_dispo=sms.configure(reglages()))


def _ajouter_utilisateur():
    """Le titulaire ajoute une personne : invitation par e-mail (lien 7 jours) dans sa langue à lui."""
    email = (request.form.get("email") or "").strip().lower()
    uid = comptes.creer_utilisateur(_db(), g.client["id"], nom=request.form.get("nom", ""), email=email,
                                    peut_acheter=bool(request.form.get("peut_acheter")), langue=langue())
    jeton = comptes.creer_jeton(_db(), g.client["id"], duree=comptes.JETON_INVITATION, utilisateur_id=uid)
    lien = _url_publique("fs.reset", jeton=jeton)
    ok = _mail(email, *traductions.mail("invitation_membre", langue(), atelier=shop()["name"],
                                        societe=g.client["societe"], nom=_nom_client(), lien=lien, email=email))
    if ok:
        flash(f"Invitation envoyée à {email} (lien valable 7 jours).")
    else:
        flash(f"Utilisateur ajouté, mais l'e-mail n'a pas pu partir : transmettez-lui ce lien (valable 7 jours) : {lien}")


@bp.route("/parametres/mes-donnees")
def settings_export():
    """Droit d'accès RGPD : toutes les données du compte en JSON."""
    if not titulaire():
        abort(403)
    data = demandes.export_client(_db(), g.client["id"])
    resp = current_app.response_class(json.dumps(data, ensure_ascii=False, indent=2), mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="mes-donnees-{dt.date.today():%Y%m%d}.json"'
    return resp


@bp.route("/parametres/api", methods=["GET", "POST"])
def settings_api():
    """Clés d'API revendeur du client (création : la clé n'est affichée qu'une fois)."""
    import api
    refus = _reserve_titulaire()
    if refus:
        return refus
    api.init_db(_db())
    api_active = bool(reglages().get("api_active"))
    nouvelle = None
    if request.method == "POST":
        try:
            if request.form.get("action") == "revoquer":
                api.revoquer_cle(_db(), g.client["id"], int(request.form.get("id") or 0))
                flash("Clé révoquée : elle ne fonctionne plus.")
                return redirect(url_for("fs.settings_api"))
            if not api_active:
                raise comptes.ErreurCompte("L'API n'est pas activée par l'atelier.")
            nouvelle = api.creer_cle(_db(), g.client["id"], request.form.get("nom", ""))
        except comptes.ErreurCompte as e:
            flash(str(e), "erreur")
            return redirect(url_for("fs.settings_api"))
    base = (current_app.config.get("FS_PUBLIC_URL") or reglages().get("public_url") or request.host_url).rstrip("/")
    return render_template("fs/api.html", nav="settings", cles=api.lister_cles(_db(), g.client["id"]),
                           nouvelle=nouvelle, api_active=api_active, base=base, categories=catalogue.CATEGORIES)


@bp.route("/support")
def support():
    return render_template("fs/support.html", nav="support", hours=hours_table(),
                           revision_jours=demandes.REVISION_JOURS)


# --- Application (PWA) et notifications -----------------------------------------

@bp.route("/manifest.webmanifest")
def manifest():
    nom = shop()["name"]
    icones = [{"src": url_for("static", filename=f"pwa/{f}"), "sizes": taille, "type": "image/png", "purpose": but}
              for f, taille, but in (("icon-192.png", "192x192", "any"), ("icon-512.png", "512x512", "any"),
                                     ("icon-maskable-512.png", "512x512", "maskable"))]
    data = {"name": f"{nom} · {t('File service')}", "short_name": nom, "lang": langue(),
            "description": t("Vos fichiers moteur, préparés par des spécialistes."), "id": "/", "start_url": "/?source=app",
            "scope": "/", "display": "standalone", "background_color": "#0a0c0f", "theme_color": "#0a0c0f",
            "icons": icones,
            "shortcuts": [{"name": t("Nouveau fichier"), "url": url_for("fs.new_file")},
                          {"name": t("Mes fichiers"), "url": url_for("fs.files")}]}
    resp = current_app.response_class(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/sw.js")
def service_worker():
    resp = current_app.response_class(render_template("fs/sw.js"), mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"          # le navigateur vérifie la version à chaque visite
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.route("/hors-ligne")
def hors_ligne():
    return render_template("fs/hors_ligne.html")


@bp.route("/push/cle")
def push_cle():
    if not push.DISPONIBLE:
        return jsonify({"disponible": False})
    _, cle = push.cles(current_app.config["FS_CONFIG"])
    return jsonify({"disponible": True, "cle": cle})


@bp.route("/push/abonnement", methods=["POST"])
def push_abonnement():
    b = request.get_json(silent=True) or {}
    try:
        push.init_db(_db())
        push.abonner(_db(), g.client["id"], b.get("abonnement"), g.utilisateur["id"] if g.utilisateur else None,
                     appareil=(request.headers.get("User-Agent") or "")[:120])
    except push.ErreurPush as e:
        return jsonify({"erreur": t(str(e))}), 400
    return jsonify({"ok": True})


@bp.route("/push/desabonnement", methods=["POST"])
def push_desabonnement():
    push.init_db(_db())
    push.desabonner(_db(), g.client["id"], (request.get_json(silent=True) or {}).get("endpoint"))
    return jsonify({"ok": True})


@bp.route("/push/test", methods=["POST"])
def push_test():
    push.init_db(_db())
    n = push.envoyer(_db(), current_app.config["FS_CONFIG"], g.client["id"],
                     {"titre": shop()["name"], "texte": t("Les notifications fonctionnent sur cet appareil."), "url": "/",
                      "tag": "test"}, sujet=_sujet_vapid(reglages()))
    return jsonify({"ok": bool(n), "appareils": n})


@bp.route("/push/renouveler", methods=["POST"])
def push_renouveler():
    """Appelé par le service worker quand le navigateur change l'abonnement : l'ancienne adresse
    d'envoi (secrète, connue du seul appareil) sert de preuve."""
    b = request.get_json(silent=True) or {}
    push.init_db(_db())
    with comptes.connect(_db()) as con:
        ancien = con.execute("SELECT client_id, utilisateur_id, appareil FROM abonnements_push WHERE endpoint = ?",
                             (str(b.get("ancien") or ""),)).fetchone()
    if not ancien:
        return jsonify({"erreur": "Abonnement inconnu."}), 404
    try:
        push.desabonner(_db(), ancien["client_id"], b.get("ancien"))
        push.abonner(_db(), ancien["client_id"], b.get("nouveau"), ancien["utilisateur_id"], ancien["appareil"])
    except push.ErreurPush as e:
        return jsonify({"erreur": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/legal/<cle>")
def legal(cle):
    if cle not in pages_legales.PAGES:
        abort(404)
    return render_template("fs/legal.html", cle=cle, titre=pages_legales.PAGES[cle],
                           contenu=pages_legales.rendre(reglages(), cle), pages=pages_legales.PAGES)


def prevenir(cfg, d, cle, lien, journal_dir=None, db_path=None, config_path=None, **champs):
    """Prévient le client d'un événement sur sa demande : e-mail au titulaire (et à l'utilisateur
    qui l'a envoyée), SMS si le client l'a demandé, notification sur les appareils où l'application
    est installée. Renvoie un texte pour l'atelier."""
    nom = cfg.get("shop_name") or "E85-FRANCE"
    smtp = cfg.get("smtp") or {}
    ok, err = mailer.envoyer(smtp, d["email"], *traductions.mail(cle, d.get("langue"), atelier=nom, numero=d["numero"],
                                                                 lien=lien, **champs),
                             nom_expediteur=nom, journal_dir=journal_dir)
    infos = ["Client prévenu par e-mail." if ok else err]
    auteur = d.get("envoye_par_email")
    if auteur and auteur.lower() != (d["email"] or "").lower() and not auteur.endswith("@invalid"):
        mailer.envoyer(smtp, auteur, *traductions.mail(cle, d.get("envoye_par_langue"), atelier=nom, numero=d["numero"],
                                                       lien=lien, **champs), nom_expediteur=nom, journal_dir=journal_dir)
    if cle in traductions.SMS and d.get("sms_actif") and d.get("sms_mobile") and sms.configure(cfg):
        sok, serr = sms.envoyer(cfg, d["sms_mobile"], traductions.sms(cle, d.get("langue"), atelier=nom,
                                                                      numero=d["numero"], lien=lien))
        infos.append("SMS envoyé." if sok else serr)
    if db_path and config_path and cle in traductions.PUSH:
        titre, texte = traductions.push(cle, d.get("langue"), numero=d["numero"], vehicule=_vehicule_court(d))
        n = push.envoyer(db_path, config_path, d["client_id"],
                         {"titre": titre, "texte": texte, "url": f"/fichiers/{d['numero']}", "tag": d["numero"]},
                         sujet=_sujet_vapid(cfg))
        if n:
            infos.append(f"Notification envoyée sur {n} appareil(s).")
    return " ".join(i for i in infos if i)


def _vehicule_court(d):
    v = d.get("vehicule") or {}
    return " ".join(x for x in (v.get("marque"), v.get("modele")) if x)


def _sujet_vapid(cfg):
    """Contact transmis aux services push (Google, Apple, Mozilla) en cas de problème d'envoi."""
    email = (cfg.get("societe") or {}).get("email") or (cfg.get("smtp") or {}).get("from") or (cfg.get("smtp") or {}).get("user")
    if email:
        return "mailto:" + email
    return cfg.get("public_url") or "mailto:contact@localhost"


def executer_relances(app, base_url=""):
    """Relances automatiques (solde bas, fichier non téléchargé). Utilisable hors requête."""
    with app.app_context():
        cfg = mailer.lire_config(app.config["FS_CONFIG"])
        base = (app.config.get("FS_PUBLIC_URL") or cfg.get("public_url") or base_url or "").rstrip("/")
        nom = cfg.get("shop_name") or "E85-FRANCE"

        def envoyer(a, sujet, texte):
            mailer.envoyer(cfg.get("smtp") or {}, a, sujet, texte, nom_expediteur=nom,
                           journal_dir=app.config["FS_DATA_DIR"])

        return relances.verifier(app.config["FS_DB"], cfg, envoyer, lambda chemin: base + chemin, nom)

