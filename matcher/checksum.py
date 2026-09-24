"""
Correcteur de checksums *prouvés* (blocs additifs / CRC16).

Honnêteté :
  - on ne touche PAS aux signatures RSA / CSA ;
  - on ne corrige que si un schéma *clair* est détecté
    (≥ 70 % des blocs utiles collent : somme 16 bits LE directe ou complément,
     mot en fin *ou* en tête de bloc, ou CRC-16 CCITT / IBM) ;
  - une PKCS#1 / marqueur RSA1-CSA1 → ready = False même si l'additif est juste ;
  - sinon status = "inconnu" → l'UI n'affiche jamais « prêt à flasher ».
"""
from . import extract

# Plateformes où un schéma Bosch additif est plausible
_BOSCH_PREFIXES = (
    "EDC1", "EDC7", "MED", "MEVD", "MEV", "ME7", "ME9", "MG1", "MD1", "MDG1",
    "SIM26", "SIM27", "CRD", "CR6",
)

# Blocs testés, du plus « Bosch flash » au plus fin (EDC15 256 o)
_BLOCK_SIZES = (0x8000, 0x4000, 0x2000, 0x800, 0x100)

_PKCS_NEEDLE = b"\x00\x01\xff\xff\xff\xff"
_RSA_MARKERS = (b"RSA1", b"CSA1", b"PKCS")


def _sum16_le(data: bytes, start: int, end: int) -> int:
    s = 0
    if (end - start) & 1:
        end -= 1
    for i in range(start, end, 2):
        s += data[i] | (data[i + 1] << 8)
    return s & 0xFFFF


def _word(data: bytes, off: int) -> int:
    return data[off] | (data[off + 1] << 8)


def _put_word(buf: bytearray, off: int, value: int) -> None:
    buf[off] = value & 0xFF
    buf[off + 1] = (value >> 8) & 0xFF


def _is_padding_block(data: bytes, start: int, end: int) -> bool:
    n = end - start
    if n <= 0:
        return True
    block = data[start:end]
    return max(block.count(0), block.count(0xFF)) / n >= 0.90


def _crc16_ccitt_false(data: bytes, start: int, end: int) -> int:
    crc = 0xFFFF
    for i in range(start, end):
        crc ^= data[i] << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _crc16_ibm(data: bytes, start: int, end: int) -> int:
    crc = 0
    for i in range(start, end):
        crc ^= data[i]
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def detect_rsa(data: bytes) -> list:
    """Blocs PKCS#1 (FF run ≥ 16) et marqueurs ASCII RSA1 / CSA1 / PKCS.

    Les marqueurs seuls ne suffisent pas à bloquer un flash : ils étiquettent.
    Un vrai PKCS#1 (00 01 FF…FF 00, au moins 16 FF) est une signature.
    """
    raw = bytes(data)
    n = len(raw)
    blocks = []
    p = 0
    while True:
        p = raw.find(_PKCS_NEEDLE, p)
        if p < 0:
            break
        j = p + 2
        while j < n and raw[j] == 0xFF:
            j += 1
        ff_run = j - (p + 2)
        if ff_run >= 16 and j < n and raw[j] == 0:
            # taille module typique 128 / 256 / 512 o
            for blen in (128, 256, 512):
                if ff_run + 3 < blen and p + blen <= n:
                    blocks.append({
                        "off": p, "len": blen, "kind": "pkcs1",
                        "ff_run": ff_run,
                    })
                    break
            p = j + 1
            continue
        p += 1
        if len(blocks) >= 32:
            break
    for marker in _RSA_MARKERS:
        q = 0
        while True:
            q = raw.find(marker, q)
            if q < 0:
                break
            blocks.append({"off": q, "len": len(marker), "kind": "marker",
                           "text": marker.decode("ascii")})
            q += len(marker)
            if len(blocks) >= 48:
                break
    return blocks


def rsa_present(data: bytes) -> bool:
    return any(b.get("kind") == "pkcs1" for b in detect_rsa(data))


