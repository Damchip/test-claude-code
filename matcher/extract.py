"""
Détection d'identifiants ECU — multi-familles.

Il n'existe pas de format universel : chaque fabricant place sa clé différemment.
Ce module combine plusieurs stratégies pour couvrir le maximum d'ECU :

  1. Bibliothèque de motifs typés par famille (Bosch, VAG, Continental, Marelli,
     Denso, BMW...). Chaque motif a un libellé, une famille et un poids.
  2. Détection du nom de plateforme (EDC17, MED17, SIMOS, PCR2.1, MJD, DCM...)
     sur l'ensemble du fichier — marqueur fiable qui donne aussi le fabricant.
  3. Filet générique : références alphanumériques isolées par des octets nuls.

Chaque candidat reçoit une confiance (0..1), avec un bonus si la chaîne est
isolée par des octets nuls (signature d'un vrai identifiant et non d'un
fragment de code/carto).

CALIBRATION : pour un format spécifique à tes fichiers, ajoute un motif dans
PART_PATTERNS, une plateforme dans PLATFORM_REGEX, ou utilise fixed_offsets().
"""

import re

MIN_STRING_LEN = 6          # longueur mini d'une chaîne ASCII candidate
NULL_BONUS = 0.15           # bonus de confiance si la chaîne est isolée par des \x00

# --- Motifs de référence/numéro, par famille ------------------------------
# (libellé, famille, regex, poids de base)
PART_PATTERNS = [
    ("Logiciel Valeo",      "Valeo",               re.compile(r"VX[4-9][0-9]_[A-Z0-9]_[0-9]{2}_[0-9]{2}(?:-[0-9A-Z]{1,3})?"), 0.8),
    ("Numéro Bosch (essence/diesel)", "Bosch",      re.compile(r"(?<!\d)02(?:61|81|80)(?:S\d{5}|\d{6})"),  0.8),
    ("Numéro Bosch (HW)",   "Bosch",               re.compile(r"\b0\s?\d{3}\s?\d{3}\s?\d{3}\b"),       0.78),
    ("Logiciel Bosch",      "Bosch",               re.compile(r"(?<!\d)103[0-9]\d{6}(?!\d)"),           0.72),
    ("Référence Ford",      "Ford",                re.compile(r"\b[0-9A-Z]{4}-12A650-[A-Z]{2,4}\b"),   0.72),
    ("Référence PSA",       "PSA",                 re.compile(r"(?<!\d)9[68]\d{6}80(?!\d)"),            0.66),
    ("Référence Renault",   "Renault",             re.compile(r"\b(?:2371\d{2}[0-9A-Z]{3,4}R|8200\d{6})\b"), 0.64),
    ("Référence VAG",       "VAG (VW/Audi/Seat)",  re.compile(r"\b(?=\w*[A-Z])[0-9][0-9A-Z][0-9A-Z]\s?\d{3}\s?\d{3}\s?[A-Z]{0,3}\b"), 0.62),
    ("Continental/Siemens", "Continental/Siemens", re.compile(r"(?<![A-Za-z0-9])(?:5WS4\w{4,8}|A[23]C\d{6,12}|S180\d{6})"), 0.72),
    ("Référence Marelli",   "Marelli",             re.compile(r"\b(?:55\d{6}|MM\dHW\w+)\b"),            0.55),
    ("Référence Delphi",    "Delphi",              re.compile(r"(?<!\d)28\d{6}(?!\d)"),                0.55),
    ("Denso/Toyota",        "Denso",               re.compile(r"\b\d{5}-\d{5}\b"),                      0.6),
    ("Mercedes",            "Mercedes",            re.compile(r"\bA\s?\d{3}\s?\d{3}\s?\d{2}\s?\d{2}\b"),  0.5),
    ("Logiciel Bosch (10SW)","Bosch",              re.compile(r"10SW\d{10,16}"),                       0.7),
    ("Référence générique", "indéterminée",        re.compile(r"\b(?=[A-Za-z0-9\-]{6,30}\b)(?=[A-Za-z0-9\-]*\d)(?=[A-Za-z0-9\-]*[A-Za-z])[A-Za-z0-9\-]{6,30}\b"), 0.32),
    ("Numéro générique",    "indéterminée",        re.compile(r"\b\d{6,12}\b"),                         0.28),
]

