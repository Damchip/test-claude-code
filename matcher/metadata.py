"""
Métadonnées issues du NOM de fichier et du CHEMIN (dossier parent).

Très utile : ces infos sont saisies par un humain et contiennent souvent la
marque, le véhicule, la plateforme ECU et le type de solution — choses qui ne
sont presque jamais lisibles dans le binaire. On les traite comme des indices
à fort poids, recoupés ensuite avec la détection binaire.
"""

import os
import re

from . import extract

# Marques courantes (et alias) -> libellé normalisé
BRANDS = {
    "audi": "Audi", "vw": "Volkswagen", "volkswagen": "Volkswagen",
    "seat": "Seat", "skoda": "Skoda", "porsche": "Porsche",
    "bmw": "BMW", "mini": "Mini",
    "mercedes": "Mercedes", "mercedes-benz": "Mercedes", "merco": "Mercedes",
    "renault": "Renault", "dacia": "Dacia", "nissan": "Nissan",
    "peugeot": "Peugeot", "citroen": "Citroën", "citroën": "Citroën",
    "ds": "DS", "opel": "Opel", "vauxhall": "Opel",
    "ford": "Ford", "fiat": "Fiat", "alfa": "Alfa Romeo", "lancia": "Lancia",
    "jeep": "Jeep", "iveco": "Iveco",
    "volvo": "Volvo", "toyota": "Toyota", "lexus": "Lexus", "mazda": "Mazda",
    "honda": "Honda", "hyundai": "Hyundai", "kia": "Kia",
    "landrover": "Land Rover", "jaguar": "Jaguar",
    "mitsubishi": "Mitsubishi", "subaru": "Subaru", "suzuki": "Suzuki",
    # agricole / PL / engins
    "claas": "Claas", "lexion": "Claas",
    "fendt": "Fendt", "massey": "Massey Ferguson",
    "masseyferguson": "Massey Ferguson", "deutz": "Deutz", "kubota": "Kubota",
    "yanmar": "Yanmar", "valtra": "Valtra", "same": "SAME",
    "johndeere": "John Deere", "deere": "John Deere", "jd": "John Deere",
    "newholland": "New Holland", "caseih": "Case IH", "case": "Case",
    "scania": "Scania", "man": "MAN", "daf": "DAF",
    "komatsu": "Komatsu", "jcb": "JCB", "hitachi": "Hitachi",
    "isuzu": "Isuzu", "hino": "Hino", "detroit": "Detroit",
    "liebherr": "Liebherr", "dieci": "Dieci", "pegasus": "Dieci",
    "caterpillar": "Caterpillar", "catet": "Caterpillar",
}

# Marques agri/PL : si présentes, elles gagnent contre une marque auto homonyme
# (Claas Jaguar 860 n'est pas une Jaguar voiture).
AGRI_BRANDS = {
    "Claas", "Fendt", "Massey Ferguson", "Deutz", "Kubota", "Yanmar",
    "Valtra", "SAME", "John Deere", "New Holland", "Case IH", "Case",
    "Scania", "MAN", "DAF", "Komatsu", "JCB", "Liebherr", "Dieci",
    "Caterpillar",
}