def _scan_scheme(data: bytes, block_size: int, kind: str):
    """kind : add_end_comp / add_end_dir / add_start_comp / add_start_dir /
              crc_ccitt / crc_ibm."""
    n = len(data)
    n_blocks = n // block_size
    if n_blocks < 4:
        return None
    hits = n_useful = 0
    for b in range(n_blocks):
        start = b * block_size
        end = start + block_size
        if kind.startswith("add_start"):
            payload = (start + 2, end)
            stored_off = start
        else:
            payload = (start, end - 2)
            stored_off = end - 2
        if _is_padding_block(data, payload[0], payload[1]):
            continue
        n_useful += 1
        stored = _word(data, stored_off)
        if kind.startswith("add_"):
            s = _sum16_le(data, payload[0], payload[1])
            want = ((0x10000 - s) & 0xFFFF) if kind.endswith("comp") else s
        elif kind == "crc_ccitt":
            want = _crc16_ccitt_false(data, payload[0], payload[1])
        else:
            want = _crc16_ibm(data, payload[0], payload[1])
        if stored == want:
            hits += 1
    if n_useful < 4:
        return None
    thresh = max(4, int(n_useful * 0.7))
    if hits < thresh:
        return None
    mode = {
        "add_end_comp": "complement",
        "add_end_dir": "direct",
        "add_start_comp": "complement_tete",
        "add_start_dir": "direct_tete",
        "crc_ccitt": "crc16_ccitt",
        "crc_ibm": "crc16_ibm",
    }[kind]
    family = "crc" if kind.startswith("crc") else (
        "additif_tete" if "start" in kind else "additif")
    return {
        "block": block_size, "mode": mode, "kind": kind, "family": family,
        "hits": hits, "useful": n_useful, "blocks": n_blocks,
        "ratio": hits / n_useful,
    }


def detect_scheme(data: bytes, platform: str = ""):
    """Trouve le meilleur schéma, ou None. Additif d'abord, puis CRC."""
    best = None
    kinds = (
        "add_end_comp", "add_end_dir",
        "add_start_comp", "add_start_dir",
        "crc_ccitt", "crc_ibm",
    )
    family_rank = {"additif": 2, "additif_tete": 1, "crc": 0}
    for bs in _BLOCK_SIZES:
        for kind in kinds:
            if kind.startswith("crc") and bs < 0x800:
                continue
            sch = _scan_scheme(data, bs, kind)
            if not sch:
                continue
            if best is None:
                best = sch
            else:
                better_ratio = sch["ratio"] > best["ratio"] + 0.05
                close = abs(sch["ratio"] - best["ratio"]) <= 0.05
                better_family = (family_rank.get(sch["family"], 0)
                                 > family_rank.get(best["family"], 0))
                bigger = sch["block"] > best["block"]
                # CRC ne remplace un additif que s'il est franchement meilleur
                if sch["family"] == "crc" and best["family"] != "crc" and not better_ratio:
                    continue
                if better_ratio or (close and better_family) or (close and bigger):
                    best = sch
            if best and best["family"] == "additif" and best["ratio"] >= 0.95:
                return best
    return best



def _bosch_like(platform: str) -> bool:
    p = (platform or "").upper().replace(" ", "")
    return any(p.startswith(pref) for pref in _BOSCH_PREFIXES)


def _correct(data: bytes, scheme: dict) -> tuple:
    """Applique le schéma. Renvoie (bytes, n_corrigés, n_déjà_ok)."""
    bs = scheme["block"]
    kind = scheme["kind"]
    out = bytearray(data)
    corrected = already_ok = 0
    n_blocks = len(out) // bs
    for b in range(n_blocks):
        start = b * bs
        end = start + bs
        if kind.startswith("add_start"):
            payload = (start + 2, end)
            stored_off = start
        else:
            payload = (start, end - 2)
            stored_off = end - 2
        if kind.startswith("add_"):
            s = _sum16_le(out, payload[0], payload[1])
            want = ((0x10000 - s) & 0xFFFF) if "comp" in kind else s
        elif kind == "crc_ccitt":
            want = _crc16_ccitt_false(out, payload[0], payload[1])
        else:
            want = _crc16_ibm(out, payload[0], payload[1])
        stored = _word(out, stored_off)
        if stored == want:
            already_ok += 1
            continue
        _put_word(out, stored_off, want)
        corrected += 1
    return bytes(out), corrected, already_ok