# --- Plateformes / familles d'ECU (scan sur tout le fichier) ---------------
PLATFORM_REGEX = re.compile(
    r"\b("
    r"EDC1[567][A-Z0-9.\-]{0,8}"          # Bosch diesel EDC15/16/17 + suffixes
    r"|EDC7[A-Z0-9.\-]{0,6}"
    r"|MEDC?(?:9|17|40)[A-Z0-9.\-]{0,8}"  # Bosch essence MED9/17/40
    r"|MEVD17[A-Z0-9.\-]{0,6}|MEV17[A-Z0-9.\-]{0,6}"
    r"|MEG?17\.[0-9](?:\.[0-9]{1,2}){0,2}"   # Bosch ME17.9.21 (Hyundai/Kia, moto), MEG17
    r"|ME[0-9](?:\.[0-9])?[A-Z0-9.]{0,4}"  # ME2.x / ME7 / ME9 (Bosch/Siemens)
    r"|MG1[A-Z0-9]{0,6}|MD1[A-Z0-9]{0,6}|MDG1[A-Z0-9]{0,5}"  # Bosch gen MG1/MD1
    r"|SIM266|SIM2[67][0-9]"               # Bosch Mercedes SIM266 / SIM27x
    r"|SIMOS[0-9.]{1,5}"                   # Continental SIMOS
    r"|SID[0-9]{3}EVO"                     # Continental SID807EVO
    r"|SID[23][0-9]{2}[A-Z]?"              # Continental/Siemens SID20x/30x
    r"|PCR2\.[0-9]"                        # Continental PCR2.1
    r"|SID[0-9]{2,3}[A-Z]?"               # Continental SID
    r"|SIRIUS\s?3[0-9]"                    # Siemens Sirius 32/34 (Renault)
    r"|PPD1\.[0-9]"                        # Siemens PPD1.x (VAG TDI pompe-injecteur)
    r"|VD[4-9][0-9]\.[0-9]{1,2}"           # Valeo VD46.1 / VD56.1 (PSA essence)
    r"|EMS[0-9]{3,4}"                      # Continental EMS
    r"|SIM2K-?[0-9]+"                      # Continental/Kefico SIM2K
    r"|DCM[0-9]\.[0-9][A-Z0-9]{0,3}"      # Delphi DCM
    r"|MJD[0-9][A-Z0-9.]{0,5}"            # Marelli Multijet Diesel
    r"|IAW[0-9][A-Z0-9.]{0,5}"            # Marelli essence
    r"|MT[0-9]{2}[A-Z]?"                   # Delphi MT20/MT38/MT80/MT86/MT92 (essence)
    r"|DDCR"                               # Delphi DDCR (diesel)
    r"|8GM[A-Z]"                           # Marelli 8GMF / 8GMK / 8GMW
    r"|MS[VD][0-9]{2}|MS4[0-9]"           # BMW MSV/MSD/MS43/45
    r"|DDE[0-9]?|DME[0-9]?"               # BMW diesel/essence
    r"|CRD[0-9][A-Z]?|CR6"                # Mercedes/Bosch CRD/CR6
    r"|TRIONIC\s?T?[0-9]"                  # Saab
    r"|KEIHIN"                             # Honda/moto Keihin
    r"|CPEGD[0-9.]{1,8}"                   # Kefico Hyundai/Kia
    r"|BEM[0-9]{3,4}"                      # Hitachi Renault
    r"|E6T[0-9]{2,4}"                      # Marelli IAW E6T
    r"|ADEM[45]"                           # Caterpillar ADEM
    r"|CM2[0-9]{3}[A-Z]?|CM8[0-9]{2}[A-Z]?"  # Cummins CM2150/2250/2350/2450, CM850/870/871
    r"|A[45]:?E2(?:V2)?"                   # Caterpillar/Perkins A4E2 / A5E2
    r"|A6E11"
    r")\b",
)
# Note : pas de IGNORECASE — les vraies plateformes sont en MAJUSCULES dans les
# fichiers ; sinon « dMe » (octets binaires) déclenche un faux « DME/BMW ».

