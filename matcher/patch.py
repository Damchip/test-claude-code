"""
Auto-patch côté serveur : delta original → solution, appliqué au fichier client.

Toute la logique vit ici (plus dans le navigateur) : alignement des headers
programmeur, zones, étiquetage local, checksums Bosch, génération du bin.
"""
import os

from . import checksum, db, headers, maps as mapsmod, zones as zonemod


GAP = 16


def diff_regions(a, b, gap=GAP):
    n = max(len(a), len(b))
    regions = []
    start = last = -1
    for i in range(n):
        av = a[i] if i < len(a) else -1
        bv = b[i] if i < len(b) else -2
        if av != bv:
            if start < 0:
                start = i
            last = i
        elif start >= 0 and i - last > gap:
            regions.append((start, last))
            start = -1
    if start >= 0:
        regions.append((start, last))
    return regions


def _bytes_equal_range(a, b, off, length):
    return a[off:off + length] == b[off:off + length]


def zone_features(orig, sol, off, length):
    bits = 16 if (length >= 8 and length % 2 == 0) else 8
    vals = []
    if bits == 16:
        for i in range(0, length, 2):
            vals.append(orig[off + i] | (orig[off + i + 1] << 8))
    else:
        vals = list(orig[off:off + length])
    mn, mx, sum_abs, sign_changes, incr, prev_sign = 10**9, -1, 0, 0, 0, 0
    for i, v in enumerate(vals):
        if v < mn:
            mn = v
        if v > mx:
            mx = v
        if i > 0:
            dlt = v - vals[i - 1]
            sum_abs += abs(dlt)
            if dlt >= 0:
                incr += 1
            sg = 0 if dlt == 0 else (1 if dlt > 0 else -1)
            if sg and prev_sign and sg != prev_sign:
                sign_changes += 1
            if sg:
                prev_sign = sg
    rng = mx - mn
    avg_delta = sum_abs / (len(vals) - 1) if len(vals) > 1 else 0
    smooth = (avg_delta / rng) if rng > 0 else 0
    monotonic = len(vals) > 3 and incr >= (len(vals) - 1) * 0.92 and rng > 0
    if rng == 0:
        shape = "constant"
    elif monotonic:
        shape = "monotonic"
    elif smooth < 0.18 and length >= 16:
        shape = "smooth"
    else:
        shape = "noisy"
    chunk = sol[off:off + length]
    if chunk and all(b == 0 for b in chunk):
        change = "zeroed"
    elif chunk and all(b == 0xFF for b in chunk):
        change = "ff"
    elif chunk and all(b == chunk[0] for b in chunk):
        change = "constant"
    else:
        change = "mixed"
    smin = min(chunk) if chunk else 0
    smax = max(chunk) if chunk else 0
    return {
        "bits": bits, "shape": shape, "change": change,
        "origRange": [mn if mn < 10**9 else 0, mx if mx >= 0 else 0],
        "solRange": [smin, smax],
        "signChanges": sign_changes, "smooth": round(smooth, 3),
    }


def local_zone_label(feat, fiche_type, length, rsa=False):
    return zonemod.local_zone_label(feat, fiche_type, length, rsa=rsa)


