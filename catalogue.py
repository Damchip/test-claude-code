"""
Catalogue du fileservice : prestations, packs, options et packs de crédits.

Repris de la boutique WooCommerce (hors « LA BOUTIQUE », brouillons et produits
privés). Les prix de prestation sont en CRÉDITS ; 1 crédit = 2,50 €.

Le client coche des prestations ; `devis()` choisit la combinaison la moins
chère de packs et de prestations seules qui couvre exactement la sélection :
le tarif pack s'applique tout seul.
"""

import copy
import json

PRIX_CREDIT_EUR = 2.50   # HT
TVA = 0.20               # prix affichés HT et TTC

CATEGORIES = [
    {"code": "vl", "nom": "Véhicule léger"},
    {"code": "pl", "nom": "Agricole / PL"},
    {"code": "moto", "nom": "Moto / Quad"},
]

# prix_siege : prix quand le calculateur est envoyé au siège pour ouverture du boîtier
# type       : type de solution correspondant dans la bibliothèque (livraison en un clic) ;
#              sans type, la prestation se traite toujours à la main
PRESTATIONS = {
    "vl": [
        {"code": "stage1", "nom": "Stage 1", "desc": "Fichier sur mesure", "prix": 59, "type": "Stage 1"},
        {"code": "e85", "nom": "Adaptation E85", "desc": "Fichier sur mesure bioéthanol", "prix": 59, "prix_siege": 89,
         "type": "E85 / Flexfuel"},
        {"code": "speed", "nom": "Speed limit", "desc": "Réglage du limiteur de vitesse", "prix": 29, "type": "Vmax off"},
        {"code": "startstop", "nom": "Start & Stop", "desc": "Réglage du Start & Stop", "prix": 29},
        {"code": "dtc", "nom": "Suppression DTC", "desc": "Codes défaut à préciser en commentaire", "prix": 29,
         "type": "DTC off"},
        {"code": "immo", "nom": "Réglage IMMO", "desc": "Antidémarrage", "prix": 59},
        {"code": "volet_adm", "nom": "Volet collecteur d'admission", "desc": "Réglage du volet d'admission", "prix": 39},
        {"code": "volet_ech", "nom": "Volet d'échappement", "desc": "Réglage du volet d'échappement", "prix": 39},
        {"code": "torque", "nom": "Torque OFF", "desc": "Limitation de couple", "prix": 19},
        {"code": "stage2", "nom": "Stage 2", "desc": "Pièces modifiées · FAP, catalyseur et EGR conservés", "prix": 89},
        {"code": "boite", "nom": "Boîte automatique (TCU)", "desc": "Passages de rapports et couple de la boîte", "prix": 59},
        {"code": "popbang", "nom": "Pop & Bang", "desc": "Détonations à la décélération (moteurs essence)", "prix": 39},
        {"code": "launch", "nom": "Launch control", "desc": "Aide au départ arrêté", "prix": 39},
        {"code": "rupteur", "nom": "Rupteur (hardcut)", "desc": "Coupure franche au régime maximal", "prix": 29},
    ],
    "pl": [
        {"code": "stage1", "nom": "Stage 1 – PL", "desc": "Fichier sur mesure", "prix": 79, "type": "Stage 1"},
        {"code": "dtc", "nom": "Suppression DTC – PL", "desc": "Codes défaut à préciser en commentaire", "prix": 59,
         "type": "DTC off"},
        {"code": "immo", "nom": "Réglage IMMO – PL", "desc": "Antidémarrage", "prix": 59},
    ],
    "moto": [
        {"code": "stage1", "nom": "Stage 1 – Moto", "desc": "Fichier sur mesure", "prix": 59, "type": "Stage 1"},
        {"code": "speed", "nom": "Speed limit – Moto/Quad", "desc": "Réglage du limiteur de vitesse", "prix": 20,
         "type": "Vmax off"},
        {"code": "popbang", "nom": "Pop & Bang – Moto", "desc": "Détonations à la décélération", "prix": 29},
    ],
}

# Proposés quel que soit le type de véhicule
SERVICES = [
    {"code": "clonage", "nom": "Clonage calculateur", "desc": "Copie vers un calculateur de remplacement", "prix": 40},
    {"code": "injecteurs", "nom": "Nettoyage injecteurs", "desc": "Nettoyage et test d'équilibrage · envoi des injecteurs", "prix": 25},
]