# Préfixe de plateforme -> fabricant (premier match gagne)
PLATFORM_FAMILY = [
    ("EDC", "Bosch"), ("MED", "Bosch"), ("MEVD", "Bosch"), ("MEV", "Bosch"),
    ("ME7", "Bosch"), ("ME9", "Bosch"), ("ME17", "Bosch"), ("MEG17", "Bosch"), ("ME", "Bosch/Siemens"),
    ("MG1", "Bosch"), ("MD1", "Bosch"),
    ("MDG1", "Bosch"),
    ("SIM266", "Bosch"), ("SIM26", "Bosch"), ("SIM27", "Bosch"),
    ("SIMOS", "Continental/Siemens"), ("PCR2", "Continental/Siemens"),
    ("SID", "Continental/Siemens"), ("EMS", "Continental/Siemens"),
    ("SIRIUS", "Continental/Siemens"), ("PPD", "Continental/Siemens"),
    ("VD", "Valeo"),
    ("SIM2K", "Continental/Kefico"),
    ("DCM", "Delphi"),
    ("MJD", "Marelli"), ("IAW", "Marelli"), ("MT", "Delphi"), ("DDCR", "Delphi"),
    ("MSV", "Bosch/BMW"), ("MSD", "Bosch/BMW"), ("MS4", "Bosch/BMW"),
    ("DDE", "BMW"), ("DME", "BMW"),
    ("CRD", "Mercedes/Bosch"), ("CR6", "Mercedes/Bosch"),
    ("TRIONIC", "Saab"),
    ("KEIHIN", "Keihin"),
    ("CPEGD", "Kefico"),
    ("BEM", "Hitachi"),
    ("E6T", "Marelli"),
    ("ADEM", "Caterpillar"),
    ("CM2", "Cummins"), ("CM8", "Cummins"),
    ("A4E", "Caterpillar/Perkins"), ("A5E", "Caterpillar/Perkins"), ("A4:E", "Caterpillar/Perkins"),
    ("A5:E", "Caterpillar/Perkins"),
    ("A6E", "VM Motori"),
    ("8GM", "Marelli"),
]


# Signatures fiables lues dans les VRAIES chaînes (pas le binaire brut).
# Ordre = priorité : un fichier Denso peut aussi contenir "Hitachi" (le fondeur
# de la puce) ; Denso doit gagner.
VENDOR_SIGS = [
    (re.compile(r"DENSO", re.I), "Denso"),
    (re.compile(r"MAGNETI|MARELLI", re.I), "Marelli"),
    (re.compile(r"KEFICO", re.I), "Kefico"),
    (re.compile(r"AC[\s\-]?DELCO", re.I), "ACDelco"),
    (re.compile(r"DELPHI", re.I), "Delphi"),
    (re.compile(r"SIEMENS|\bVDO\b|CONTINENTAL|\bCONTI|TEMIC|\bSID[23]\d{2}\b", re.I), "Continental/Siemens"),
    (re.compile(r"\bVALEO\b", re.I), "Valeo"),
    (re.compile(r"\bCATERPILLAR\b|\bPERKINS\b", re.I), "Caterpillar/Perkins"),
    (re.compile(r"BOSCH", re.I), "Bosch"),
    (re.compile(r"CUMMINS", re.I), "Cummins"),
    (re.compile(r"KEIHIN", re.I), "Keihin"),
    (re.compile(r"MITSUBISHI", re.I), "Mitsubishi"),
    (re.compile(r"HITACHI", re.I), "Hitachi"),
]
# Cœurs / microcontrôleurs courants (Renesas/Hitachi SH, Freescale, Infineon…)
CORE_SIGS = [
    (re.compile(r"\bSH(70\d{2})", re.I), "SH"),             # SH7055, SH705513N…
    (re.compile(r"\bHo(70\d{2})"), "SH"),                   # "Ho7058"
    (re.compile(r"\b(70\d{2})\s+Operating System", re.I), "SH"),
    (re.compile(r"\b(MPC5\d{2}\w*)\b", re.I), ""),                 # Freescale PowerPC
    (re.compile(r"\b(TC1[789]\d{2}\w*)\b", re.I), ""),             # Infineon TriCore
]


def detect_signatures(data: bytes):
    """Fabricant et cœur ECU déduits des chaînes ASCII réelles. (None, None) sinon."""
    runs = ascii_runs(data)
    found = set()
    core = None
    hitachi_calib = has_database = False
    mcm = None
    for r in runs:
        s = r["s"]
        for pat, name in VENDOR_SIGS:
            if pat.search(s):
                found.add(name)
        # signature Hitachi : ID de calibration SH705xxN + bloc "DATABASE"
        if re.search(r"\bSH70\d{4}[A-Z]\b", s):
            hitachi_calib = True
        if "DATABASE" in s:
            has_database = True
        # module MCM (Mercedes/Detroit Motor Control Module) — souvent en minuscules
        if mcm is None:
            mm = re.search(r"[Mm][Cc][Mm]([0-9])", s)
            if mm:
                mcm = "MCM" + mm.group(1)
        if core is None:
            for pat, pre in CORE_SIGS:
                m = pat.search(s)
                if m:
                    core = (pre + m.group(1)) if pre else m.group(1)
                    break
    if hitachi_calib and has_database:
        found.add("Hitachi")
    manuf = next((name for _, name in VENDOR_SIGS if name in found), None)
    if mcm:
        core = core or mcm                       # MCM sert de plateforme
        manuf = manuf or "Mercedes/Detroit"      # sauf signature fabricant explicite
    return manuf, core