def evaluate_patch(client, orig, sol, fiche_type=""):
    """Évalue la compatibilité. Aligne les headers si les tailles diffèrent."""
    header_note = None
    c_hdr, c_body, c_trl = headers.split(client)
    o_hdr, o_body, o_trl = headers.split(orig)
    s_hdr, s_body, s_trl = headers.split(sol)

    # 1) corps déjà de même taille
    if len(o_body) == len(s_body) == len(c_body):
        work_c, work_o, work_s = c_body, o_body, s_body
        wrap = (c_hdr, c_trl)
        if c_hdr or o_hdr or s_hdr:
            tools = [headers.detect(x)["tool"] for x in (client, orig, sol)]
            tool = next((t for t in tools if t), None)
            header_note = (f"Header programmeur retiré pour le patch"
                           f" ({len(c_hdr) or len(o_hdr)} o"
                           f"{', ' + tool if tool else ''}).")
    elif len(orig) == len(sol) == len(client):
        work_c, work_o, work_s = client, orig, sol
        wrap = (b"", b"")
    else:
        aligned, info = headers.align_to(orig, client)
        if aligned is not None and len(orig) == len(sol) == len(aligned):
            work_c, work_o, work_s = aligned, orig, sol
            wrap = (client[:info.get("delta", 0)], b"") if info.get("where") == "header" else (b"", b"")
            header_note = f"Fichier client aligné (−{info.get('delta', 0)} o de header)."
        elif len(o_body) == len(s_body) and len(c_body) == len(o_body):
            work_c, work_o, work_s = c_body, o_body, s_body
            wrap = (c_hdr, c_trl)
            header_note = "Corps alignés après retrait des headers."
        else:
            if len(orig) != len(sol) and len(o_body) != len(s_body):
                return {"error": "taille_incompatible_orig_sol",
                        "detail": f"original {len(orig)} o / solution {len(sol)} o"}
            return {"error": "taille_incompatible_client",
                    "detail": (f"client {len(client)} o, original {len(orig)} o "
                               f"(corps {len(c_body)} / {len(o_body)})")}

    regions = diff_regions(work_o, work_s)
    if not regions:
        return {"error": "solution_identique_original"}

    rsa_blocks = checksum.detect_rsa(work_s) or checksum.detect_rsa(work_o)
    rsa_pkcs = [b for b in rsa_blocks if b.get("kind") == "pkcs1"]

    zones = []
    matched = changed_bytes = rsa_skipped = 0
    for i, (s, e) in enumerate(regions):
        length = e - s + 1
        ok = _bytes_equal_range(work_c, work_o, s, length)
        if ok:
            matched += 1
        changed_bytes += length
        feat = zone_features(work_o, work_s, s, length)
        is_rsa = zonemod.overlaps_rsa(s, length, rsa_pkcs)
        named = zonemod.name_zone(feat, fiche_type, length, rsa=is_rsa)
        if is_rsa:
            rsa_skipped += 1
        guess = None
        if length >= 32 and not is_rsa:
            rec = mapsmod.guess_from_zone(work_o, s, length)
            if rec and rec.get("score", 0) >= 0.35:
                guess = {k: rec[k] for k in (
                    "kind", "bits", "endian", "rows", "cols",
                    "axis_x_off", "axis_y_off", "data_off", "offset",
                    "end", "score", "zmin", "zmax") if k in rec}
        zones.append({
            "i": i, "off": s, "len": length, "ok": ok,
            "feat": feat,
            "localLabel": named["label"],
            "zone": named,
            "rsa": is_rsa,
            "map": guess,
        })
    mismatched = len(zones) - matched


    in_zone = bytearray(len(work_c))
    for z in zones:
        in_zone[z["off"]: z["off"] + z["len"]] = b"\x01" * z["len"]
    diff_outside = sum(1 for i in range(len(work_c))
                       if not in_zone[i] and work_c[i] != work_o[i])

    if mismatched == 0 and diff_outside == 0:
        verdict = "propre"
    elif mismatched == 0:
        verdict = "checksum_a_verifier"
    else:
        verdict = "incompatible"

    return {
        "verdict": verdict,
        "zones": zones,
        "mismatched": mismatched,
        "matched": matched,
        "diff_outside": diff_outside,
        "changed_bytes": changed_bytes,
        "header_note": header_note,
        "work_size": len(work_c),
        "rsa": bool(rsa_pkcs),
        "rsa_skipped": rsa_skipped,
        "_work": (work_c, work_o, work_s, wrap),
    }



def apply_patch(client_body, sol_body, zones, partial=False):
    """Copie les zones solution → client. Jamais une zone incompatible
    ni une zone RSA (partial ne fait qu'autoriser la génération en n'appliquant
    que le compatible)."""
    out = bytearray(client_body)
    applied = 0
    skipped_rsa = 0
    for z in zones:
        if z.get("rsa") or not z.get("zone", {}).get("apply", True):
            skipped_rsa += 1
            continue
        if not z["ok"]:
            continue
        s, n = z["off"], z["len"]
        out[s:s + n] = sol_body[s:s + n]
        applied += 1
    return bytes(out), applied, skipped_rsa



