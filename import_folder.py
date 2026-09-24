"""
Import en masse depuis la ligne de commande (raisonne par dossier).

    py import_folder.py "D:\\Mes solutions"
ou sans argument (IDLE / double-clic) : il demande le dossier.

Le même import est aussi disponible dans l'interface (onglet « Importer »).
Voir matcher/importer.py pour la logique (original = .ori/plus ancien,
une solution par type de prestation dans le dossier).
"""

import argparse
import sys

from matcher import importer


def parse_args():
    p = argparse.ArgumentParser(description="Import en masse de solutions (par dossier)")
    p.add_argument("folder", nargs="?", default=None)
    p.add_argument("--status", default="a_confirmer")
    p.add_argument("--type", default=None)
    p.add_argument("--ext", default=None)
    p.add_argument("--min-ko", type=int, default=16)
    p.add_argument("--max-mo", type=int, default=32)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def interactive(args):
    print("=== Import de solutions (par dossier) ===")
    folder = input("Dossier à importer (glisse-le ici ou colle le chemin) : ").strip()
    args.folder = folder.strip('"').strip("'")
    rep = input("Simulation d'abord (sans rien enregistrer) ? [O/n] : ").strip().lower()
    args.dry_run = rep in ("", "o", "oui", "y", "yes")
    args.is_interactive = True
    return args


def show(entry):
    if entry["state"] == "erreur":
        print(f"  ! {entry['folder']} — {entry.get('error')}")
        return
    print(f"  [{entry['state']}] {entry['folder']}")
    print(f"      original={entry['original']} | solution={entry['solution'] or '(aucun)'}")
    print(f"      véhicule={entry['vehicle'] or '?'} | plateforme={entry['platform'] or '?'} "
          f"| ecu={entry['ecu'] or '?'} | type={entry['type'] or '?'}")


def main():
    import os
    args = parse_args()
    if not args.folder:
        args = interactive(args)
    root = args.folder.strip('"').strip("'")
    if not os.path.isdir(root):
        print(f"Dossier introuvable : {root}")
        if getattr(args, "is_interactive", False):
            input("Appuie sur Entrée pour fermer…")
        sys.exit(1)

    exts = (set(e if e.startswith(".") else "." + e for e in args.ext.split(","))
            if args.ext else None)

    print(f"Parcours de : {root}\n")
    res = importer.scan_folder(
        root, status=args.status, type_override=args.type, exts=exts,
        min_ko=args.min_ko, max_mo=args.max_mo, dry_run=args.dry_run,
        progress=show,
    )
    c = res["counts"]
    print()
    print("Terminé (simulation, rien enregistré)" if args.dry_run else "Terminé")
    print(f"  ajoutées        : {c['added'] if not args.dry_run else 0}")
    if args.dry_run:
        print(f"  nouvelles (à venir): {c['new']}")
    print(f"  doublons ignorés: {c['dup']}")
    print(f"  dossiers vides  : {c['empty']}")
    if c["errors"]:
        print(f"  erreurs ignorées: {c['errors']}")
    print(f"  total en base   : {res['db_size']}")

    if getattr(args, "is_interactive", False):
        input("\nAppuie sur Entrée pour fermer…")


if __name__ == "__main__":
    main()
