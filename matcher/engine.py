"""
Moteur de matching — tourne 100% en local.

Pipeline pour un fichier client entrant :
  1. Empreinte (sha256 + taille + MinHash) et extraction des identifiants.
  2. Match exact fichier   : sha256 == stock_sha256 d'une solution.
  3. Match exact calibration : un identifiant du fichier == ecu_version en base.
  4. Similarité binaire     : Jaccard(MinHash) au-dessus d'un seuil,
     uniquement si les tailles sont quasi identiques (veto ±2 %).
  5. Recoupement d'identifiants : égalité exacte uniquement (plus de sous-chaîne).
  6. Plateforme / libellé : indices faibles, jamais suffisants pour un verdict client.

Chaque solution candidate reçoit un score (0..1) et une raison lisible.
Aucune donnée ne sort de la machine à cette étape.
"""

from . import db, extract, fingerprint, headers, metadata

FUZZY_THRESHOLD = 0.45   # en-dessous, on n'affiche pas la similarité binaire
SIZE_RATIO_MIN = 0.98    # ±2 % — dumps d'une même ECU ont (quasi) la même taille
MIN_ID_LEN = 8           # en-dessous, un identifiant n'est pas assez discriminant
FAMILY_CAP = 24          # fiches « même stock / même calib » jamais coupées par top_n

# Seuils portail (le score interne n'est PLUS utilisé tel quel pour le client)
PORTAL_JAC_COMPATIBLE = 0.85
PORTAL_JAC_VERIFIER = 0.85


def analyze(data: bytes, path: str = "", with_minhash: bool = True) -> dict:
    hdr = headers.detect(data)
    body = hdr["body"]
    sha_full = fingerprint.sha256(data)
    sha_body = fingerprint.sha256(body) if body is not data else sha_full
    mh_v2 = fingerprint.minhash_signature(body, skip_padding=True) if with_minhash else []
    mh_v1 = fingerprint.minhash_signature(data, skip_padding=False) if with_minhash else []
    ex = extract.extract(body if len(body) >= 16 * 1024 else data)
    meta = metadata.parse(path)

    platform = ex["platform"] or meta["platform"]
    manufacturer = ex["manufacturer"] or meta["manufacturer"]
    platform_confirmed = bool(ex["platform"] and meta["platform"]
                              and ex["platform"].upper() == meta["platform"].upper())

    ids = list(ex["candidate_ids"])
    for i in meta["ids"]:
        if i not in ids:
            ids.append(i)

    return {
        "sha256": sha_full,
        "sha256_body": sha_body,
        "size": len(data),
        "body_size": hdr["body_size"],
        "header_len": hdr["header_len"],
        "header_tool": hdr["tool"],
        "minhash": mh_v2,
        "minhash_v1": mh_v1,
        "minhash_ver": fingerprint.MINHASH_VERSION,
        "platform": platform,
        "platform_confirmed": platform_confirmed,
        "manufacturer": manufacturer,
        "candidate_ids": ids,
        "typed_candidates": ex["typed_candidates"],
        "best_ecu_version": ex["best_ecu_version"] or (meta["ids"][0] if meta["ids"] else ""),
        "strings_count": ex["strings_count"],
        "name_meta": meta,
    }


def _size_ratio(a, b):
    if not a or not b:
        return 0.0
    return min(a, b) / max(a, b)


