"""
Zones de patch nommées (FAP / EGR / SCR / DTC / Stage / E85) + refus RSA.

Heuristique locale, sans Damos :
  - la forme (axe / table / scalaire) et le motif (mise à 0, 0xFF, forcé)
  - le type de la fiche (atomes Stage 1 + FAP + SCR…)
  - les blocs PKCS#1 / marqueurs RSA-CSA → zone *non appliquée*

Ce n'est pas une vérité cartographique : c'est une étiquette métier pour
lire le rapport. Une zone RSA n'est jamais patchée.
"""
from . import checksum as ckmod
from . import metadata

# ordre de priorité quand plusieurs contextes collent
_CONTEXT_PRIORITY = (
    "fap_off", "egr_off", "scr_off", "dtc_mask",
    "flexfuel", "torque", "limiter",
)


def detect_rsa_blocks(data):
    """Délègue au détecteur PKCS#1 / marqueurs de checksum.py."""
    return ckmod.detect_rsa(data)


def overlaps_rsa(off, length, blocks):
    if not blocks:
        return False
    a, b = off, off + length
    for blk in blocks:
        if blk.get("kind") == "marker":
            continue
        u, v = int(blk["off"]), int(blk["off"]) + int(blk["len"])
        if not (b <= u or a >= v):
            return True
    return False


def _contexts(fiche_type):
    atoms = metadata.type_atoms(fiche_type or "")
    blob = " ".join(atoms).lower() + " " + (fiche_type or "").lower()
    ctx = set()
    if any(k in blob for k in ("fap", "dpf", "particulate")):
        ctx.add("fap")
    if "egr" in blob:
        ctx.add("egr")
    if any(k in blob for k in ("scr", "adblue", "ad blue")):
        ctx.add("scr")
    if any(k in blob for k in ("dtc", "mil", "pcode", "voyant")):
        ctx.add("dtc")
    if any(k in blob for k in ("e85", "flex", "ethanol", "éthanol")):
        ctx.add("e85")
    if any(k in blob for k in ("stage", "stg", "puissance", "couple")):
        ctx.add("stage")
    return ctx


def _shape_fallback(feat, length):
    shape = feat.get("shape") or ""
    change = feat.get("change") or ""
    ch = {"zeroed": "mise à 0", "ff": "0xFF", "constant": "valeur forcée"}.get(change, "")
    if shape == "monotonic":
        code, word, conf = "axis", "axe (croissant)", "moyenne"
    elif shape == "constant":
        code, word, conf = "flag", "scalaire / drapeau", "moyenne"
    elif shape == "smooth":
        code, word, conf = "map", "table / cartographie", "moyenne"
    elif length <= 4:
        code, word, conf = "limiter", "scalaire / limiteur", "faible"
    else:
        code, word, conf = "data", "données", "faible"
    label = word + (" — " + ch if ch else "")
    return code, label, conf


def name_zone(feat, fiche_type, length, rsa=False):
    """Renvoie {code, label, confidence, rsa, apply}.

    `apply` est False uniquement pour une zone RSA (on ne touche pas la
    signature). Les autres zones restent applicables si elles sont compatibles.
    """
    if rsa:
        return {
            "code": "rsa",
            "label": "Signature RSA / CSA — non touchée",
            "confidence": "haute",
            "rsa": True,
            "apply": False,
        }

    ctx = _contexts(fiche_type)
    change = feat.get("change") or ""
    shape = feat.get("shape") or ""
    forced = change in ("zeroed", "ff", "constant")
    zeroish = change in ("zeroed", "ff")

    # FAP / DPF : grande table (ou scalaire) mise à 0 / FF
    if "fap" in ctx and zeroish and length >= 8:
        how = "mise à 0" if change == "zeroed" else "0xFF"
        return {
            "code": "fap_off",
            "label": f"FAP/DPF — table désactivée ({how})",
            "confidence": "haute",
            "rsa": False,
            "apply": True,
        }
    if "fap" in ctx and forced and length < 8:
        return {
            "code": "fap_off",
            "label": "FAP/DPF — drapeau / seuil forcé",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }

    # EGR
    if "egr" in ctx and zeroish and length >= 4:
        how = "mise à 0" if change == "zeroed" else "0xFF"
        return {
            "code": "egr_off",
            "label": f"EGR — désactivation ({how})",
            "confidence": "haute",
            "rsa": False,
            "apply": True,
        }
    if "egr" in ctx and change == "constant":
        return {
            "code": "egr_off",
            "label": "EGR — valeur forcée",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }

    # SCR / AdBlue
    if "scr" in ctx and zeroish and length >= 4:
        how = "mise à 0" if change == "zeroed" else "0xFF"
        return {
            "code": "scr_off",
            "label": f"AdBlue/SCR — désactivation ({how})",
            "confidence": "haute",
            "rsa": False,
            "apply": True,
        }
    if "scr" in ctx and change == "constant":
        return {
            "code": "scr_off",
            "label": "AdBlue/SCR — valeur forcée",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }

    # DTC / voyant — petites zones
    if "dtc" in ctx and length <= 32 and forced:
        how = {"zeroed": "mise à 0", "ff": "0xFF", "constant": "valeur forcée"}[change]
        return {
            "code": "dtc_mask",
            "label": f"DTC — masque / inhibition ({how})",
            "confidence": "haute",
            "rsa": False,
            "apply": True,
        }

    # Stage : petit scalaire = limiteur ; table lisse = couple / injection
    if "stage" in ctx and length <= 8 and shape in ("constant", "noisy") or (
            "stage" in ctx and length <= 4):
        return {
            "code": "limiter",
            "label": "Stage — limiteur",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }
    if "stage" in ctx and shape == "smooth" and length >= 16:
        return {
            "code": "torque",
            "label": "Stage — table couple / injection",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }
    if "stage" in ctx and shape == "monotonic":
        return {
            "code": "axis",
            "label": "Stage — axe (régime / charge)",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }

    # E85 / Flexfuel
    if "e85" in ctx and (shape == "smooth" or length >= 16):
        return {
            "code": "flexfuel",
            "label": "E85 — cartographie injection",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }
    if "e85" in ctx:
        return {
            "code": "flexfuel",
            "label": "E85 — correction Flexfuel",
            "confidence": "moyenne",
            "rsa": False,
            "apply": True,
        }

    code, label, conf = _shape_fallback(feat, length)
    ctx_note = []
    if "fap" in ctx:
        ctx_note.append("contexte FAP/DPF")
    if "egr" in ctx:
        ctx_note.append("contexte EGR")
    if "scr" in ctx:
        ctx_note.append("contexte AdBlue/SCR")
    if "stage" in ctx:
        ctx_note.append("contexte Stage")
    if ctx_note and " — " not in label:
        label = label + " · " + " · ".join(ctx_note)
    elif ctx_note:
        label = label + " · " + " · ".join(ctx_note)
    return {
        "code": code,
        "label": label,
        "confidence": conf,
        "rsa": False,
        "apply": True,
    }


def local_zone_label(feat, fiche_type, length, rsa=False):
    """Compat : l'ancienne chaîne unique."""
    return name_zone(feat, fiche_type, length, rsa=rsa)["label"]
