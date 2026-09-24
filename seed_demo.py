"""
Remplit une BASE DE DÉMO avec quelques solutions synthétiques (différentes
familles d'ECU) + génère un fichier "client" de test.

    python seed_demo.py

Par défaut écrit dans data/demo.db — JAMAIS dans data/solutions.db, pour ne
pas écraser une bibliothèque atelier.
"""

import os
import random
import sys

from matcher import db, extract, fingerprint, maps

DATA = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA, exist_ok=True)
# Isolé de la vraie base. On peut forcer via CARTO_DB, mais on refuse
# d'écraser une base qui contient déjà des fiches.
DB = os.environ.get("CARTO_DB") or os.path.join(DATA, "demo.db")
SEED_BINS = os.path.join(DATA, "_seed_bins")

MAP_1D_OFF = 0x4800
MAP_2D_OFF = 0x5000
MAP_DPF_OFF = 0x7600


def _map_2d(boost=1.0):
    x = [i * 800 for i in range(16)]
    y = [i * 200 for i in range(12)]
    z = []
    for r in range(12):
        row = []
        for c in range(16):
            v = 400 + c * 25 + r * 35 + (c * r) // 4
            if boost != 1.0:
                v = int(v * boost)
            row.append(min(v, 65535))
        z.append(row)
    return x, y, z


def _map_1d(boost=1.0):
    x = [i * 500 for i in range(8)]
    z = [int(v * boost) for v in (100, 180, 250, 310, 360, 400, 430, 450)]
    return x, z


def _map_dpf(zero=False):
    x = [i * 400 for i in range(8)]
    y = [i * 50 for i in range(8)]
    if zero:
        z = [[0] * 8 for _ in range(8)]
    else:
        z = [[800 + r * 20 + c * 15 for c in range(8)] for r in range(8)]
    return x, y, z


def plant_maps(buf, *, stage_boost=1.0, dpf_off=False):
    x, y, z = _map_2d(boost=stage_boost)
    blob = maps.build_2d(x, y, z)
    buf[MAP_2D_OFF:MAP_2D_OFF + len(blob)] = blob
    x1, z1 = _map_1d(boost=stage_boost if stage_boost != 1.0 else 1.0)
    blob1 = maps.build_1d(x1, z1)
    buf[MAP_1D_OFF:MAP_1D_OFF + len(blob1)] = blob1
    xd, yd, zd = _map_dpf(zero=dpf_off)
    blobd = maps.build_2d(xd, yd, zd)
    buf[MAP_DPF_OFF:MAP_DPF_OFF + len(blobd)] = blobd
    return buf


def make_binary(seed, ecu_version, platform, size=512 * 1024):
    """Binaire pseudo-ECU contenant une calibration et un nom de plateforme,
    isolés par des octets nuls comme dans un vrai dump."""
    rng = random.Random(seed)
    data = bytearray(rng.getrandbits(8) for _ in range(size))

    def place(off, text):
        b = text.encode("ascii")
        data[off - 4:off] = b"\x00" * 4
        data[off:off + len(b)] = b
        data[off + len(b):off + len(b) + 4] = b"\x00" * 4

    place(4096, ecu_version)
    place(8192, platform)
    plant_maps(data)
    return bytes(data)


if os.path.exists(DB):
    try:
        n = db.count(DB)
    except Exception:
        n = 0
    if n > 0:
        print(f"Refus : {n} fiche(s) déjà dans {os.path.abspath(DB)}")
        print("seed_demo.py n'écrase plus une base existante.")
        print("Pour une démo isolée :  CARTO_DB=data/demo.db  (et supprime ce fichier si tu veux la régénérer).")
        sys.exit(1)
    os.remove(DB)

db.init_db(DB)
os.makedirs(SEED_BINS, exist_ok=True)

samples = [
    # seed, calibration,    plateforme,   véhicule,             type,               statut
    (101, "0281020088",  "EDC17CP14",  "Audi A3 2.0 TDI CR",  "Stage 1",          "validee_client"),
    (202, "1037551299",  "EDC17C41",   "BMW 320d E90",        "Stage 1 + EGR off","testee"),
    (303, "8V0906259",   "SIMOS18.1",  "VW Golf 7 1.4 TSI",   "Stage 1",          "a_confirmer"),
    (404, "55256829",    "MJD8F3",     "Fiat 500 1.3 MJ",     "DPF off",          "testee"),
]

for seed, ecu, plat, label, stype, status in samples:
    raw = make_binary(seed, ecu, plat)
    sol = bytearray(raw)
    if seed == 101:
        plant_maps(sol, stage_boost=1.08)
    else:
        for off in range(20000, 20400):
            sol[off] ^= 0x5A
    sol = bytes(sol)
    ori_path = os.path.join(SEED_BINS, f"{seed}_ORI.bin")
    sol_path = os.path.join(SEED_BINS, f"{seed}_SOL.bin")
    with open(ori_path, "wb") as f:
        f.write(raw)
    with open(sol_path, "wb") as f:
        f.write(sol)
    fp = fingerprint.fingerprint(raw)
    ex = extract.extract(raw)
    db.add_solution(DB, ecu_version=ecu, ecu_platform=plat,
                    manufacturer=ex["manufacturer"] or "",
                    vehicle_label=label, solution_type=stype, tested_status=status,
                    stock_sha256=fp["sha256"], stock_size=fp["size"],
                    minhash=fp["minhash"], minhash_ver=2,
                    original_file=ori_path, solution_file=sol_path)
    print(f"+ {label:22s} {ecu:12s} {plat:12s} {stype:16s} -> {ex['manufacturer']}")
    # Démo multi-prestations : le même stock Audi a aussi un FAP off.
    if seed == 101:
        dpf = bytearray(raw)
        plant_maps(dpf, dpf_off=True)
        dpf_path = os.path.join(SEED_BINS, "101_DPF.bin")
        with open(dpf_path, "wb") as f:
            f.write(dpf)
        db.add_solution(DB, ecu_version=ecu, ecu_platform=plat,
                        manufacturer=ex["manufacturer"] or "",
                        vehicle_label=label, solution_type="DPF/FAP off",
                        tested_status="testee",
                        stock_sha256=fp["sha256"], stock_size=fp["size"],
                        minhash=fp["minhash"], minhash_ver=2,
                        original_file=ori_path, solution_file=dpf_path)
        print(f"+ {label:22s} {ecu:12s} {plat:12s} {'DPF/FAP off':16s} -> (même stock)")

# Fichier client : stock Audi (match exact + auto-patch propre)
client = make_binary(101, "0281020088", "EDC17CP14")
with open(os.path.join(DATA, "demo_client.bin"), "wb") as f:
    f.write(client)
# Même dump lu au KESS (+0x400) — matching sur le corps, patch avec header recollé
kess = bytearray(b"KESS") + bytearray(0x400 - 4) + client
with open(os.path.join(DATA, "demo_client_kess.bin"), "wb") as f:
    f.write(kess)

print(f"\nBase de démo : {db.count(DB)} solutions → {os.path.abspath(DB)}")
print("Fichier de test : data/demo_client.bin (stock Audi, match exact)")
print("Header KESS    : data/demo_client_kess.bin (même corps + 0x400)")
print("Pour l'utiliser :  set CARTO_DB=data\\demo.db   puis lance Carto Matcher.")
