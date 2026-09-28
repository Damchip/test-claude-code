"""
Tableau de bord chiffré de l'atelier (outil interne, onglet Fileservice → Statistiques).

Tout est calculé depuis data/fileservice.db :
  - par mois : chiffre d'affaires HT (factures), demandes reçues / refusées, crédits consommés,
    délai moyen de livraison ;
  - sur la période : prestations les plus demandées, meilleurs clients (crédits consommés),
    part de l'express, délai médian.
Les demandes refusées (remboursées) ne comptent ni dans les crédits consommés ni dans les classements.
"""
import datetime as dt
import json
import statistics
from collections import Counter, defaultdict

from comptes import connect

EXCLUS = {"Traitement express (prioritaire)"}   # supplément, pas une prestation


def _mois(n, aujourd_hui=None):
    d = (aujourd_hui or dt.date.today()).replace(day=1)
    out = []
    for _ in range(n):
        out.append(d.strftime("%Y-%m"))
        d = (d - dt.timedelta(days=1)).replace(day=1)
    return out[::-1]


def calculer(db_path, mois=12, aujourd_hui=None):
    liste = _mois(max(1, min(int(mois), 36)), aujourd_hui)
    debut = liste[0] + "-01"
    par_mois = {m: {"mois": m, "ca_ht": 0.0, "factures": 0, "demandes": 0, "refusees": 0, "express": 0,
                    "credits": 0, "delais": []} for m in liste}
    prestations = Counter()
    credits_prestation = Counter()
    clients = defaultdict(lambda: {"credits": 0, "demandes": 0})
    delais = []
    with connect(db_path) as con:
        for r in con.execute("SELECT substr(date, 1, 7) m, SUM(ht) ht, COUNT(*) n FROM factures WHERE date >= ? GROUP BY m",
                             (debut,)):
            if r["m"] in par_mois:
                par_mois[r["m"]]["ca_ht"] = round(r["ht"] or 0, 2)
                par_mois[r["m"]]["factures"] = r["n"]
        rows = con.execute(
            "SELECT d.cree_le, d.livre_le, d.statut, d.rembourse, d.total, d.lignes, d.express, d.client_id, c.societe"
            " FROM demandes d JOIN clients c ON c.id = d.client_id WHERE d.cree_le >= ?", (debut,)).fetchall()
    for r in rows:
        m = par_mois.get(r["cree_le"][:7])
        if not m:
            continue
        m["demandes"] += 1
        m["express"] += int(bool(r["express"]))
        if r["rembourse"]:
            m["refusees"] += 1
            continue
        m["credits"] += r["total"]
        cl = clients[r["client_id"]]
        cl["societe"], cl["credits"], cl["demandes"] = r["societe"], cl["credits"] + r["total"], cl["demandes"] + 1
        for l in json.loads(r["lignes"] or "[]"):
            if l.get("credits", 0) > 0 and l.get("nom") not in EXCLUS and not l.get("nom", "").startswith("Garantie"):
                prestations[l["nom"]] += 1
                credits_prestation[l["nom"]] += l["credits"]
        if r["livre_le"]:
            try:
                h = (dt.datetime.strptime(r["livre_le"], "%Y-%m-%d %H:%M:%S")
                     - dt.datetime.strptime(r["cree_le"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
            except ValueError:
                continue
            m["delais"].append(h)
            delais.append(h)
    series = []
    for m in liste:
        x = par_mois[m]
        d = x.pop("delais")
        x["delai_moyen_h"] = round(sum(d) / len(d), 1) if d else None
        series.append(x)
    total_dem = sum(x["demandes"] for x in series)
    return {
        "mois": series,
        "totaux": {"ca_ht": round(sum(x["ca_ht"] for x in series), 2), "demandes": total_dem,
                   "refusees": sum(x["refusees"] for x in series), "credits": sum(x["credits"] for x in series),
                   "express_pct": round(100 * sum(x["express"] for x in series) / total_dem) if total_dem else 0,
                   "delai_median_h": round(statistics.median(delais), 1) if delais else None,
                   "clients_actifs": len(clients)},
        "prestations": [{"nom": n, "nombre": k, "credits": credits_prestation[n]} for n, k in prestations.most_common(10)],
        "clients": sorted(({"societe": v["societe"], "credits": v["credits"], "demandes": v["demandes"]}
                           for v in clients.values()), key=lambda c: -c["credits"])[:10],
    }