# Mots-clés de solution -> libellé normalisé
# (ordre = priorité : le premier match gagne pour ce libellé, tous sont cumulés)
SOLUTION_KEYWORDS = [
    (r"stage\s?-?2|(?<![a-z0-9])st2(?![a-z0-9])", "Stage 2"),
    (r"stage\s?-?1|(?<![a-z0-9])st1(?![a-z0-9])", "Stage 1"),
    (r"(?<![a-z0-9])e85(?![a-z0-9])|flex[\s\-]?fuel|flexfuel|bio[\s\-]?etha|(?<![a-z0-9])ethanol(?![a-z0-9])|(?<![a-z0-9])éthanol(?![a-z0-9])", "E85 / Flexfuel"),
    (r"(?<![a-z0-9])(?:dpf|fap)(?![a-z0-9])|dpfoff|fapoff|dpf[\s_\-]?off|fap[\s_\-]?off", "DPF/FAP off"),
    (r"(?<![a-z0-9])egr(?![a-z0-9])|egroff|egr[\s_\-]?off", "EGR off"),
    (r"(?<![a-z0-9])(?:adblue|scr|nox)(?![a-z0-9])|scroff|scr[\s_\-]?off", "AdBlue/SCR off"),
    (r"(?<![a-z0-9])dtc(?![a-z0-9])|dtcoff|dtc[\s_\-]?off", "DTC off"),
    (r"decat|cat[\s_\-]?off|catalyst", "Decat"),
    (r"pop.?(?:and|&|n).?bang|crackle", "Pop & Bang"),
    (r"(?<![a-z0-9])vmax(?![a-z0-9])|speed\s?limit|(?<![a-z0-9])svl(?![a-z0-9])", "Vmax off"),
    (r"hardcut|launch", "Launch / Hardcut"),
    (r"(?<![a-z0-9])(?:tva|gearbox|tcu|dsg)(?![a-z0-9])", "Boîte / TCU"),
    (r"(?<![a-z0-9])(?:stock|original|stockfile|ori)(?![a-z0-9])", "Origine (stock)"),
]

# Atomes de presta (ordre d'affichage devis / portail)
TYPE_ATOMS_ORDER = [
    "E85 / Flexfuel", "Stage 2", "Stage 1",
    "DPF/FAP off", "EGR off", "AdBlue/SCR off", "DTC off",
    "Decat", "Pop & Bang", "Vmax off", "Launch / Hardcut", "Boîte / TCU",
    "Origine (stock)",
]

TYPE_ALIASES = {
    "e85": "E85 / Flexfuel", "flexfuel": "E85 / Flexfuel",
    "flex fuel": "E85 / Flexfuel", "e85 / flexfuel": "E85 / Flexfuel",
    "st1": "Stage 1", "stage1": "Stage 1",
    "st2": "Stage 2", "stage2": "Stage 2",
    "dpf off": "DPF/FAP off", "fap off": "DPF/FAP off",
    "dpf": "DPF/FAP off", "fap": "DPF/FAP off",
    "dpf/fap off": "DPF/FAP off",
    "egr off": "EGR off", "egr": "EGR off",
    "adblue off": "AdBlue/SCR off", "scr off": "AdBlue/SCR off",
    "adblue": "AdBlue/SCR off", "scr": "AdBlue/SCR off",
    "adblue/scr off": "AdBlue/SCR off",
    "dtc off": "DTC off", "dtc": "DTC off",
}


def type_atoms(text: str):
    """Découpe 'Stage 1 + DPF/FAP off' en atomes normalisés."""
    out, seen = [], set()
    for part in (text or "").split(" + "):
        a = canonical_atom(part)
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def canonical_atom(text: str):
    s = (text or "").strip()
    if not s:
        return ""
    return TYPE_ALIASES.get(s.lower(), s)


def ordered_atoms(types):
    """Atomes uniques, Origine retiré s'il y a une vraie presta, tri canonique."""
    seen, atoms = set(), []
    for t in types or []:
        for a in type_atoms(t):
            if a not in seen:
                seen.add(a)
                atoms.append(a)
    if any(a != "Origine (stock)" for a in atoms):
        atoms = [a for a in atoms if a != "Origine (stock)"]
    order = {n: i for i, n in enumerate(TYPE_ATOMS_ORDER)}
    atoms.sort(key=lambda x: (order.get(x, 99), x.lower()))
    return atoms