def build_patched(client, orig, sol, fiche_type="", platform="", partial=False):
    """Produit le bin patché + checksums. Ne lève pas : renvoie un dict."""
    ev = evaluate_patch(client, orig, sol, fiche_type=fiche_type)
    work = ev.pop("_work", None)
    if "error" in ev:
        return ev
    if ev["verdict"] == "incompatible" and not partial:
        ev["error"] = "zones_incompatibles"
        return ev
    if work is None:
        ev["error"] = "alignement_impossible"
        return ev
    work_c, work_o, work_s, wrap = work
    patched_body, applied, skipped_rsa = apply_patch(
        work_c, work_s, ev["zones"],
        partial=partial or ev["verdict"] != "propre")
    hdr, trl = wrap
    patched = headers.reattach(hdr, patched_body, trl)

    ck = checksum.apply(patched, platform=platform)
    ev["applied"] = applied
    ev["rsa_skipped"] = skipped_rsa or ev.get("rsa_skipped") or 0
    ev["checksum"] = {k: ck[k] for k in ("status", "method", "blocks_corrected",
                                         "note", "ready", "rsa") if k in ck}
    ev["patched"] = ck["data"]
    # RSA présente → jamais « prêt à flasher », même additif juste.
    ev["ready_to_flash"] = bool(
        ev["verdict"] == "propre" and ck.get("ready") and not ck.get("rsa")
        and not ev["rsa_skipped"])
    return ev



def analyze_solution(client, orig, sol, fiche=None):
    """Rapport JSON (sans les octets) pour l'UI."""
    fiche = fiche or {}
    ev = evaluate_patch(client, orig, sol, fiche_type=fiche.get("solution_type") or "")
    if "_work" in ev:
        ev.pop("_work")
    if "error" in ev:
        return ev
    mismatched = ev["mismatched"]
    if ev["verdict"] == "propre":
        text = ("Patch propre — le fichier client est identique au stock connu "
                "hors modifications.")
        vcls = "patch-good"
    elif ev["verdict"] == "checksum_a_verifier":
        text = (f"Zones compatibles, mais le fichier client diffère du stock ailleurs "
                f"({ev['diff_outside']} octets) — checksums à revoir.")
        vcls = "patch-warn"
    else:
        text = (f"{mismatched} zone(s) sur {len(ev['zones'])} ne correspondent pas "
                "au stock dans le fichier client. Appliquer tel quel risque de corrompre.")
        vcls = "patch-bad"
    ck_preview = checksum.apply(client, platform=fiche.get("ecu_platform") or "")
    rsa_note = None
    if ev.get("rsa") or ck_preview.get("rsa"):
        rsa_note = ("Signature RSA/CSA détectée — les checksums additifs ne suffisent "
                    "pas. Les zones RSA ne sont pas patchées. Passe le fichier dans "
                    "WinOLS avant flash.")
    return {
        "verdict": ev["verdict"],
        "verdict_text": text,
        "vcls": vcls,
        "zones": ev["zones"],
        "matched": ev["matched"],
        "mismatched": mismatched,
        "diff_outside": ev["diff_outside"],
        "changed_bytes": ev["changed_bytes"],
        "header_note": ev.get("header_note"),
        "work_size": ev.get("work_size"),
        "allMatched": mismatched == 0,
        "rsa": bool(ev.get("rsa") or ck_preview.get("rsa")),
        "rsa_skipped": ev.get("rsa_skipped") or 0,
        "rsa_note": rsa_note,
        "stats": (f"{len(ev['zones'])} zone(s) · {ev['matched']} compatible(s) · "
                  f"{mismatched} incompatible(s) · "
                  f"{ev['changed_bytes']} octets modifiés"
                  + (f" · {ev.get('rsa_skipped') or 0} RSA ignorée(s)"
                     if ev.get("rsa_skipped") else "")),
        "fiche": {
            "platform": fiche.get("ecu_platform") or "",
            "manufacturer": fiche.get("manufacturer") or "",
            "solution_type": fiche.get("solution_type") or "",
            "size": len(orig),
        },
        "checksum": {
            "status": ck_preview["status"],
            "method": ck_preview.get("method") or "",
            "note": ck_preview["note"],
            "ready": ck_preview["ready"],
            "rsa": ck_preview.get("rsa") or False,
        },
        "client_size": len(client),
    }