PACKS = {
    "vl": [
        {"nom": "Pack E85 + débridage moteur", "codes": {"e85", "stage1"}, "prix": 99, "prix_siege": 129},
        {"nom": "Pack E85 + Start & Stop", "codes": {"e85", "startstop"}, "prix": 79, "prix_siege": 109},
        {"nom": "Pack E85 + Speed limit", "codes": {"e85", "speed"}, "prix": 79, "prix_siege": 109},
        {"nom": "Pack Stage 1 + Pop & Bang", "codes": {"stage1", "popbang"}, "prix": 89},
    ],
}

GARANTIES = [
    {"code": "g1", "nom": "Garantie Sérénité 1 an", "prix": 20},
    {"code": "g2", "nom": "Garantie Sérénité 2 ans", "prix": 30},
]

# Retour du boîtier envoyé au siège — facturé en euros, hors crédits
RETOURS = [
    {"code": "colissimo", "nom": "Colissimo", "prix_eur": 10},
    {"code": "chronopost", "nom": "Chronopost", "prix_eur": 21},
]

# Packs de crédits (1 crédit = 2,50 €) ; bonus = crédits offerts en plus
PACKS_CREDITS = [
    {"credits": 50, "bonus": 0, "prix_eur": 125},
    {"credits": 100, "bonus": 0, "prix_eur": 250},
    {"credits": 200, "bonus": 10, "prix_eur": 500},
    {"credits": 400, "bonus": 40, "prix_eur": 1000, "vedette": True},
    {"credits": 1000, "bonus": 150, "prix_eur": 2500},
    {"credits": 2000, "bonus": 400, "prix_eur": 5000},
    {"credits": 4000, "bonus": 1000, "prix_eur": 10000},
]


def prestations_de(categorie):
    """Prestations proposées pour un type de véhicule (services inclus)."""
    return PRESTATIONS.get(categorie, []) + SERVICES


def _prix(item, siege):
    return item["prix_siege"] if siege and item.get("prix_siege") is not None else item["prix"]


EXPRESS_NOM = "Traitement express (prioritaire)"


def devis(categorie, codes, siege=False, garantie=None, remise=0, niveau="", express=0):
    """Tarif le plus avantageux pour la sélection.

    remise  : pourcentage accordé au niveau du client (sur les prestations, pas sur la garantie).
    express : supplément en crédits de l'option express (0 = non demandée), hors remise.
    Retourne {"lignes": [{"nom", "credits"}], "total", "economie", "remise", "siege_possible", "erreur"}.
    """
    if categorie not in PRESTATIONS:
        return {"lignes": [], "total": 0, "economie": 0, "siege_possible": False,
                "erreur": "Type de véhicule inconnu."}
    catalogue = {p["code"]: p for p in prestations_de(categorie)}
    choix = list(dict.fromkeys(c for c in codes if c))
    inconnus = [c for c in choix if c not in catalogue]
    if inconnus:
        return {"lignes": [], "total": 0, "economie": 0, "siege_possible": False,
                "erreur": "Prestation indisponible pour ce véhicule : " + ", ".join(inconnus)}

    siege_possible = any(catalogue[c].get("prix_siege") is not None for c in choix)
    siege = siege and siege_possible
    n = len(choix)
    bit = {c: 1 << i for i, c in enumerate(choix)}
    packs = []
    for pk in PACKS.get(categorie, []):
        if pk["codes"] <= set(choix):
            packs.append((sum(bit[c] for c in pk["codes"]), _prix(pk, siege), pk["nom"]))

    # Couverture exacte au moindre coût (programmation dynamique sur les sous-ensembles)
    full = (1 << n) - 1
    best = {0: (0, [])}
    for mask in range(1, full + 1):
        low = mask & -mask
        i = low.bit_length() - 1
        options = []
        prev = best.get(mask ^ low)
        if prev is not None:
            item = catalogue[choix[i]]
            options.append((prev[0] + _prix(item, siege), [(item["nom"], _prix(item, siege))] + prev[1]))
        for pmask, pprix, pnom in packs:
            if pmask & low and pmask & mask == pmask:
                prev = best.get(mask ^ pmask)
                if prev is not None:
                    options.append((prev[0] + pprix, [(pnom, pprix)] + prev[1]))
        if options:
            best[mask] = min(options, key=lambda o: o[0])

    total, lignes = best[full]
    somme_seules = sum(_prix(catalogue[c], siege) for c in choix)
    lignes = [{"nom": nom, "credits": prix} for nom, prix in lignes]
    try:
        remise = max(0.0, min(float(remise or 0), 90.0))
    except (TypeError, ValueError):
        remise = 0.0
    montant_remise = int(round(total * remise / 100)) if total else 0
    if montant_remise:
        lignes.append({"nom": f"Remise {niveau or 'client'} −{remise:g} %", "credits": -montant_remise})
        total -= montant_remise
    g = next((g for g in GARANTIES if g["code"] == garantie), None)
    if g and choix:
        lignes.append({"nom": g["nom"], "credits": g["prix"]})
        total += g["prix"]
    if express and choix:
        lignes.append({"nom": EXPRESS_NOM, "credits": int(express), "code": "express"})
        total += int(express)
    return {"lignes": lignes, "total": total, "economie": somme_seules - best[full][0],
            "remise": montant_remise, "siege_possible": siege_possible, "erreur": ""}