def apply(data: bytes, platform: str = "") -> dict:
    """Corrige les checksums additifs / CRC16 si un schéma est détecté.

    Renvoie un dict :
      status : ok | corrige | inconnu | non_applicable
      method, blocks_corrected, note, data, ready, rsa
    """
    raw = bytes(data)
    plat = platform or (extract.extract(raw).get("platform") or "")
    scheme = detect_scheme(raw, plat)
    rsa_blocks = detect_rsa(raw)
    has_pkcs = any(b.get("kind") == "pkcs1" for b in rsa_blocks)

    def _rsa_note(base):
        if not has_pkcs:
            return base
        n = sum(1 for b in rsa_blocks if b.get("kind") == "pkcs1")
        extra = (f" Signature RSA/CSA détectée ({n} bloc(s) PKCS#1) — "
                 "passe le fichier dans WinOLS / un correcteur dédié avant flash. "
                 "RSA non touchée.")
        return (base + " " + extra).strip()

    if not scheme:
        if _bosch_like(plat) or has_pkcs:
            note = ("Schéma Bosch additif non reconnu sur ce dump "
                    "(EDC17 RSA / CSA propriétaire). Passe le fichier dans "
                    "WinOLS / un correcteur de checksum avant flash.")
            if has_pkcs:
                note = _rsa_note("Aucun checksum additif/CRC16 clair.")
            status = "inconnu"
        else:
            note = ("Pas de correcteur connu pour cette plateforme. "
                    "Vérifie les checksums dans WinOLS avant flash.")
            status = "non_applicable"
        return {"status": status, "method": "", "blocks_corrected": 0,
                "note": note, "data": raw, "ready": False, "platform": plat,
                "rsa": has_pkcs, "rsa_blocks": rsa_blocks}

    out, corrected, already_ok = _correct(raw, scheme)
    bs = scheme["block"]
    mode = scheme["mode"]
    family = scheme["family"]
    if family == "crc":
        method = f"{mode}_{bs // 1024}k"
        human = f"CRC-16 ({'CCITT' if 'ccitt' in mode else 'IBM'})"
    elif family == "additif_tete":
        method = f"bosch_additif_tete_{bs // 1024 if bs >= 1024 else bs}{'k' if bs >= 1024 else 'o'}_{mode}"
        human = f"checksums additifs (mot en tête, {bs // 1024 if bs >= 1024 else bs} {'Ko' if bs >= 1024 else 'o'}, {mode})"
    else:
        method = f"bosch_additif_{bs // 1024 if bs >= 1024 else bs}{'k' if bs >= 1024 else 'o'}_{mode}"
        human = f"checksums Bosch additifs ({bs // 1024 if bs >= 1024 else bs} {'Ko' if bs >= 1024 else 'o'}, {mode})"

    if corrected == 0:
        status = "ok"
        note = f"{human[0].upper() + human[1:]} déjà justes ({already_ok} blocs)."
    else:
        status = "corrige"
        note = (f"{human[0].upper() + human[1:]} recalculés : {corrected} bloc(s) "
                f"corrigé(s), {already_ok} déjà justes. RSA non touchée.")
    ready = not has_pkcs
    note = _rsa_note(note)
    if has_pkcs:
        # additif/CRC justes ≠ flashable
        pass
    return {
        "status": status, "method": method, "blocks_corrected": corrected,
        "blocks_ok": already_ok, "note": note, "data": out,
        "ready": ready, "platform": plat,
        "rsa": has_pkcs, "rsa_blocks": rsa_blocks,
    }


def describe(result: dict) -> str:
    return result.get("note") or ""