# Plateformes lues dans un CHEMIN (insensible à la casse — contrairement au
# binaire, où « dMe » est du bruit). Plus spécifique d'abord.
PATH_PLATFORMS = [
    re.compile(r"\b(EDC17CV\d{2}[A-Z0-9]*)\b", re.I),
    re.compile(r"\b(EDC17CP\d{2}[A-Z0-9]*)\b", re.I),
    re.compile(r"\b(EDC17C\d{2}[A-Z0-9]*)\b", re.I),
    re.compile(r"\b(EDC16(?:C\d{2})?)\b", re.I),
    re.compile(r"\b(EDC7UC\d{2})\b", re.I),
    re.compile(r"\b(MD1[A-Z]{2}\d{3}[A-Z0-9]*)\b", re.I),
    re.compile(r"\b(MG1[A-Z]{2}\d{3}[A-Z0-9]*)\b", re.I),
    re.compile(r"\b(ADEM\s?[45])\b", re.I),
    re.compile(r"\b(CM2\d{3}[A-Z]?|CM8\d{2}[A-Z]?)\b", re.I),
    re.compile(r"\b(VD[4-9]\d\.\d{1,2})", re.I),
    re.compile(r"\b(SID\d{3}EVO)\b", re.I),
    re.compile(r"\b(MEG?17\.\d(?:\.\d{1,2}){0,2})", re.I),
    re.compile(r"\b(A[45]:?E2)\b", re.I),
    re.compile(r"\b(A6E11)\b", re.I),
    re.compile(r"\b(CPEGD\d(?:\.\d+)+)\b", re.I),
    re.compile(r"\b(BEM\d{3,4})\b", re.I),
    re.compile(r"\b(E6T\d{2,4})\b", re.I),
    re.compile(r"\b(8GM[A-Z])\b", re.I),
    re.compile(r"\b(PCR2\.[0-9])\b", re.I),
    re.compile(r"\b(SID\d{3}[A-Z]?)\b", re.I),
    re.compile(r"\b(CRD\d[A-Z]?)\b", re.I),
    re.compile(r"\b(DCM\d\.\d[A-Z0-9]*)\b", re.I),
    re.compile(r"\b(SIM266|SIM27\d)\b", re.I),
    re.compile(r"\b(DENSO)\b", re.I),
    re.compile(r"\b(MCM\d?)\b", re.I),
    re.compile(r"\b(ACM)\b", re.I),
]

# Bruit binaire souvent stocké comme « plateforme » dans les anciennes bases.
_GARBAGE_PLAT = re.compile(
    r"^(dme|dde|me0m|me|dme0|ms)$", re.I)


def compose_label(brand, vehicle):
    """Assemble marque + véhicule sans répéter la marque si déjà présente."""
    brand = (brand or "").strip()
    vehicle = (vehicle or "").strip()
    if brand and vehicle:
        if vehicle.lower().startswith(brand.lower()):
            return vehicle
        return f"{brand} {vehicle}"
    return brand or vehicle or ""


def _normalize_tokens(text: str):
    return re.split(r"[\s_\-.()\[\]/\\+]+", text.lower())


def is_garbage_platform(p: str) -> bool:
    s = (p or "").strip()
    if not s:
        return True
    if _GARBAGE_PLAT.fullmatch(s):
        return True
    # casse mélangee 2-4 lettres (dMe, DmE) = octets binaires, pas un nom d'ECU
    if 2 <= len(s) <= 4 and s.isalpha() and not (s.isupper() or s.islower() or s.istitle()):
        return True
    return False


def normalize_platform(p: str):
    """Nettoie un nom de plateforme (suffixes WinOLS, bruit binaire)."""
    if is_garbage_platform(p):
        return None
    s = (p or "").strip().upper().replace(" ", "")
    s = re.sub(r"-EP-?$", "", s)
    s = re.sub(r"\.A000$", "", s)
    s = re.sub(r"C\.\d+$", "", s)
    s = s.strip("-.")
    if s in {"ADEM4", "ADEM5"}:
        return s
    if s == "DENSO":
        return "Denso"
    return s or None


