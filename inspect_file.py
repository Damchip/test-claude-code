"""
Diagnostic : montre TOUT ce que le détecteur trouve dans un fichier réel.

À utiliser AVANT le premier vrai test, sur quelques-uns de tes fichiers, pour
vérifier que la version ECU / la plateforme sont bien repérées — et sinon
repérer à l'œil la vraie chaîne d'identification pour calibrer l'extracteur.

Utilisation :
    py inspect_file.py "D:\\chemin\\vers\\fichier.bin"
ou sans argument (depuis IDLE / double-clic) : il demande le fichier.
"""

import os
import sys

from matcher import engine, extract


def ask_path():
    p = input("Fichier à inspecter (glisse-le ici ou colle le chemin) : ").strip()
    return p.strip('"').strip("'"), True


def main():
    interactive = False
    if len(sys.argv) > 1:
        path = sys.argv[1].strip('"').strip("'")
    else:
        path, interactive = ask_path()

    if not os.path.isfile(path):
        print(f"Fichier introuvable : {path}")
        if interactive:
            input("Entrée pour fermer…")
        return

    with open(path, "rb") as f:
        data = f.read()

    info = engine.analyze(data, path)
    nm = info["name_meta"]

    print("\n" + "=" * 60)
    print(f"Fichier : {os.path.basename(path)}")
    print(f"Taille  : {len(data):,} octets   sha256 {info['sha256'][:16]}…")
    print("-" * 60)
    print("DÉTECTÉ DANS LE BINAIRE")
    print(f"  Plateforme   : {info['platform'] or '— (non trouvée)'}")
    print(f"  Fabricant    : {info['manufacturer'] or '—'}")
    print(f"  Version ECU  : {info['best_ecu_version'] or '— (non trouvée)'}")
    if info["typed_candidates"]:
        print("  Candidats identifiants :")
        for c in info["typed_candidates"]:
            print(f"     {int(c['confidence']*100):>3}%  {c['value']:<22} "
                  f"{c['type']} ({c['family']})")
    else:
        print("  Candidats identifiants : aucun")

    print("-" * 60)
    print("DÉDUIT DU NOM / CHEMIN")
    print(f"  Marque       : {nm['brand'] or '—'}")
    print(f"  Véhicule     : {nm['vehicle'] or '—'}")
    print(f"  Plateforme   : {nm['platform'] or '—'}")
    print(f"  Type solution: {nm['solution_type'] or '—'}")

    print("-" * 60)
    print("CHAÎNES ASCII LES PLUS PROBABLES (pour repérer la clé à l'œil)")
    print("  [offset]  (N)=isolée par \\x00   contenu")
    runs = extract.ascii_runs(data, min_len=4)
    # priorité : isolées par des nuls, puis les plus courtes (souvent des refs)
    runs.sort(key=lambda r: (not r["null_bounded"], len(r["s"])))
    shown = 0
    for r in runs:
        if shown >= 40:
            break
        if len(r["s"]) > 40:
            continue
        mark = "N" if r["null_bounded"] else " "
        print(f"  0x{r['start']:08X} ({mark})  {r['s']}")
        shown += 1
    if not runs:
        print("  (aucune chaîne ASCII — fichier peut-être chiffré/compressé)")

    print("=" * 60)
    print("Si la vraie version ECU apparaît ci-dessus mais n'est pas dans les")
    print("candidats détectés, envoie-moi cette ligne : j'ajoute le motif.")
    if interactive:
        input("\nEntrée pour fermer…")


if __name__ == "__main__":
    main()
