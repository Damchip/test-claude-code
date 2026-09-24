"""
Empreintes de fichiers ECU — 100% local, sans dépendance externe.

On calcule trois choses pour chaque binaire :
  - sha256  : identité exacte (fichier strictement identique)
  - size    : taille en octets
  - minhash : signature de similarité (pour retrouver un fichier "presque
              identique", typiquement le même stock avec une carto modifiée
              sur quelques régions mémoire)

La signature MinHash a une taille fixe (NUM_HASHES entiers, ~1 Ko) quelle que
soit la taille du fichier, donc elle se stocke sans problème dans SQLite et se
compare en temps constant.

v2 (1.34) : les blocs de padding (≥90 % de 00 ou FF) sont ignorés, sinon deux
EDC17 différentes bourrées de 0xFF ressortent « binaires similaires ».
Les anciennes signatures (v1, padding inclus) restent comparables : le moteur
calcule les deux versions à l'entrée.
"""

import hashlib
import random

BLOCK_SIZE = 64
NUM_HASHES = 64
MINHASH_VERSION = 2
_PRIME = (1 << 61) - 1

_rng = random.Random(0xC0FFEE_1979)
_A = [_rng.randrange(1, _PRIME) for _ in range(NUM_HASHES)]
_B = [_rng.randrange(0, _PRIME) for _ in range(NUM_HASHES)]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_padding(block: bytes) -> bool:
    n = len(block)
    if n == 0:
        return True
    return max(block.count(0), block.count(0xFF)) / n >= 0.90


def _block_ints(data: bytes, skip_padding: bool):
    seen = set()
    n = len(data)
    for off in range(0, n, BLOCK_SIZE):
        block = data[off:off + BLOCK_SIZE]
        if skip_padding and _is_padding(block):
            continue
        h = hashlib.blake2b(block, digest_size=8).digest()
        seen.add(int.from_bytes(h, "big"))
    return seen


def minhash_signature(data: bytes, skip_padding: bool = True):
    """Signature MinHash (liste de NUM_HASHES entiers)."""
    blocks = _block_ints(data, skip_padding)
    if not blocks:
        blocks = _block_ints(data, skip_padding=False)
    sig = [_PRIME] * NUM_HASHES
    A, B, P = _A, _B, _PRIME
    for x in blocks:
        for i in range(NUM_HASHES):
            h = (A[i] * x + B[i]) % P
            if h < sig[i]:
                sig[i] = h
    return sig


def jaccard(sig_a, sig_b) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    equal = sum(1 for x, y in zip(sig_a, sig_b) if x == y)
    return equal / len(sig_a)


def fingerprint(data: bytes, skip_padding: bool = True) -> dict:
    return {
        "sha256": sha256(data),
        "size": len(data),
        "minhash": minhash_signature(data, skip_padding=skip_padding),
        "minhash_ver": MINHASH_VERSION if skip_padding else 1,
    }