def family_for_platform(platform: str):
    p = (platform or "").upper().replace(" ", "")
    for prefix, fam in PLATFORM_FAMILY:
        if p.startswith(prefix):
            return fam
    return None


def ascii_runs(data: bytes, min_len: int = MIN_STRING_LEN):
    """Chaînes ASCII imprimables avec position et indicateur d'isolation \\x00."""
    runs, start, cur = [], 0, bytearray()
    n = len(data)
    for i in range(n + 1):
        b = data[i] if i < n else -1
        if 32 <= b <= 126:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= min_len:
                end = start + len(cur)
                null_left = (start == 0) or (data[start - 1] == 0)
                null_right = (end >= n) or (data[end] == 0)
                runs.append({
                    "s": cur.decode("ascii", "ignore"),
                    "start": start,
                    "null_bounded": null_left and null_right,
                })
            cur = bytearray()
    return runs


# Bloc d'identification Bosch (ME17, MED17, EDC17, MD1, MG1…) : « 39/1/ME17_9_20/15/P_1220//r1780… »,
# « 34/1/EDC17C46/3/P1135// » — le nom exact du calculateur, avec « _ » à la place des points.
BOSCH_IDENT = re.compile(r"(?<![0-9])[0-9]{1,3}/1/([A-Z]{2,5}[0-9][A-Z0-9_.]{0,15})/[0-9]{1,3}/")
# Identifiant logiciel Valeo (PSA PureTech) : « VX56_L_29_07-6M » → calculateur VD56
VALEO_IDENT = re.compile(r"(?<![A-Za-z0-9])VX([4-9][0-9])_[A-Z0-9]_[0-9]{2}_[0-9]{2}")
# Ligne générique présente dans tous les Bosch TriCore (« ME(D)/EDC17 SB_V18.00.02/1782 ») : ne désigne pas le modèle
_BOSCH_SOCLE = "ME(D)/"


def _dans_une_chaine(text, debut, fin, mini=4):
    """La plateforme fait-elle partie d'un vrai texte (≥ `mini` caractères imprimables) et non de 3 octets de code ?"""
    g, d = debut, fin
    while g > 0 and 32 <= ord(text[g - 1]) <= 126:
        g -= 1
    while d < len(text) and 32 <= ord(text[d]) <= 126:
        d += 1
    return d - g >= mini


