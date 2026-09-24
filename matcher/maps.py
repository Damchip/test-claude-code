"""
Détection de cartes 1D / 2D (Kennlinie / Kennfeld) dans un dump ECU.

Heuristique volontairement conservatrice — on préfère rater une carte
douteuse plutôt que d'en afficher 200 fausses dans du padding / du random :

  1. Axes 16 bits (LE par défaut) strictement croissants, « propres »
     (quasi linéaires, souvent 0 en tête, pas du 0xFFFF).
  2. Carte 2D = axe X (colonnes) collé à un axe Y (lignes) collé à une
     table Z row-major de rows×cols. La table doit être plus lisse que
     du bruit.
  3. Carte 1D = un axe suivi de N valeurs qui ne forment pas un 2e axe.

Les headers programmeur sont retirés avant le scan (corps ECU).
Aucune donnée ne sort de la machine.
"""
from . import headers

TYPICAL_DIMS = (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
                17, 18, 20, 21, 24, 25, 28, 30, 32)
MIN_AXIS = 4
MAX_AXIS = 32
MAX_MAPS = 80


def _read(data, off, bits, be):
    if bits == 8:
        return data[off]
    if be:
        return (data[off] << 8) | data[off + 1]
    return data[off] | (data[off + 1] << 8)


def _read_n(data, off, n, bits, be):
    bps = bits // 8
    out = []
    for i in range(n):
        p = off + i * bps
        if p + bps > len(data):
            return None
        out.append(_read(data, p, bits, be))
    return out


def _is_pad_window(data, off, win=64):
    chunk = data[off:off + win]
    if not chunk:
        return True
    n = len(chunk)
    return max(chunk.count(0), chunk.count(0xFF)) / n >= 0.90


def axis_quality(vals):
    """0..1 — 0 = rejeté. Linéaire, 0 en tête, pas du bruit d'adresses."""
    n = len(vals)
    if n < MIN_AXIS or n > MAX_AXIS:
        return 0.0
    if vals[-1] <= vals[0]:
        return 0.0
    if vals[-1] > 65000:
        return 0.0
    steps = [vals[i + 1] - vals[i] for i in range(n - 1)]
    if any(s <= 0 for s in steps):
        return 0.0
    # suite 1,2,3,4… = souvent un tableau d'index / d'adresses, pas un axe
    if all(s == 1 for s in steps) and vals[0] > 32:
        return 0.0
    mean_s = sum(steps) / len(steps)
    var = sum((s - mean_s) ** 2 for s in steps) / len(steps)
    cv = (var ** 0.5) / mean_s if mean_s else 99.0
    score = 0.15
    if vals[0] == 0:
        score += 0.25
    elif vals[0] < mean_s * 2:
        score += 0.08
    if cv < 0.12:
        score += 0.45
    elif cv < 0.35:
        score += 0.25
    elif cv < 0.7:
        score += 0.08
    else:
        return 0.0
    # multiples « ronds » (rpm, mm3, °C × 10…)
    for mod in (10, 16, 25, 50, 100, 256):
        if all(v % mod == 0 for v in vals):
            score += 0.12
            break
    if vals[-1] < 8:
        score -= 0.2
    return max(0.0, min(1.0, score))


def _smoothness(grid):
    """1 = parfaitement lisse, 0 = bruit. Moyenne des |Δ| voisins / étendue."""
    rows = len(grid)
    if not rows:
        return 0.0
    cols = len(grid[0])
    vals = [v for row in grid for v in row]
    mn, mx = min(vals), max(vals)
    rng = mx - mn
    if rng <= 0:
        return 0.15  # constante : possible (limiteur) mais peu informatif
    acc = n = 0
    for r in range(rows):
        for c in range(cols):
            v = grid[r][c]
            if c + 1 < cols:
                acc += abs(v - grid[r][c + 1])
                n += 1
            if r + 1 < rows:
                acc += abs(v - grid[r + 1][c])
                n += 1
    if not n:
        return 0.0
    mean_d = acc / n
    ratio = mean_d / rng
    if ratio > 0.55:
        return 0.0  # bruit
    return max(0.0, min(1.0, 1.0 - ratio * 1.6))


def _collect_axes(data, bits=16, be=False):
    bps = bits // 8
    n = len(data)
    axes = []
    i = 0
    while i + bps * MIN_AXIS <= n:
        if _is_pad_window(data, i):
            i += 64
            continue
        vals = []
        j = i
        prev = None
        while j + bps <= n and len(vals) < MAX_AXIS:
            v = _read(data, j, bits, be)
            if prev is not None and v <= prev:
                break
            vals.append(v)
            prev = v
            j += bps
        if len(vals) >= MIN_AXIS:
            q = axis_quality(vals)
            if q >= 0.35:
                axes.append({
                    "off": i, "n": len(vals), "vals": vals, "q": q, "end": j,
                })
        i += bps
    return axes