def match(data: bytes, db_path=db.DEFAULT_DB, top_n: int = 12, path: str = "") -> dict:
    info = analyze(data, path)
    sols = db.all_solutions(db_path)

    incoming_ids = {i for i in info["candidate_ids"] if i and len(i) >= MIN_ID_LEN}
    name_meta = info["name_meta"]
    name_words = set()
    for field in (name_meta.get("brand"), name_meta.get("vehicle")):
        if field:
            name_words |= {w.lower() for w in field.split() if len(w) >= 3}
    scored = {}  # id -> dict

    def bump(sol, score, reason, **flags):
        cur = scored.get(sol["id"])
        if cur is None:
            scored[sol["id"]] = {
                "id": sol["id"],
                "ecu_version": sol["ecu_version"],
                "ecu_platform": sol.get("ecu_platform") or "",
                "vehicle_label": sol["vehicle_label"],
                "solution_type": sol["solution_type"],
                "type_atoms": metadata.type_atoms(sol.get("solution_type") or ""),
                "tested_status": sol["tested_status"],
                "solution_file": sol["solution_file"],
                "stock_size": sol["stock_size"],
                "stock_sha256": sol.get("stock_sha256") or "",
                "notes": sol["notes"],
                "exact": False,
                "calibration_exact": False,
                "same_stock": False,
                "type_match": True,
                "size_ratio": flags.get("size_ratio", 0.0),
                "jaccard": flags.get("jaccard", 0.0),
                "platform_same": False,
                "score": round(score, 3),
                "reasons": [reason],
            }
        else:
            cur["score"] = max(cur["score"], round(score, 3))
            if reason not in cur["reasons"]:
                cur["reasons"].append(reason)
        row = scored[sol["id"]]
        if flags.get("exact"):
            row["exact"] = True
        if flags.get("calibration_exact"):
            row["calibration_exact"] = True
        if flags.get("platform_same"):
            row["platform_same"] = True
        if flags.get("same_stock"):
            row["same_stock"] = True
        if "jaccard" in flags:
            row["jaccard"] = max(row.get("jaccard") or 0, flags["jaccard"])
        if "size_ratio" in flags:
            row["size_ratio"] = flags["size_ratio"]

    for sol in sols:
        ok_size, ratio = headers.sizes_compatible(
            sol.get("stock_size"), info["size"], min_ratio=SIZE_RATIO_MIN)
        if not ok_size:
            ok_size, ratio = headers.sizes_compatible(
                sol.get("stock_size"), info.get("body_size"), min_ratio=SIZE_RATIO_MIN)

        # 2. fichier identique (dump complet OU corps sans header)
        sha_sol = sol.get("stock_sha256") or ""
        if sha_sol and sha_sol in (info["sha256"], info.get("sha256_body")):
            bump(sol, 1.0, "Fichier identique au stock enregistré",
                 exact=True, size_ratio=1.0, jaccard=1.0, calibration_exact=True)
            continue

        # 3. calibration exacte (égalité, pas sous-chaîne) — identifiant assez long
        sol_ver = (sol.get("ecu_version") or "").strip()
        if sol_ver and len(sol_ver) >= MIN_ID_LEN and sol_ver in incoming_ids:
            bump(sol, 0.97, f"Calibration exacte ({sol_ver})",
                 calibration_exact=True, size_ratio=ratio)

        # 4. similarité binaire : veto taille (headers programmeur tolérés)
        if ok_size:
            stored_ver = int(sol.get("minhash_ver") or 1)
            incoming_mh = info["minhash"] if stored_ver >= 2 else info.get("minhash_v1") or info["minhash"]
            jac = fingerprint.jaccard(incoming_mh, sol["minhash"])
            if jac >= FUZZY_THRESHOLD:
                bump(sol, jac, f"Binaire similaire ({int(jac * 100)}%)",
                     jaccard=jac, size_ratio=ratio)

        # 5. même plateforme — indice FAIBLE, jamais ≥ 0.6 (seuil portail)
        if (info["platform"] and sol.get("ecu_platform")
                and info["platform"].upper() == sol["ecu_platform"].upper()):
            if info["platform_confirmed"]:
                bump(sol, 0.42, f"Plateforme confirmée nom+binaire ({sol['ecu_platform']})",
                     platform_same=True, size_ratio=ratio)
            else:
                bump(sol, 0.32, f"Même plateforme ({sol['ecu_platform']})",
                     platform_same=True, size_ratio=ratio)

        # 6. recoupement du libellé véhicule — indice faible, plafonné sous 0.6
        if name_words and sol.get("vehicle_label"):
            sol_words = {w.lower() for w in sol["vehicle_label"].split() if len(w) >= 3}
            common = name_words & sol_words
            if len(common) >= 2:
                bump(sol, min(0.40, 0.22 + 0.05 * len(common)),
                     f"Libellé véhicule proche ({', '.join(sorted(common))})",
                     size_ratio=ratio)

    # 1.44 — même stock / même calibration : toutes les prestas, pas seulement
    # celle qui a scoré (top_n ne doit pas les faire disparaître).
    sha_hits = {r.get("stock_sha256") for r in scored.values()
                if r.get("stock_sha256") and (r.get("exact") or r.get("calibration_exact"))}
    ver_hits = {(r.get("ecu_version") or "").strip() for r in scored.values()
                if r.get("calibration_exact") and len((r.get("ecu_version") or "").strip()) >= MIN_ID_LEN}
    for sol in sols:
        if sol["id"] in scored:
            continue
        sha = sol.get("stock_sha256") or ""
        ver = (sol.get("ecu_version") or "").strip()
        if sha and sha in sha_hits:
            bump(sol, 0.99, "Même stock — autre prestation",
                 exact=True, same_stock=True, calibration_exact=True,
                 size_ratio=1.0, jaccard=1.0)
        elif ver and ver in ver_hits:
            bump(sol, 0.96, f"Même calibration — autre prestation ({ver})",
                 calibration_exact=True, same_stock=True)

    want = metadata.type_atoms(name_meta.get("solution_type") or "")
    for row in scored.values():
        atoms = row.get("type_atoms") or metadata.type_atoms(row.get("solution_type") or "")
        row["type_atoms"] = atoms
        row["type_match"] = bool(set(want) & set(atoms)) if want else True

    ranked = sorted(
        scored.values(),
        key=lambda x: (
            not x.get("exact"),
            not x.get("calibration_exact"),
            not x.get("type_match"),
            -x["score"],
        ),
    )
    family = [m for m in ranked if m.get("exact") or m.get("calibration_exact")][:FAMILY_CAP]
    seen = {m["id"] for m in family}
    rest = [m for m in ranked if m["id"] not in seen][:max(0, top_n)]
    matches = family + rest
    for m in matches:
        m["reason"] = " · ".join(m.pop("reasons"))
        m["wanted_types"] = want

    return {
        "incoming": {
            "sha256": info["sha256"],
            "sha256_body": info.get("sha256_body"),
            "size": info["size"],
            "body_size": info.get("body_size"),
            "header_len": info.get("header_len") or 0,
            "header_tool": info.get("header_tool"),
            "platform": info["platform"],
            "platform_confirmed": info["platform_confirmed"],
            "manufacturer": info["manufacturer"],
            "candidate_ids": info["candidate_ids"],
            "typed_candidates": info["typed_candidates"],
            "best_ecu_version": info["best_ecu_version"],
            "strings_count": info["strings_count"],
            "name_meta": info["name_meta"],
            "wanted_types": want,
        },
        "minhash": info["minhash"],
        "minhash_ver": info.get("minhash_ver") or fingerprint.MINHASH_VERSION,
        "matches": matches,
        "db_size": len(sols),
    }