def load_fiche_bins(db_path, sol_id):
    sol = db.get_solution(db_path, sol_id)
    if not sol:
        return None, "Solution introuvable"
    orig_path = sol.get("original_file") or ""
    sol_path = sol.get("solution_file") or ""
    if not orig_path or not os.path.isfile(orig_path):
        return sol, "original_missing"
    if not sol_path or not os.path.isfile(sol_path):
        return sol, "solution_missing"
    with open(orig_path, "rb") as fh:
        orig = fh.read()
    with open(sol_path, "rb") as fh:
        soldata = fh.read()
    return sol, (orig, soldata)



def _parse_ids(raw):
    ids = []
    if raw is None:
        return ids
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw).replace(";", ",").split(",")
    for part in parts:
        part = str(part).strip()
        if not part:
            continue
        try:
            i = int(part)
        except (TypeError, ValueError):
            continue
        if i not in ids:
            ids.append(i)
    return ids


def load_many(db_path, ids):
    """Charge plusieurs fiches. Renvoie (bundles, errors)."""
    bundles, errors = [], []
    for sid in ids:
        fiche, bins = load_fiche_bins(db_path, sid)
        if not fiche:
            errors.append({"id": sid, "error": "introuvable"})
            continue
        if bins == "original_missing":
            errors.append({"id": sid, "error": "original_missing",
                           "type": fiche.get("solution_type")})
            continue
        if bins == "solution_missing":
            errors.append({"id": sid, "error": "solution_missing",
                           "type": fiche.get("solution_type")})
            continue
        orig, soldata = bins
        bundles.append({"fiche": fiche, "orig": orig, "sol": soldata})
    return bundles, errors


def _overlap_len(a0, a1, b0, b1):
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, hi - lo)


def stock_keys(fiche):
    sha = (fiche.get("stock_sha256") or "").strip().lower()
    ver = (fiche.get("ecu_version") or "").strip()
    return sha, ver


def same_stock_groups(bundles):
    """Regroupe les fiches par stock (sha puis calibre)."""
    groups = {}
    for b in bundles:
        sha, ver = stock_keys(b.get("fiche") or {})
        key = sha or ("cal:" + ver if ver else "inconnu")
        groups.setdefault(key, []).append(b)
    return groups


