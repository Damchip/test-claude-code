"""Pack de sortie atelier : BIN + rapport + nommage métier."""
import io
import os
import re
import time
import zipfile
from datetime import datetime

from . import metadata

SLUG = {
    "E85 / Flexfuel": "E85",
    "Stage 1": "Stage1",
    "Stage 2": "Stage2",
    "DPF/FAP off": "FAP",
    "EGR off": "EGR",
    "AdBlue/SCR off": "SCR",
    "DTC off": "DTC",
    "Decat": "Decat",
    "Pop & Bang": "PnB",
    "Vmax off": "Vmax",
    "Launch / Hardcut": "LC",
    "Boîte / TCU": "TCU",
}


def presta_slug(label):
    atoms = metadata.ordered_atoms([label] if isinstance(label, str) else (label or []))
    parts = [SLUG.get(a, re.sub(r"[^A-Za-z0-9]+", "", a)[:12] or "Presta") for a in atoms]
    return "_".join(p for p in parts if p) or "PATCH"


def _safe_token(s, fallback="fichier"):
    s = re.sub(r"[^\w.\-]+", "_", (s or "").strip(), flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("._")
    return s[:40] or fallback


def pack_basename(plate="", vin="", label="", client_name=""):
    """AA-123-BB_Stage1_FAP_E85  (immat > VIN > nom de fichier)."""
    plate = (plate or "").strip().upper()
    vin = (vin or "").strip().upper()
    if plate:
        head = plate.replace(" ", "")
    elif len(vin) >= 8:
        head = vin[-8:]
    else:
        base = os.path.splitext(os.path.basename(client_name or ""))[0]
        head = _safe_token(base, "DUMP")
    return f"{head}_{presta_slug(label)}"


def ready_flag(res):
    if res.get("ready_to_flash") is True:
        return True
    ck = res.get("checksum") or {}
    if res.get("rsa") or ck.get("rsa") or res.get("rsa_skipped"):
        return False
    if res.get("conflicts"):
        return False
    if res.get("verdict") != "propre":
        return False
    return bool(ck.get("ready"))


def report_text(meta):
    """Rapport atelier en texte (UTF-8)."""
    lines = [
        "CARTO MATCHER — pack de sortie",
        f"Généré : {meta.get('date') or ''}",
        "",
        f"Client / fichier : {meta.get('client_name') or '—'}",
        f"Immat : {meta.get('plate') or '—'}",
        f"VIN   : {meta.get('vin') or '—'}",
        f"Véhicule : {meta.get('vehicle') or '—'}",
        f"Dossier  : {meta.get('dossier_id') or '—'}",
        "",
        f"Prestations : {meta.get('label') or '—'}",
        f"Plateforme  : {meta.get('platform') or '—'}",
        f"Fiches      : {meta.get('ids_txt') or '—'}",
        "",
        f"Verdict : {meta.get('verdict') or '—'}",
        f"Détail  : {meta.get('verdict_text') or ''}",
        f"Stats   : {meta.get('stats') or ''}",
        f"Checksum : {meta.get('ck_status') or '—'} — {meta.get('ck_note') or ''}",
        f"RSA      : {'oui' if meta.get('rsa') else 'non'}",
        f"Prêt à flasher : {'NON' if not meta.get('ready') else 'OUI (checksum clair, pas de RSA)'}",
        "",
    ]
    if meta.get("conflicts"):
        lines.append("Conflits (octets non écrits) :")
        for c in meta["conflicts"][:30]:
            lines.append(
                f"  - 0x{int(c.get('a_off') or 0):X} "
                f"{c.get('a_type') or ''} ↔ {c.get('b_type') or ''} "
                f"({c.get('overlap') or 0} o)"
            )
        lines.append("")
    if meta.get("fiches"):
        lines.append("Fiches combinées :")
        for f in meta["fiches"]:
            lines.append(
                f"  - #{f.get('id')} {f.get('solution_type') or ''} "
                f"[{f.get('verdict') or f.get('error') or '—'}] "
                f"{f.get('matched') or 0} zone(s) OK"
            )
        lines.append("")
    zones = meta.get("zones") or []
    if zones:
        lines.append(f"Zones ({len(zones)}) :")
        for z in zones[:80]:
            etat = "RSA" if z.get("rsa") else ("OK" if z.get("ok") else "DIFF")
            lines.append(
                f"  #{int(z.get('i') or 0)+1}  0x{int(z.get('off') or 0):X}  "
                f"{z.get('len')} o  {etat}  {z.get('localLabel') or ''}"
            )
        if len(zones) > 80:
            lines.append(f"  … {len(zones) - 80} de plus")
        lines.append("")
    lines.append("Le fichier source n'a pas été modifié.")
    lines.append("Vérifier le BIN dans l'outil de flash / WinOLS avant écriture ECU.")
    return "\n".join(lines) + "\n"


def report_html(meta):
    def esc(s):
        return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    ready = meta.get("ready")
    vcls = "#e7f7ee" if ready else "#fdf4e0"
    rows = ""
    for z in (meta.get("zones") or [])[:80]:
        etat = "RSA" if z.get("rsa") else ("OK" if z.get("ok") else "DIFF")
        rows += (
            f"<tr><td>{int(z.get('i') or 0)+1}</td>"
            f"<td>0x{int(z.get('off') or 0):X}</td><td>{z.get('len')}</td>"
            f"<td>{etat}</td><td>{esc(z.get('localLabel'))}</td></tr>"
        )
    conf = ""
    for c in (meta.get("conflicts") or [])[:30]:
        conf += (
            f"<li>0x{int(c.get('a_off') or 0):X} "
            f"{esc(c.get('a_type'))} ↔ {esc(c.get('b_type'))}</li>"
        )
    fiches = ""
    for f in meta.get("fiches") or []:
        fiches += (
            f"<li>#{f.get('id')} {esc(f.get('solution_type'))} "
            f"— {esc(f.get('verdict') or f.get('error'))}</li>"
        )
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Pack {esc(meta.get('basename'))}</title>
<style>
body{{font-family:Arial,sans-serif;color:#111;margin:28px;font-size:13px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:14px;margin:16px 0 6px}}
.muted{{color:#666}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:4px 7px;text-align:left}}
th{{background:#f2f2f2}} .box{{padding:8px 10px;border-radius:6px;background:{vcls}}}
.warn{{color:#8a4b00}}
</style></head><body>
<h1>Pack de sortie — Carto Matcher</h1>
<div class="muted">{esc(meta.get('date'))}</div>
<h2>Véhicule / client</h2>
<div>{esc(meta.get('client_name'))}<br>
Immat {esc(meta.get('plate') or '—')} · VIN {esc(meta.get('vin') or '—')}<br>
{esc(meta.get('vehicle') or '')} · dossier {esc(meta.get('dossier_id') or '—')}</div>
<h2>Prestation</h2>
<div>{esc(meta.get('label'))}<br>Plateforme {esc(meta.get('platform') or '—')}
 · fiches {esc(meta.get('ids_txt') or '—')}</div>
<ul>{fiches}</ul>
<h2>Verdict</h2>
<div class="box">{esc(meta.get('verdict_text') or meta.get('verdict'))}<br>
{esc(meta.get('stats'))}<br>
Checksum : {esc(meta.get('ck_status'))} — {esc(meta.get('ck_note'))}<br>
<strong>Prêt à flasher : {'OUI' if ready else 'NON'}</strong>
{' · RSA détectée' if meta.get('rsa') else ''}</div>
{('<h2>Conflits</h2><ul>'+conf+'</ul>') if conf else ''}
<h2>Zones</h2>
<table><thead><tr><th>#</th><th>Offset</th><th>o</th><th>État</th><th>Type</th></tr></thead>
<tbody>{rows or '<tr><td colspan="5">Voir fiches combinées</td></tr>'}</tbody></table>
<p class="muted">Fichier source intact. Vérifier le BIN avant flash.</p>
</body></html>
"""


def meta_from_result(res, *, client_name="", plate="", vin="", vehicle="",
                     dossier_id=None, label="", ids=None, platform=""):
    ck = res.get("checksum") or {}
    zones = res.get("zones") or []
    if not zones:
        for f in res.get("fiches") or []:
            for z in f.get("zones") or []:
                z = dict(z)
                z["presta"] = f.get("solution_type")
                zones.append(z)
    ids = ids or res.get("ids") or []
    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "client_name": client_name,
        "plate": plate,
        "vin": vin,
        "vehicle": vehicle,
        "dossier_id": dossier_id,
        "label": label or res.get("combined_type") or "",
        "platform": platform or (res.get("fiche") or {}).get("platform") or res.get("platform") or "",
        "ids_txt": ",".join(str(i) for i in ids),
        "verdict": res.get("verdict") or "",
        "verdict_text": res.get("verdict_text") or "",
        "stats": res.get("stats") or "",
        "ck_status": ck.get("status") or "",
        "ck_note": ck.get("note") or "",
        "rsa": bool(res.get("rsa") or ck.get("rsa")),
        "ready": ready_flag(res),
        "conflicts": res.get("conflicts") or [],
        "fiches": res.get("fiches") or [],
        "zones": zones,
        "basename": "",
    }


def write_files(dest_dir, basename, patched, meta):
    os.makedirs(dest_dir, exist_ok=True)
    meta = dict(meta)
    meta["basename"] = basename
    bin_name = basename + ".bin"
    txt_name = basename + "_rapport.txt"
    html_name = basename + "_rapport.html"
    paths = {}
    bin_path = os.path.join(dest_dir, bin_name)
    with open(bin_path, "wb") as fh:
        fh.write(patched)
    paths["bin"] = bin_path
    txt_path = os.path.join(dest_dir, txt_name)
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(report_text(meta))
    paths["txt"] = txt_path
    html_path = os.path.join(dest_dir, html_name)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(report_html(meta))
    paths["html"] = html_path
    return paths


def zip_pack(paths, basename):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, path in paths.items():
            zf.write(path, arcname=os.path.basename(path))
    buf.seek(0)
    return buf, basename + ".zip"


def pack_dir(db_path, dossier_id=None):
    root = os.path.dirname(os.path.abspath(db_path))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if dossier_id:
        dest = os.path.join(root, "dossiers", f"{int(dossier_id):06d}", "livraisons", stamp)
    else:
        dest = os.path.join(root, "livraisons", stamp)
    return dest