NIVEAUX_DEFAUT = {"Standard": 0, "Partenaire": 10, "VIP": 20}
EXPRESS_DEFAUT = {"actif": False, "credits": 20}


def express(cfg):
    """Réglages de l'option express : {"actif", "credits"} (supplément en crédits, réglé par l'atelier)."""
    r = dict(EXPRESS_DEFAUT)
    brut = cfg.get("express") if isinstance(cfg.get("express"), dict) else {}
    r["actif"] = bool(brut.get("actif", r["actif"]))
    try:
        r["credits"] = max(1, min(int(brut.get("credits", r["credits"])), 1000))
    except (TypeError, ValueError):
        pass
    return r


def supplement_express(cfg, demande):
    """Crédits à ajouter si le client demande l'express et que l'option est active, sinon 0."""
    r = express(cfg)
    return r["credits"] if demande and r["actif"] else 0


def remises(cfg):
    """Pourcentage de remise par niveau (réglages atelier, sinon valeurs par défaut)."""
    raw = cfg.get("remises") if isinstance(cfg.get("remises"), dict) else None
    out = {}
    for k, v in (raw or NIVEAUX_DEFAUT).items():
        try:
            out[str(k)[:30]] = max(0.0, min(float(v), 90.0))
        except (TypeError, ValueError):
            continue
    return out or dict(NIVEAUX_DEFAUT)


# --- Tarifs réglés par l'atelier (outil atelier → Fileservice → Tarifs) --------------
# Seuls les PRIX des prestations existantes se règlent en ligne, et chaque prestation peut être
# retirée de l'offre. Les prestations elles-mêmes (noms, descriptions, types) restent définies ici.
# Réglages gardés dans portal_config.json, clé « tarifs » :
#   {"prestations": {"vl.stage1": {"prix": 69, "prix_siege": 99}}, "services": {"clonage": {"prix": 45}},
#    "packs": {"vl.0": {"prix": 99}}, "garanties": {"g1": {"prix": 20}}, "retours": {"colissimo": {"prix_eur": 10}},
#    "packs_credits": {"50": {"prix_eur": 125, "bonus": 0}}, "masques": ["vl.torque", "clonage"]}

PRIX_MAX = 10000       # crédits
EUR_MAX = 100000       # euros
_DEFAUTS = copy.deepcopy({"PRESTATIONS": PRESTATIONS, "SERVICES": SERVICES, "PACKS": PACKS, "GARANTIES": GARANTIES,
                          "RETOURS": RETOURS, "PACKS_CREDITS": PACKS_CREDITS})
_APPLIQUE = json.dumps({})


def _lignes_tarifs(d):
    """(section, clé, élément, champs réglables, masquable) pour chaque élément du catalogue `d`."""
    for cat, items in d["PRESTATIONS"].items():
        for p in items:
            yield "prestations", f"{cat}.{p['code']}", p, ("prix", "prix_siege") if p.get("prix_siege") is not None else ("prix",), True
    for p in d["SERVICES"]:
        yield "services", p["code"], p, ("prix",), True
    for cat, items in d["PACKS"].items():
        for i, p in enumerate(items):
            yield "packs", f"{cat}.{i}", p, ("prix", "prix_siege") if p.get("prix_siege") is not None else ("prix",), False
    for p in d["GARANTIES"]:
        yield "garanties", p["code"], p, ("prix",), False
    for p in d["RETOURS"]:
        yield "retours", p["code"], p, ("prix_eur",), False
    for p in d["PACKS_CREDITS"]:
        yield "packs_credits", str(p["credits"]), p, ("prix_eur", "bonus"), False