def _decode_grid(data, off, rows, cols, bits, be):
    bps = bits // 8
    grid = []
    p = off
    for _r in range(rows):
        row = []
        for _c in range(cols):
            if p + bps > len(data):
                return None
            row.append(_read(data, p, bits, be))
            p += bps
        grid.append(row)
    return grid


def _pack(kind, bits, be, x_off, y_off, data_off, x, y, grid, score, extra=None):
    rows = len(grid)
    cols = len(grid[0]) if grid else 0
    flat = [v for row in grid for v in row]
    bps = bits // 8
    end = data_off + rows * cols * bps
    rec = {
        "kind": kind,
        "bits": bits,
        "endian": "be" if be else "le",
        "rows": rows,
        "cols": cols,
        "axis_x_off": x_off,
        "axis_y_off": y_off,
        "data_off": data_off,
        "offset": x_off if x_off is not None else data_off,
        "end": end,
        "x": x,
        "y": y,
        "zmin": min(flat) if flat else 0,
        "zmax": max(flat) if flat else 0,
        "score": round(min(1.0, score), 3),
        "bytes": end - (x_off if x_off is not None else data_off),
    }
    if extra:
        rec.update(extra)
    return rec


def find_maps(data, bits=16, be=False, max_maps=MAX_MAPS):
    """Retourne une liste de cartes (1d/2d) triée par score, NMS inclus."""
    hdr = headers.detect(data)
    body = hdr["body"]
    header_len = hdr["header_len"]
    axes = _collect_axes(body, bits=bits, be=be)
    by_off = {}
    for a in axes:
        # on garde le plus long / meilleur si collision d'offset
        prev = by_off.get(a["off"])
        if prev is None or a["n"] > prev["n"] or (a["n"] == prev["n"] and a["q"] > prev["q"]):
            by_off[a["off"]] = a

    maps = []
    used_1d_starts = set()
    bps = bits // 8

    for a in axes:
        cols = a["n"]
        if cols < 4:
            continue
        for rows in TYPICAL_DIMS:
            if rows < 6 or cols < 4:
                continue
            y_off = a["end"]
            yvals = _read_n(body, y_off, rows, bits, be)
            if not yvals:
                continue
            qy = axis_quality(yvals)
            if qy < 0.35:
                continue
            if a["vals"][0] > 256 and yvals[0] > 256:
                continue
            data_off = y_off + rows * bps
            need = rows * cols * bps
            if data_off + need > len(body):
                continue
            grid = _decode_grid(body, data_off, rows, cols, bits, be)
            if grid is None:
                continue
            sm = _smoothness(grid)
            if sm < 0.28:
                continue
            flat = [v for row in grid for v in row]
            zmin, zmax = min(flat), max(flat)
            if zmax == zmin:
                continue
            pad = sum(1 for v in flat if v in (0, 0xFFFF)) / len(flat)
            if pad > 0.65:
                continue
            if zmax - zmin > 50000 and sm < 0.55:
                continue
            score = 0.25 * a["q"] + 0.25 * qy + 0.5 * sm
            if cols in TYPICAL_DIMS and rows in TYPICAL_DIMS:
                score += 0.05
            if a["vals"][0] == 0 and yvals[0] == 0:
                score += 0.08
            rec = _pack("2d", bits, be, a["off"], y_off, data_off,
                        a["vals"], yvals, grid, score)
            rec["offset"] += header_len
            rec["axis_x_off"] += header_len
            rec["axis_y_off"] += header_len
            rec["data_off"] += header_len
            rec["end"] += header_len
            maps.append(rec)
            used_1d_starts.add(a["off"])
            used_1d_starts.add(y_off)

    # 1D : axe suivi de N valeurs qui ne sont PAS un 2e axe
    bps = bits // 8
    for a in axes:
        if a["off"] in used_1d_starts:
            continue
        n = a["n"]
        if n < 6 or a["q"] < 0.5:
            continue
        data_off = a["end"]
        if data_off + n * bps > len(body):
            continue
        if data_off in by_off:
            continue
        z = _read_n(body, data_off, n, bits, be)
        if not z:
            continue
        if axis_quality(z) >= 0.45:
            continue  # c'est un axe, pas une courbe
        rng = max(z) - min(z)
        if rng < 8:
            continue
        # un dump aléatoire produit des « courbes » 16 bits qui saturent
        if rng > 40000:
            continue
        acc = sum(abs(z[i] - z[i + 1]) for i in range(n - 1)) / (n - 1)
        sm = max(0.0, 1.0 - (acc / rng) * 1.4) if rng else 0
        if sm < 0.35:
            continue
        grid = [z]  # 1 × n
        rec = _pack("1d", bits, be, a["off"], None, data_off,
                    a["vals"], None, grid, 0.2 * a["q"] + 0.5 * sm)
        rec["rows"] = 1
        rec["cols"] = n
        rec["offset"] += header_len
        rec["axis_x_off"] += header_len
        rec["data_off"] += header_len
        rec["end"] += header_len
        maps.append(rec)

    maps.sort(key=lambda m: (-m["score"], -(m["rows"] * m["cols"]), m["offset"]))
    maps = _nms(maps)
    return {
        "header_len": header_len,
        "header_tool": hdr.get("tool"),
        "body_size": hdr["body_size"],
        "maps": maps[:max_maps],
        "scanned": True,
        "bits": bits,
        "endian": "be" if be else "le",
    }