def _path_platform(text: str):
    """Plateforme lue dans un chemin / nom de dossier (insensible à la casse)."""
    if not text:
        return None
    t = text.replace("\\", "/").replace("_", " ")
    # ACM/MCM : le dernier dossier gagne (…/ACM/fichier vs …/MCM/fichier)
    low = t.lower()
    if "/mcm/" in low or low.rstrip("/").endswith("/mcm"):
        return "MCM"
    if "/acm/" in low or low.rstrip("/").endswith("/acm"):
        return "ACM"
    for pat in PATH_PLATFORMS:
        m = pat.search(t)
        if m:
            return normalize_platform(m.group(1).replace(" ", ""))
    return None


def _prefer_platform(*cands):
    best = None
    for c in cands:
        n = normalize_platform(c) if c else None
        if not n:
            continue
        if best is None or (n.upper().startswith(best.upper()) and len(n) > len(best)):
            best = n
        elif best.upper().startswith(n.upper()) and len(best) > len(n):
            continue
        elif len(n) > len(best):
            best = n
    return best


def _pick_brand(tokens):
    found = []
    seen = set()
    pairs = [tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1)]
    for t in list(tokens) + pairs:
        b = BRANDS.get(t)
        if b and b not in seen:
            found.append(b)
            seen.add(b)
    if not found:
        return None
    agri = [b for b in found if b in AGRI_BRANDS]
    return agri[0] if agri else found[0]


def _neutralize_workspace(text: str) -> str:
    """Retire le dossier OneDrive « E85 » (société), pas le nom du fichier solution."""
    t = (text or "").replace("\\", "/")
    t = re.sub(r"(?i)onedrive\s+e85", " ", t)
    t = re.sub(r"(?i)/bio[\s\-]?e85/", "/", t)
    return t


_MOD_TYPES = {"DPF/FAP off", "EGR off", "AdBlue/SCR off", "Stage 1", "Stage 2", "DTC off"}


def _solution_types(text: str):
    low = _neutralize_workspace(text).lower()
    sols = []
    for pat, label in SOLUTION_KEYWORDS:
        if re.search(pat, low) and label not in sols:
            sols.append(label)
    # E85France / 19% dans le NOM DE FICHIER = vraie presta E85,
    # sauf si le fichier est déjà un FAP/EGR/SCR/Stage (tampon société sur du PL).
    bases = " ".join(os.path.basename(p.replace("\\", "/"))
                     for p in re.split(r"[|]", text or "") if p).lower()
    has_mod = any(s in sols for s in _MOD_TYPES)
    if not has_mod:
        if re.search(r"e85france", bases) or re.search(r"(?<!\d)(?:1[5-9]|2[0-9]|30)\s*%", bases):
            if "E85 / Flexfuel" not in sols:
                sols.append("E85 / Flexfuel")
        elif re.search(r"[/\\]reference[/\\]", low) and sols == []:
            sols.append("E85 / Flexfuel")
    if any(s != "Origine (stock)" for s in sols):
        sols = [s for s in sols if s != "Origine (stock)"]
    if "Decat" in sols and not re.search(r"decat|cat[\s_\-]?off|catalyst", low):
        sols = [s for s in sols if s != "Decat"]
    return sols


def parse(path: str) -> dict:
    """Analyse un nom de fichier et/ou un chemin (dossiers parents inclus)."""
    if not path:
        return _empty()

    name = os.path.basename(path.replace("\\", "/"))
    full = path.replace("\\", "/")
    tokens = _normalize_tokens(full)
    brand = _pick_brand(tokens)

    sols = _solution_types(full)
    solution_type = " + ".join(sols) if sols else None

    det_text = re.sub(r"[_\-]+", " ", full)
    data = det_text.encode("latin-1", "ignore")
    bin_plat, manufacturer = extract.detect_platform(data)
    path_plat = _path_platform(full)
    platform = _prefer_platform(path_plat, bin_plat)
    if platform:
        fam = extract.family_for_platform(platform)
        if platform in {"ADEM4", "ADEM5"}:
            manufacturer = "Caterpillar"
        elif platform in {"ACM", "MCM"}:
            manufacturer = "Mercedes/Detroit"
        elif (platform or "").upper().startswith(("CM2", "CM8")):
            manufacturer = "Cummins"
        elif (platform or "").lower() == "denso":
            manufacturer = "Denso"
        elif fam:
            manufacturer = fam

    ids = [c["value"] for c in extract.detect_candidates(data)]
    vehicle = _vehicle_label(full, name, platform, sols, brand)

    return {
        "source_name": name,
        "brand": brand,
        "vehicle": vehicle,
        "solution_type": solution_type,
        "platform": platform,
        "manufacturer": manufacturer,
        "ids": ids,
    }


