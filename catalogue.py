"""
Catalogue du fileservice : prestations, packs, options et packs de crédits.

Repris de la boutique WooCommerce (hors « LA BOUTIQUE », brouillons et produits
privés). Les prix de prestation sont en CRÉDITS ; 1 crédit = 2,50 €.

Le client coche des prestations ; `devis()` choisit la combinaison la moins
chère de packs et de prestations seules qui couvre exactement la sélection :
le tarif pack s'applique tout seul.
"""

PRIX_CREDIT_EUR = 2.50   # HT
TVA = 0.20               # prix affichés HT et TTC

CATEGORIES = [
    {"code": "vl", "nom": "Véhicule léger"},
    {"code": "pl", "nom": "Agricole / PL"},
    {"code": "moto", "nom": "Moto / Quad"},
]

# prix_siege : prix quand le calculateur est envoyé au siège pour ouverture du boîtier
PRESTATIONS = {
    "vl": [
        {"code": "stage1", "nom": "Stage 1", "desc": "Fichier sur mesure", "prix": 59},
        {"code": "e85", "nom": "Adaptation E85", "desc": "Fichier sur mesure bioéthanol", "prix": 59, "prix_siege": 89},
        {"code": "speed", "nom": "Speed limit", "desc": "Réglage du limiteur de vitesse", "prix": 29},
        {"code": "startstop", "nom": "Start & Stop", "desc": "Réglage du Start & Stop", "prix": 29},
        {"code": "dtc", "nom": "Suppression DTC", "desc": "Codes défaut à préciser en commentaire", "prix": 29},
        {"code": "immo", "nom": "Réglage IMMO", "desc": "Antidémarrage", "prix": 59},
        {"code": "volet_adm", "nom": "Volet collecteur d'admission", "desc": "Réglage du volet d'admission", "prix": 39},
        {"code": "volet_ech", "nom": "Volet d'échappement", "desc": "Réglage du volet d'échappement", "prix": 39},
        {"code": "torque", "nom": "Torque OFF", "desc": "Limitation de couple", "prix": 19},
    ],
    "pl": [
        {"code": "stage1", "nom": "Stage 1 – PL", "desc": "Fichier sur mesure", "prix": 79},
        {"code": "dtc", "nom": "Suppression DTC – PL", "desc": "Codes défaut à préciser en commentaire", "prix": 59},
        {"code": "immo", "nom": "Réglage IMMO – PL", "desc": "Antidémarrage", "prix": 59},
    ],
    "moto": [
        {"code": "stage1", "nom": "Stage 1 – Moto", "desc": "Fichier sur mesure", "prix": 59},
        {"code": "speed", "nom": "Speed limit – Moto/Quad", "desc": "Réglage du limiteur de vitesse", "prix": 20},
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


def devis(categorie, codes, siege=False, garantie=None):
    """Tarif le plus avantageux pour la sélection.

    Retourne {"lignes": [{"nom", "credits"}], "total", "economie", "siege_possible", "erreur"}.
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
    g = next((g for g in GARANTIES if g["code"] == garantie), None)
    if g and choix:
        lignes.append({"nom": g["nom"], "credits": g["prix"]})
        total += g["prix"]
    return {"lignes": lignes, "total": total, "economie": somme_seules - best[full][0],
            "siege_possible": siege_possible, "erreur": ""}