def portal_verdict(result: dict):
    """Décision exposée au client — volontairement plus stricte que le score interne.

    compatible  : sha256 identique, ou calibration exacte + taille quasi identique
    a_verifier  : calibration exacte (taille différente, ex. header programmeur)
                  OU binaire très proche (taille + MinHash ≥ 0.85) + même plateforme
    non_trouve  : le reste (plateforme seule, libellé, sous-chaîne d'identifiant…)
    """
    matches = result.get("matches") or []
    incoming = result.get("incoming") or {}
    best = matches[0] if matches else None
    if not best:
        return "non_trouve", []

    def relevant(pred):
        return [m for m in matches if pred(m)]

    if best.get("exact"):
        return "compatible", relevant(
            lambda m: m.get("exact") or m.get("calibration_exact") or m.get("same_stock"))

    size_ok = (best.get("size_ratio") or 0) >= SIZE_RATIO_MIN
    if best.get("calibration_exact") and size_ok:
        return "compatible", relevant(
            lambda m: m.get("calibration_exact") or m.get("same_stock"))

    if best.get("calibration_exact"):
        return "a_verifier", relevant(
            lambda m: m.get("calibration_exact") or m.get("same_stock"))

    jac_ok = (best.get("jaccard") or 0) >= PORTAL_JAC_VERIFIER
    plat = (incoming.get("platform") or "").upper()
    plat_ok = bool(plat) and plat == (best.get("ecu_platform") or "").upper()
    if size_ok and jac_ok and plat_ok:
        return "a_verifier", relevant(
            lambda m: (_size_ratio(m.get("stock_size"), incoming.get("size")) >= SIZE_RATIO_MIN
                       and (m.get("jaccard") or 0) >= PORTAL_JAC_VERIFIER)
        )

    return "non_trouve", []


def portal_offers(relevant):
    """Atomes de presta à afficher au client (Stage 1 et FAP, pas le paquet collé)."""
    return metadata.ordered_atoms(m.get("solution_type") for m in (relevant or []))


def portal_offer_rows(relevant):
    """Presta + ids de fiches (pour commander un combo)."""
    by = {}
    for m in relevant or []:
        for a in metadata.type_atoms(m.get("solution_type") or ""):
            by.setdefault(a, []).append(int(m["id"]))
    rows = []
    for a in metadata.ordered_atoms(by.keys()):
        ids, seen = [], set()
        for i in by.get(a) or []:
            if i not in seen:
                seen.add(i)
                ids.append(i)
        rows.append({"prestation": a, "ids": ids})
    return rows