def _nombre(v, maxi):
    try:
        n = float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        raise ValueError("nombre attendu")
    if n != n or not 0 <= n <= maxi:
        raise ValueError(f"entre 0 et {maxi}")
    return int(n) if n == int(n) else round(n, 2)


def normaliser_tarifs(brut):
    """Vérifie les tarifs saisis : uniquement des prix d'éléments existants et des retraits de prestations.
    Lève ValueError (message lisible) si une valeur est invalide. Les valeurs égales à l'origine ne sont pas gardées."""
    brut = brut if isinstance(brut, dict) else {}
    out, masques = {}, []
    demandes_masques = set(str(x) for x in (brut.get("masques") or []) if isinstance(x, str))
    for section, cle, item, champs, masquable in _lignes_tarifs(_DEFAUTS):
        saisie = (brut.get(section) or {}).get(cle)
        if isinstance(saisie, dict):
            for champ in champs:
                if saisie.get(champ) in (None, ""):
                    continue
                maxi = EUR_MAX if champ == "prix_eur" else PRIX_MAX
                try:
                    v = _nombre(saisie[champ], maxi)
                except ValueError as e:
                    raise ValueError(f"{item.get('nom') or cle} : prix invalide ({e}).")
                if champ != "prix_eur":
                    v = int(round(v))
                if v != item.get(champ):
                    out.setdefault(section, {}).setdefault(cle, {})[champ] = v
        if masquable and cle in demandes_masques:
            masques.append(cle)
    if masques:
        out["masques"] = masques
    return out


def appliquer_tarifs(cfg):
    """Applique les tarifs de l'atelier au catalogue (à chaque requête ; ne refait rien si rien n'a changé)."""
    global _APPLIQUE
    t = cfg.get("tarifs") if isinstance(cfg, dict) and isinstance(cfg.get("tarifs"), dict) else {}
    cle = json.dumps(t, sort_keys=True)
    if cle == _APPLIQUE:
        return
    try:
        t = normaliser_tarifs(t)
    except ValueError:
        t = {}
    d = copy.deepcopy(_DEFAUTS)
    masques = set(t.get("masques") or [])
    for section, c, item, champs, _ in _lignes_tarifs(d):
        for champ, v in ((t.get(section) or {}).get(c) or {}).items():
            if champ in champs:
                item[champ] = v
    prestations = {cat: [p for p in items if f"{cat}.{p['code']}" not in masques] for cat, items in d["PRESTATIONS"].items()}
    PRESTATIONS.clear()
    PRESTATIONS.update(prestations)
    SERVICES[:] = [p for p in d["SERVICES"] if p["code"] not in masques]
    PACKS.clear()
    PACKS.update(d["PACKS"])
    GARANTIES[:] = d["GARANTIES"]
    RETOURS[:] = d["RETOURS"]
    PACKS_CREDITS[:] = d["PACKS_CREDITS"]      # jamais retirés : l'ordre sert au paiement Stripe
    _APPLIQUE = cle


def tableau_tarifs(cfg):
    """Pour l'écran des tarifs : chaque élément avec son prix d'origine et son prix actuel."""
    t = cfg.get("tarifs") if isinstance(cfg.get("tarifs"), dict) else {}
    try:
        t = normaliser_tarifs(t)
    except ValueError:
        t = {}
    masques = set(t.get("masques") or [])
    noms_cat = {c["code"]: c["nom"] for c in CATEGORIES}
    lignes = []
    for section, cle, item, champs, masquable in _lignes_tarifs(_DEFAUTS):
        reg = (t.get(section) or {}).get(cle) or {}
        cat = cle.split(".")[0] if section in ("prestations", "packs") else ""
        nom = item.get("nom") or (f"{item['credits']} crédits" + (f" (+{item['bonus']} offerts)" if item.get("bonus") else "")
                                   if section == "packs_credits" else cle)
        lignes.append({"section": section, "cle": cle, "nom": nom, "categorie": noms_cat.get(cat, ""),
                       "champs": {c: {"origine": item.get(c), "valeur": reg.get(c, item.get(c))} for c in champs},
                       "masquable": masquable, "masque": cle in masques})
    return lignes