def _nms(maps):
    kept, used = [], []
    for m in maps:
        a, b = m["offset"], m["end"]
        if any(not (b <= u or a >= v) for u, v in used):
            continue
        kept.append(m)
        used.append((a, b))
    return kept


def _guess_at(body, hlen, body_off, cells, bits, be):
    bps = bits // 8
    best = None
    for cols in TYPICAL_DIMS:
        if cells % cols:
            continue
        rows = cells // cols
        if rows < 4 or rows > MAX_AXIS:
            continue
        y_off = body_off - rows * bps
        x_off = y_off - cols * bps
        score = 0.2
        xvals = yvals = None
        if x_off >= 0:
            xv = _read_n(body, x_off, cols, bits, be)
            yv = _read_n(body, y_off, rows, bits, be)
            if xv and yv:
                qx, qy = axis_quality(xv), axis_quality(yv)
                if qx >= 0.35 and qy >= 0.35:
                    score = 0.4 + 0.3 * qx + 0.3 * qy
                    xvals, yvals = xv, yv
        grid = _decode_grid(body, body_off, rows, cols, bits, be)
        if grid is None:
            continue
        sm = _smoothness(grid)
        score += 0.35 * sm
        score -= abs(rows - cols) * 0.005
        if xvals is None:
            score *= 0.5  # sans axes, moins convaincant
        if best is None or score > best["score"]:
            rec = _pack("2d", bits, be,
                        x_off if xvals is not None else None,
                        y_off if yvals is not None else None,
                        body_off, xvals, yvals, grid, score,
                        extra={"from_zone": True})
            for k in ("offset", "axis_x_off", "axis_y_off", "data_off", "end"):
                if rec.get(k) is not None:
                    rec[k] += hlen
            if rec["axis_x_off"] is None:
                rec["offset"] = rec["data_off"]
            best = rec
    return best


def guess_from_zone(data, off, length, bits=16, be=False):
    """À partir d'une zone de diff (souvent la table Z seule), retrouve
    les axes collés juste avant et propose rows×cols.

    La zone peut être impaire (dernier octet 16 bits inchangé) : on aligne.
    """
    hdr = headers.detect(data)
    body = hdr["body"]
    hlen = hdr["header_len"]
    body_off = max(0, off - hlen)
    candidates = []

    off16 = body_off - (body_off % 2)
    len16 = length + (body_off - off16)
    if len16 % 2:
        len16 += 1
    cells16 = len16 // 2
    for ccount in dict.fromkeys((cells16, cells16 + 1, cells16 - 1)):
        if ccount >= 8:
            rec = _guess_at(body, hlen, off16, ccount, 16, be)
            if rec:
                candidates.append(rec)

    with_axes = [c for c in candidates if c.get("axis_x_off") is not None]
    pool = with_axes or candidates
    if pool:
        return max(pool, key=lambda r: r["score"])

    if length >= 16:
        rec8 = _guess_at(body, hlen, body_off, length, 8, be)
        if rec8:
            return rec8
    return None


def decode_map(data, spec):
    """Relit X/Y/Z à partir d'une spec (offset/rows/cols/bits/endian).
    Permet le réglage manuel dans l'UI."""
    bits = int(spec.get("bits") or 16)
    be = (spec.get("endian") or "le") == "be"
    rows = int(spec.get("rows") or 1)
    cols = int(spec.get("cols") or 1)
    data_off = spec.get("data_off")
    if data_off is None:
        data_off = int(spec.get("offset") or 0)
    x_off = spec.get("axis_x_off")
    y_off = spec.get("axis_y_off")
    x = _read_n(data, x_off, cols, bits, be) if x_off is not None else None
    y = _read_n(data, y_off, rows, bits, be) if y_off is not None else None
    grid = _decode_grid(data, data_off, rows, cols, bits, be)
    if grid is None:
        return None
    flat = [v for row in grid for v in row]
    return {
        "x": x, "y": y, "z": grid,
        "zmin": min(flat), "zmax": max(flat),
        "rows": rows, "cols": cols,
        "bits": bits, "endian": "be" if be else "le",
        "data_off": data_off,
        "axis_x_off": x_off, "axis_y_off": y_off,
    }


