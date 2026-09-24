"""
Détection / retrait des headers programmeur (KESS, Autotuner, PCMFlash…).

Deux dumps du même ECU, l'un lu au KESS ( +0x400 ) et l'autre à l'Autotuner,
doivent se comparer et se patcher sur le *corps* — pas sur l'enveloppe.
"""
KNOWN_HEADER = (0x100, 0x200, 0x400, 0x410, 0x800, 0x1000, 0x2000)

STANDARD_SIZES = {
    128 * 1024, 256 * 1024, 384 * 1024, 512 * 1024, 640 * 1024, 768 * 1024,
    1024 * 1024, 1536 * 1024, 2048 * 1024, 2560 * 1024, 3072 * 1024, 4096 * 1024,
}

_MAGICS = (
    (b"KESS", "KESS"),
    (b"KTAG", "KTAG"),
    (b"Alientech", "Alientech"),
    (b"PCMFlash", "PCMFlash"),
    (b"AutoTuner", "Autotuner"),
    (b"Autotuner", "Autotuner"),
    (b"CMD FLASH", "CMD"),
    (b"MPPS", "MPPS"),
    (b"Galletto", "Galletto"),
    (b"BitBox", "BitBox"),
    (b"Flex", "Flex"),
)


def _looks_like_header(chunk: bytes) -> bool:
    if not chunk:
        return False
    # un vrai flash démarre rarement par une majorité d'ASCII ; un header outil, si
    return sum(1 for b in chunk if 32 <= b < 127) / len(chunk) > 0.45


def _tool_in(data: bytes):
    probe = data[: min(0x2000, len(data))]
    for mag, name in _MAGICS:
        if mag in probe:
            return name
    return None


def detect(data: bytes) -> dict:
    """Repère un header / trailer d'outil. `body` est le dump ECU à comparer."""
    n = len(data or b"")
    empty = {"header_len": 0, "trailer_len": 0, "tool": None,
             "body": data or b"", "body_size": n}
    if n < 16 * 1024:
        return empty
    tool = _tool_in(data)
    header_len = trailer_len = 0
    if n not in STANDARD_SIZES:
        for h in KNOWN_HEADER:
            if n - h in STANDARD_SIZES:
                start, end = data[:h], data[-h:]
                if tool or _looks_like_header(start):
                    header_len = h
                elif _looks_like_header(end):
                    trailer_len = h
                else:
                    header_len = h  # convention : les outils prépendent
                break
    body = data[header_len: n - trailer_len if trailer_len else n]
    return {
        "header_len": header_len,
        "trailer_len": trailer_len,
        "tool": tool,
        "body": body,
        "body_size": len(body),
    }


def split(data: bytes):
    """(header, body, trailer)."""
    d = detect(data)
    n = len(data)
    h, t = d["header_len"], d["trailer_len"]
    return data[:h], data[h: n - t if t else n], data[n - t:] if t else b""


def reattach(header: bytes, body: bytes, trailer: bytes) -> bytes:
    return (header or b"") + body + (trailer or b"")


def sizes_compatible(a, b, min_ratio=0.98):
    """True si les tailles collent, éventuellement à un header connu près."""
    if not a or not b:
        return False, 0.0
    ratio = min(a, b) / max(a, b)
    if ratio >= min_ratio:
        return True, ratio
    for h in KNOWN_HEADER:
        if a > h and min(a - h, b) / max(a - h, b) >= min_ratio:
            return True, min(a - h, b) / max(a - h, b)
        if b > h and min(a, b - h) / max(a, b - h) >= min_ratio:
            return True, min(a, b - h) / max(a, b - h)
    return False, ratio


def align_to(reference: bytes, candidate: bytes):
    """Essaie d'aligner `candidate` sur la longueur de `reference`.

    Renvoie (aligned_or_None, info).
    """
    if len(candidate) == len(reference):
        return candidate, {"aligned": False, "delta": 0}
    delta = len(candidate) - len(reference)
    if delta in KNOWN_HEADER:
        # header en trop au début ?
        return candidate[delta:], {"aligned": True, "delta": delta, "where": "header"}
    if -delta in KNOWN_HEADER:
        # candidate plus court : on ne pad pas (risqué). Signale seulement.
        return None, {"aligned": False, "delta": delta, "error": "plus_court"}
    # essai via detect()
    ref_b = detect(reference)["body"]
    cand_b = detect(candidate)["body"]
    if len(cand_b) == len(ref_b):
        return cand_b, {"aligned": True, "delta": len(candidate) - len(cand_b),
                        "where": "body", "ref_body": True}
    if len(cand_b) == len(reference):
        return cand_b, {"aligned": True, "delta": len(candidate) - len(cand_b),
                        "where": "body"}
    return None, {"aligned": False, "delta": delta}