def combine_evaluations(client, bundles, force_mix=False):
    """Superpose plusieurs deltas ORI→SOL sur le même dump client.

    Conflit = même octet écrit avec deux valeurs différentes.
    RSA jamais appliquée.
    Deux stocks / deux calibres différents : refusé sauf force_mix.
    """
    if not bundles:
        return {"error": "aucune_fiche"}
    groups = same_stock_groups(bundles)
    if len(groups) > 1 and not force_mix:
        detail = []
        for k, items in groups.items():
            types = ", ".join((b["fiche"].get("solution_type") or "?") for b in items)
            detail.append({"stock": k[:16], "n": len(items), "types": types})
        return {
            "error": "stocks_melanges",
            "detail": "Impossible de combiner des fiches de stocks / calibres différents.",
            "groups": detail,
        }
    reports = []
    writes = {}
    wrap = (b"", b"")
    work_c = None
    platform = ""
    types = []
    rsa_any = False
    rsa_skipped = 0
    header_notes = []

    for b in bundles:
        fiche = b["fiche"]
        ftype = fiche.get("solution_type") or ""
        types.append(ftype)
        ev = evaluate_patch(client, b["orig"], b["sol"], fiche_type=ftype)
        work = ev.pop("_work", None)
        item = {
            "id": fiche.get("id"),
            "solution_type": ftype,
            "vehicle_label": fiche.get("vehicle_label") or "",
            "ecu_version": fiche.get("ecu_version") or "",
            "error": ev.get("error"),
            "verdict": ev.get("verdict"),
            "matched": ev.get("matched") or 0,
            "mismatched": ev.get("mismatched") or 0,
            "zones": ev.get("zones") or [],
            "changed_bytes": ev.get("changed_bytes") or 0,
            "rsa_skipped": ev.get("rsa_skipped") or 0,
        }
        reports.append(item)
        if ev.get("error") or work is None:
            continue
        wc, _wo, ws, wr = work
        if work_c is None:
            work_c, wrap = wc, wr
            platform = fiche.get("ecu_platform") or platform
        if ev.get("header_note"):
            header_notes.append(ev["header_note"])
        if ev.get("rsa"):
            rsa_any = True
        rsa_skipped += ev.get("rsa_skipped") or 0
        fid = fiche.get("id")
        for z in ev.get("zones") or []:
            if z.get("rsa") or not z.get("zone", {}).get("apply", True):
                continue
            if not z.get("ok"):
                continue
            s, n = z["off"], z["len"]
            for i in range(n):
                off = s + i
                if off >= len(ws) or off >= len(work_c):
                    continue
                val = ws[off]
                prev = writes.get(off)
                if prev is None:
                    writes[off] = [val, fid, ftype]
                elif prev[0] != val:
                    prev.append(True)
                    prev.append(fid)
                    prev.append(ftype)

    conflicts = []
    conflict_bytes = 0
    for off, rec in writes.items():
        if len(rec) > 3:
            conflict_bytes += 1
            if len(conflicts) < 40:
                conflicts.append({
                    "off": off,
                    "a_id": rec[1], "a_type": rec[2],
                    "b_id": rec[4] if len(rec) > 4 else None,
                    "b_type": rec[5] if len(rec) > 5 else None,
                })

    zone_conflicts = []
    seen_pairs = set()
    for i, a in enumerate(reports):
        for j, b in enumerate(reports):
            if j <= i:
                continue
            for za in a.get("zones") or []:
                if za.get("rsa") or not za.get("ok"):
                    continue
                for zb in b.get("zones") or []:
                    if zb.get("rsa") or not zb.get("ok"):
                        continue
                    ov = _overlap_len(za["off"], za["off"] + za["len"],
                                      zb["off"], zb["off"] + zb["len"])
                    if ov <= 0:
                        continue
                    key = (a["id"], za["off"], b["id"], zb["off"])
                    if key in seen_pairs:
                        continue
                    real = False
                    start = max(za["off"], zb["off"])
                    for k in range(ov):
                        rec = writes.get(start + k)
                        if rec and len(rec) > 3:
                            real = True
                            break
                    if not real:
                        continue
                    seen_pairs.add(key)
                    zone_conflicts.append({
                        "a_id": a["id"], "a_type": a["solution_type"],
                        "a_off": za["off"], "a_len": za["len"],
                        "b_id": b["id"], "b_type": b["solution_type"],
                        "b_off": zb["off"], "b_len": zb["len"],
                        "overlap": ov,
                    })

    if work_c is None:
        return {
            "error": "aucune_fiche_applicable",
            "fiches": reports,
            "conflicts": zone_conflicts,
        }

    clean_writes = {off: rec[0] for off, rec in writes.items() if len(rec) <= 3}
    patched_body = bytearray(work_c)
    for off, val in clean_writes.items():
        patched_body[off] = val

    verdicts = [r.get("verdict") for r in reports if r.get("verdict")]
    if any(v == "incompatible" for v in verdicts):
        verdict = "incompatible"
    elif zone_conflicts:
        verdict = "conflit"
    elif any(v == "checksum_a_verifier" for v in verdicts):
        verdict = "checksum_a_verifier"
    else:
        verdict = "propre"

    combined_type = " + ".join(t for t in types if t) or "combiné"
    return {
        "verdict": verdict,
        "fiches": reports,
        "conflicts": zone_conflicts,
        "conflict_bytes": conflict_bytes,
        "applied_bytes": len(clean_writes),
        "combined_type": combined_type,
        "platform": platform,
        "rsa": rsa_any,
        "rsa_skipped": rsa_skipped,
        "header_note": header_notes[0] if header_notes else None,
        "ids": [b["fiche"].get("id") for b in bundles],
        "_work": (bytes(patched_body), wrap, platform),
    }