# --- helpers de construction (démo / tests) ---------------------------------

def pack_u16_le(vals):
    out = bytearray()
    for v in vals:
        v = int(v) & 0xFFFF
        out.append(v & 0xFF)
        out.append((v >> 8) & 0xFF)
    return bytes(out)


def build_2d(x, y, z):
    """z = liste de lignes (rows × cols)."""
    buf = pack_u16_le(x) + pack_u16_le(y)
    for row in z:
        buf += pack_u16_le(row)
    return bytes(buf)


def build_1d(x, z):
    return pack_u16_le(x) + pack_u16_le(z)


# --- export CSV / facteur manuel (pas de Damos) ----------------------------

def apply_factor(val, factor=1.0, offset=0.0):
    """physique = brut × facteur + offset. Facteur 1, offset 0 = brut."""
    try:
        f = float(factor)
    except (TypeError, ValueError):
        f = 1.0
    try:
        o = float(offset)
    except (TypeError, ValueError):
        o = 0.0
    if val is None:
        return None
    return val * f + o


def fmt_value(val, decimals=None):
    if val is None:
        return ""
    if decimals is not None:
        return f"{val:.{int(decimals)}f}"
    if abs(val - round(val)) < 1e-9:
        return str(int(round(val)))
    s = f"{val:.6f}".rstrip("0").rstrip(".")
    return s


def to_csv(decoded, factor=1.0, offset=0.0, unit="", sep=";",
           decimals=None, title="", other=None):
    """CSV Excel FR (séparateur ';') d'une carte décodée.

    `decoded` = sortie de decode_map (x, y, z).
    `other` = autre decode_map (solution) → sections original / solution / delta.
    Valeurs Z transformées par facteur/offset ; axes laissés bruts.
    """
    def phys_grid(grid):
        return [[apply_factor(v, factor, offset) for v in row] for row in (grid or [])]

    def emit(grid, x, y):
        rows = []
        cols = len(grid[0]) if grid else 0
        header = [""] + [fmt_value(x[c] if x and c < len(x) else c, None) for c in range(cols)]
        rows.append(sep.join(header))
        for r, row in enumerate(grid):
            yv = y[r] if y and r < len(y) else r
            cells = [fmt_value(yv, None)] + [fmt_value(v, decimals) for v in row]
            rows.append(sep.join(cells))
        return rows

    lines = []
    z = phys_grid(decoded.get("z") or [])
    x, y = decoded.get("x"), decoded.get("y")
    rows_n = decoded.get("rows") or len(z)
    cols_n = decoded.get("cols") or (len(z[0]) if z else 0)
    meta = [title or "Carto Matcher 2D"]
    off = decoded.get("data_off")
    if off is not None:
        meta.append(f"data 0x{int(off):x}")
    meta.append(f"{rows_n}x{cols_n}")
    meta.append(f"facteur={factor}")
    meta.append(f"offset={offset}")
    if unit:
        meta.append(f"unite={unit}")
    meta.append("valeurs physiques" if float(factor) != 1.0 or float(offset) != 0.0
                else "valeurs brutes")
    lines.append("# " + " · ".join(str(m) for m in meta if m))
    if other is None:
        lines.extend(emit(z, x, y))
    else:
        z2 = phys_grid(other.get("z") or [])
        lines.append("# original")
        lines.extend(emit(z, x, y))
        lines.append("")
        lines.append("# solution")
        lines.extend(emit(z2, other.get("x") or x, other.get("y") or y))
        delta = []
        for r, row in enumerate(z2):
            drow = []
            for c, v in enumerate(row):
                o = z[r][c] if r < len(z) and c < len(z[r]) else None
                drow.append(None if v is None or o is None else (v - o))
            delta.append(drow)
        lines.append("")
        lines.append("# delta (solution - original)")
        lines.extend(emit(delta, other.get("x") or x, other.get("y") or y))
    return "\n".join(lines) + "\n"


def maps_to_csv(map_list, data, other=None, factor=1.0, offset=0.0,
                unit="", sep=";", decimals=None, changed_only=False):
    """Concatène plusieurs cartes (séparées par une ligne vide)."""
    chunks = []
    for i, spec in enumerate(map_list or []):
        if changed_only and not spec.get("changed"):
            continue
        d = decode_map(data, spec)
        if not d:
            continue
        d2 = decode_map(other, spec) if other else None
        title = f"carte {i + 1} ({spec.get('kind') or '2d'} {d['rows']}x{d['cols']})"
        chunks.append(to_csv(d, factor=factor, offset=offset, unit=unit,
                             sep=sep, decimals=decimals, title=title, other=d2))
    return "\n".join(chunks)