def detect_platform(data: bytes):
    """Renvoie (plateforme, fabricant) le plus probable, ou (None, None)."""
    try:
        brut = data.decode("latin-1")
    except Exception:
        return None, None
    ident = {}
    for m in BOSCH_IDENT.finditer(brut):
        v = m.group(1).replace("_", ".").strip(".")
        if family_for_platform(v):
            ident[v] = ident.get(v, 0) + 1
    if ident:
        best = max(ident.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        return best, family_for_platform(best)
    m = VALEO_IDENT.search(brut)
    if m:
        return "VD" + m.group(1), "Valeo"
    # le « _ » est un caractère de mot et casse \b (ex. CONTI_SID209) → espace
    text = brut.replace("_", " ")
    hits = {}
    for m in PLATFORM_REGEX.finditer(text):
        if text[max(0, m.start() - len(_BOSCH_SOCLE)):m.start()] == _BOSCH_SOCLE:
            continue
        if not _dans_une_chaine(text, m.start(), m.end()):
            continue
        v = m.group(0).strip().rstrip(".-").replace(" ", "")
        hits[v] = hits.get(v, 0) + 1
    if not hits:
        return None, None
    # priorité au plus long puis au plus fréquent (plus spécifique)
    best = sorted(hits.items(), key=lambda kv: (len(kv[0]), kv[1]), reverse=True)[0][0]
    return best, family_for_platform(best)


_NOISE_RUN = re.compile(r"([^0])\1{3,}|(..)\2{3,}")  # 4+ car. non nuls, ou motif de 2 car. répété 4×+


def _is_sequential(v: str) -> bool:
    """Vrai pour une suite strictement croissante/décroissante (0123456789…)."""
    if len(v) < 6:
        return False
    diffs = {ord(v[i + 1]) - ord(v[i]) for i in range(len(v) - 1)}
    return diffs == {1} or diffs == {-1}


def _looks_like_noise(v: str) -> bool:
    """Vrai pour le bruit de carto (répétitions de motif, trop peu de variété,
    ou suite séquentielle). Les zéros sont épargnés (vrais suffixes ex. EEM4-0000)."""
    return bool(_NOISE_RUN.search(v)) or len(set(v)) < 4 or _is_sequential(v)


def detect_candidates(data: bytes):
    """Liste de candidats typés et notés, triés par confiance décroissante."""
    runs = ascii_runs(data)
    by_value = {}  # value -> {value, type, family, confidence}

    for run in runs:
        s, nb = run["s"], run["null_bounded"]
        # match plein (toute la chaîne) prioritaire, sinon recherche dans chaîne courte
        for label, family, pat, weight in PART_PATTERNS:
            value = None
            if pat.fullmatch(s):
                value = s
            elif family != "indéterminée" or len(s) <= 40:
                # motifs spécifiques (Bosch, Continental…) : recherche partout ;
                # filet générique : seulement dans les chaînes courtes (anti-bruit)
                m = pat.search(s)
                if m:
                    value = m.group(0)
            if not value:
                continue
            # anti-bruit : un motif de remplissage (0888888888, 55555555…) ne doit
            # jamais être pris pour un vrai numéro, quelle que soit la famille
            if _looks_like_noise(value):
                continue
            # le filet générique non isolé exige en plus une référence longue
            if family == "indéterminée" and not nb and len(value) < 12:
                continue
            # les vraies références sont en MAJUSCULES et chiffres ; « 2dXRMHD », « tH4FyG », « d-5Gac » = code
            if family == "indéterminée" and any(ch.islower() for ch in value):
                continue
            conf = weight + (NULL_BONUS if nb else 0.0)
            prev = by_value.get(value)
            if prev is None or conf > prev["confidence"]:
                by_value[value] = {
                    "value": value, "type": label,
                    "family": family, "confidence": round(min(conf, 0.99), 2),
                }
            break  # un motif suffit par chaîne

    return sorted(by_value.values(), key=lambda c: c["confidence"], reverse=True)


def pick_ecu_version(candidates, min_conf=0.55):
    """Premier identifiant assez sûr (pas un numéro générique flou)."""
    for c in candidates or []:
        v = (c.get("value") or "").strip()
        if not v or v in {"0", "0000000000", "FFFFFFFF"}:
            continue
        fam = c.get("family") or ""
        conf = float(c.get("confidence") or 0)
        if fam == "indéterminée" and conf < 0.7:
            continue
        if conf < min_conf:
            continue
        return v
    return ""


def extract(data: bytes) -> dict:
    runs = ascii_runs(data)
    candidates = detect_candidates(data)
    platform, manufacturer = detect_platform(data)
    sig_manuf, sig_core = detect_signatures(data)
    # le cœur (SH7058…) sert de plateforme si aucune plateforme franche détectée
    if sig_core and not platform:
        platform = sig_core
    # une signature fabricant fiable (DENSO…) prime sur la déduction par plateforme
    if sig_manuf:
        manufacturer = sig_manuf
    if manufacturer is None and candidates:
        manufacturer = next((c["family"] for c in candidates
                             if c["family"] != "indéterminée"), None)
    if manufacturer == "Mercedes/Detroit":
        # Sur un module MCM confirmé, un numéro à 10 chiffres qui ressemble à un
        # « Bosch (HW) » est en réalité un numéro de pièce Mercedes (00xxxxxxxx,
        # le pendant sans le préfixe A des références A0xxxxxxxx) : Detroit/MCM
        # n'utilise pas de calculateur Bosch, donc jamais de vrai numéro Bosch ici.
        for c in candidates:
            if c["type"] == "Numéro Bosch (HW)":
                c["type"] = "Numéro Mercedes (pièce)"
                c["family"] = "Mercedes/Detroit"
    return {
        "platform": platform,
        "manufacturer": manufacturer,
        "typed_candidates": candidates[:12],
        "candidate_ids": [c["value"] for c in candidates[:12]],
        # identifiant le plus sûr ; jamais un numéro générique flou (fragment de table) faute de mieux
        "best_ecu_version": pick_ecu_version(candidates),
        "strings_count": len(runs),
    }


def fixed_offsets(data: bytes, offsets: dict) -> dict:
    """Lecture à des offsets fixes connus : {"ecu_version": (start, length), ...}."""
    out = {}
    for name, (start, length) in offsets.items():
        out[name] = data[start:start + length].decode("ascii", "ignore").strip("\x00 ").strip()
    return out