def parse_record(original="", solution="", label=""):
    """Fusionne original + solution + libellé (chacun parsé comme un vrai chemin)."""
    m_ori = parse(original or "")
    m_sol = parse(solution or "")
    types = []
    for src in (
        (m_ori.get("solution_type") or "").split(" + "),
        (m_sol.get("solution_type") or "").split(" + "),
        _solution_types(label or ""),
    ):
        for t in src:
            t = (t or "").strip()
            if t and t not in types:
                types.append(t)
    if any(t != "Origine (stock)" for t in types):
        types = [t for t in types if t != "Origine (stock)"]
    brand = _pick_brand(_normalize_tokens(
        " ".join(p for p in (original, solution, label) if p)))
    platform = _prefer_platform(m_sol.get("platform"), m_ori.get("platform"))
    manufacturer = None
    if platform:
        fam = extract.family_for_platform(platform)
        if platform in {"ADEM4", "ADEM5"}:
            manufacturer = "Caterpillar"
        elif platform in {"ACM", "MCM"}:
            manufacturer = "Mercedes/Detroit"
        elif (platform or "").upper().startswith(("CM2", "CM8")):
            manufacturer = "Cummins"
        elif (platform or "").lower() == "denso":
            manufacturer = "Denso"
        elif fam:
            manufacturer = fam
    manufacturer = manufacturer or m_sol.get("manufacturer") or m_ori.get("manufacturer")
    vehicle = m_ori.get("vehicle") or m_sol.get("vehicle")
    if brand and vehicle:
        vehicle = compose_label(brand, vehicle)
    return {
        "source_name": m_ori.get("source_name") or m_sol.get("source_name"),
        "brand": brand,
        "vehicle": vehicle,
        "solution_type": " + ".join(types) if types else None,
        "platform": platform,
        "manufacturer": manufacturer,
        "ids": (m_ori.get("ids") or []) + (m_sol.get("ids") or []),
    }


def _vehicle_label(full, name, platform, sols, brand):
    # dossier parent = souvent le vrai libellé de job
    parent = os.path.basename(os.path.dirname(full.replace("\\", "/")))
    base_src = parent if parent and parent.lower() not in {
        "acm", "mcm", "adem", "ori", "stock", "files", "cartos", "poids lourds",
        "backup",
    } else name
    base = re.sub(r"(\.[A-Za-z0-9]{1,5})+$", "", base_src)
    base = re.sub(r"[_\-]+", " ", base)
    if platform:
        base = re.sub(re.escape(platform), "", base, flags=re.I)
    for pat, _ in SOLUTION_KEYWORDS:
        base = re.sub(pat, "", base, flags=re.I)
    base = re.sub(
        r"\b(off|on|tun|mod|file|read|virgin|lecture|relecture|dc|motors|"
        r"octane|winols|e85france|sav\s?\d+|nv|scr|egr|dpf|fap|stage\s?[12]|"
        r"cod|mpc|bin)\b",
        "", base, flags=re.I)
    base = re.sub(r"\s{2,}", " ", base).strip(" -_.")
    return compose_label(brand, base) or base or None


def _empty():
    return {"source_name": None, "brand": None, "vehicle": None,
            "solution_type": None, "platform": None, "manufacturer": None, "ids": []}