def analyze_combined(client, bundles, force_mix=False):
    ev = combine_evaluations(client, bundles, force_mix=force_mix)
    work = ev.pop("_work", None)
    if ev.get("error") and ev.get("error") not in ("aucune_fiche_applicable",):
        return ev
    n = len(ev.get("fiches") or [])
    n_ok = sum(1 for f in ev.get("fiches") or [] if f.get("verdict") == "propre")
    n_bad = sum(1 for f in ev.get("fiches") or [] if f.get("error") or f.get("verdict") == "incompatible")
    n_conf = len(ev.get("conflicts") or [])
    if ev.get("verdict") == "propre":
        text = (f"{n} presta(s) superposables — {ev.get('applied_bytes') or 0} octets "
                "à écrire, aucun conflit.")
        vcls = "patch-good"
    elif ev.get("verdict") == "conflit":
        text = (f"{n_conf} zone(s) se recouvrent avec des valeurs différentes. "
                "Les octets en conflit ne seront pas écrits ; le reste peut l'être.")
        vcls = "patch-warn"
    elif ev.get("verdict") == "checksum_a_verifier":
        text = "Zones combinables, mais le dump diffère du stock hors patch — checksums à revoir."
        vcls = "patch-warn"
    else:
        text = f"{n_bad} presta(s) incompatible(s) sur {n}."
        vcls = "patch-bad"
    ck_preview = checksum.apply(client, platform=ev.get("platform") or "")
    rsa_note = None
    if ev.get("rsa") or ck_preview.get("rsa"):
        rsa_note = ("Signature RSA/CSA détectée — les zones RSA ne sont pas patchées. "
                    "Passe le fichier dans WinOLS avant flash.")
    return {
        "verdict": ev.get("verdict"),
        "verdict_text": text,
        "vcls": vcls,
        "fiches": ev.get("fiches") or [],
        "conflicts": ev.get("conflicts") or [],
        "conflict_bytes": ev.get("conflict_bytes") or 0,
        "applied_bytes": ev.get("applied_bytes") or 0,
        "combined_type": ev.get("combined_type") or "",
        "ids": ev.get("ids") or [],
        "rsa": bool(ev.get("rsa") or ck_preview.get("rsa")),
        "rsa_skipped": ev.get("rsa_skipped") or 0,
        "rsa_note": rsa_note,
        "header_note": ev.get("header_note"),
        "allMatched": ev.get("verdict") == "propre",
        "multi": True,
        "stats": (f"{n} presta(s) · {n_ok} propre(s) · {n_bad} incompatible(s) · "
                  f"{ev.get('applied_bytes') or 0} o à écrire"
                  + (f" · {n_conf} conflit(s)" if n_conf else "")),
        "fiche": {
            "platform": ev.get("platform") or "",
            "solution_type": ev.get("combined_type") or "",
            "size": len(client),
        },
        "checksum": {
            "status": ck_preview["status"],
            "method": ck_preview.get("method") or "",
            "note": ck_preview["note"],
            "ready": ck_preview["ready"],
            "rsa": ck_preview.get("rsa") or False,
        },
        "client_size": len(client),
        "_has_work": work is not None,
    }


def build_combined(client, bundles, partial=False, force_mix=False):
    ev = combine_evaluations(client, bundles, force_mix=force_mix)
    work = ev.pop("_work", None)
    if ev.get("error") and not work:
        return ev
    if ev.get("verdict") == "incompatible" and not partial:
        ev["error"] = "zones_incompatibles"
        return ev
    if work is None:
        ev["error"] = "alignement_impossible"
        return ev
    patched_body, wrap, platform = work
    hdr, trl = wrap
    patched = headers.reattach(hdr, patched_body, trl)
    ck = checksum.apply(patched, platform=platform)
    ev["checksum"] = {k: ck[k] for k in ("status", "method", "blocks_corrected",
                                         "note", "ready", "rsa") if k in ck}
    ev["patched"] = ck["data"]
    ev["applied"] = ev.get("applied_bytes") or 0
    ev["ready_to_flash"] = bool(
        ev.get("verdict") == "propre" and ck.get("ready") and not ck.get("rsa")
        and not ev.get("rsa_skipped") and not ev.get("conflicts"))
    return ev
