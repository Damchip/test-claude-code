"""
Livraison en un clic : prépare le fichier d'une demande à partir de la bibliothèque.

Conditions (toutes obligatoires, sinon traitement manuel dans Auto-patch) :
  - chaque prestation commandée a un « type » de bibliothèque (catalogue.py) ;
  - pas d'ouverture de boîtier au siège (intervention physique) ;
  - pour chaque type, une fiche de MÊME STOCK existe, et aucune fiche retenue
    n'apporte une prestation non commandée ;
  - le patch (seul ou combiné) est « propre », sans conflit ;
  - les checksums sont prêts et aucune signature RSA n'est détectée.

`preparer` ne modifie rien : il renvoie le fichier patché et un compte rendu.
"""
import re

import catalogue
from matcher import db as mdb, engine, patch as pmod

IGNORE = {"Origine (stock)"}


def types_voulus(demande):
    """Types de bibliothèque correspondant aux prestations, ou (None, raison)."""
    items = {p["code"]: p for p in catalogue.prestations_de(demande["categorie"])}
    types = []
    for code in demande["prestations"]:
        p = items.get(code)
        if not p or not p.get("type"):
            return None, f"« {p['nom'] if p else code} » se traite à la main."
        if p["type"] not in types:
            types.append(p["type"])
    return types, ""


def _fiches(matches, voulus):
    """Choisit des fiches même stock couvrant exactement les types voulus (glouton, sans surplus)."""
    candidates = []
    for m in matches:
        if not (m.get("exact") or m.get("same_stock") or m.get("calibration_exact")):
            continue
        atoms = [a for a in (m.get("type_atoms") or []) if a not in IGNORE]
        if atoms and set(atoms) <= set(voulus):
            candidates.append((m, set(atoms)))
    reste, ids, libelles = set(voulus), [], []
    while reste:
        best = max(candidates, key=lambda c: (len(c[1] & reste), c[0].get("score", 0)), default=None)
        if not best or not (best[1] & reste):
            return None, None, sorted(reste)
        ids.append(best[0]["id"])
        libelles.append(best[0].get("vehicle_label") or f"#{best[0]['id']}")
        reste -= best[1]
        candidates.remove(best)
    return ids, libelles, []


def preparer(demande, data, db_path):
    """Renvoie {"ok": bool, "raison": str, ...}. Si ok : "patched" (octets), "compte_rendu"."""
    if demande.get("siege"):
        return {"ok": False, "raison": "Ouverture du boîtier au siège : intervention physique."}
    voulus, raison = types_voulus(demande)
    if voulus is None:
        return {"ok": False, "raison": raison}
    result = engine.match(data, db_path, path=demande.get("fichier_nom") or "")
    ids, libelles, manquants = _fiches(result.get("matches") or [], voulus)
    if not ids:
        return {"ok": False, "raison": "Pas de fiche même stock en bibliothèque pour : " + ", ".join(manquants) + "."}
    bundles, errors = pmod.load_many(db_path, ids)
    if len(bundles) != len(ids):
        return {"ok": False, "raison": "Fichiers de la fiche introuvables sur le disque (original / solution)."}
    if len(bundles) == 1:
        b = bundles[0]
        res = pmod.build_patched(data, b["orig"], b["sol"],
                                 fiche_type=(b["fiche"] or {}).get("solution_type") or "",
                                 platform=(b["fiche"] or {}).get("ecu_platform") or "")
    else:
        res = pmod.build_combined(data, bundles)
    if res.get("error") or res.get("verdict") != "propre" or res.get("conflicts"):
        detail = {"zones_incompatibles": "zones incompatibles avec le stock",
                  "stocks_melanges": "fiches de stocks différents",
                  "taille_incompatible_client": "taille de fichier inattendue (en-tête d'outil ?)"}.get(
            res.get("error"), res.get("verdict") or res.get("error") or "?")
        return {"ok": False, "raison": f"Patch non « propre » ({detail}) : à vérifier dans Auto-patch."}
    ck = res.get("checksum") or {}
    if not res.get("ready_to_flash"):
        return {"ok": False, "raison": "Checksums non prêts ou signature RSA : à finir à la main. "
                                       + (ck.get("note") or "")}
    return {"ok": True, "raison": "", "patched": res["patched"], "ids": ids,
            "compte_rendu": {"fiches": libelles, "types": voulus, "checksum": ck.get("status") or "",
                             "checksum_note": ck.get("note") or "",
                             "octets": res.get("changed_bytes") or res.get("applied_bytes") or 0}}


def nom_fichier(demande):
    base = re.sub(r"[^\w.-]+", "_", demande["fichier_nom"].rsplit(".", 1)[0])[:60] or "fichier"
    types = "_".join(re.sub(r"[^\w]+", "", t) for t in types_voulus(demande)[0] or [])
    return f"{base}_{types or 'mod'}.bin"


def journaliser(db_path, demande, prep, auteur=""):
    """Trace dans l'historique des traitements de l'outil (onglet Tableau de bord)."""
    try:
        mdb.add_job(db_path, client_name=f"{demande['numero']} · {demande.get('societe', '')}",
                    solution_id=prep["ids"][0], solution_label=" + ".join(prep["compte_rendu"]["types"]),
                    verdict="propre (fileservice, un clic)", zones=0, applied=1,
                    note=f"Livré automatiquement {('par ' + auteur) if auteur else ''}".strip())
    except Exception:
        pass
