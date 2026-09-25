"""
Relances automatiques par e-mail (réglages atelier, clé "relances" de portal_config.json) :

  solde_bas       : quand le solde d'un client passe sous `seuil` crédits — une seule fois,
                    ré-armée quand le solde repasse au-dessus du seuil (après une recharge) ;
  non_telecharge  : fichier prêt et pas téléchargé après `delai_h` heures — une seule fois
                    par version livrée.

`verifier` est appelé par le portail toutes les heures et après chaque envoi de fichier.
"""
import datetime as dt

from comptes import connect

DEFAUT = {"solde_bas": True, "seuil": 50, "non_telecharge": True, "delai_h": 48}

SCHEMA = """
CREATE TABLE IF NOT EXISTS relances (
    client_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    cle TEXT NOT NULL,
    date TEXT NOT NULL,
    PRIMARY KEY (client_id, type, cle)
);
"""


def init_db(db_path):
    with connect(db_path) as con:
        con.executescript(SCHEMA)


def reglages(cfg):
    r = dict(DEFAUT)
    r.update({k: v for k, v in (cfg.get("relances") or {}).items() if k in DEFAUT})
    try:
        r["seuil"] = max(0, int(r["seuil"]))
        r["delai_h"] = max(1, int(r["delai_h"]))
    except (TypeError, ValueError):
        r["seuil"], r["delai_h"] = DEFAUT["seuil"], DEFAUT["delai_h"]
    return r


def _deja(con, client_id, type_, cle):
    return con.execute("SELECT 1 FROM relances WHERE client_id = ? AND type = ? AND cle = ?",
                       (client_id, type_, cle)).fetchone() is not None


def _marquer(con, client_id, type_, cle):
    con.execute("INSERT OR IGNORE INTO relances (client_id, type, cle, date) VALUES (?, ?, ?, ?)",
                (client_id, type_, cle, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def verifier(db_path, cfg, envoyer, lien, nom_atelier="E85-FRANCE", maintenant=None):
    """Envoie les relances dues. `envoyer(email, sujet, texte)` ; `lien(chemin)` -> URL absolue.
    Renvoie la liste des relances envoyées [(type, client_id, cle)]."""
    r = reglages(cfg)
    maintenant = maintenant or dt.datetime.now()
    faites = []
    init_db(db_path)
    with connect(db_path) as con:
        clients = [dict(x) for x in con.execute(
            "SELECT id, email, contact, societe, credits FROM clients WHERE statut = 'actif'")]
        a_envoyer = []
        for c in clients:
            if c["credits"] >= r["seuil"]:
                con.execute("DELETE FROM relances WHERE client_id = ? AND type = 'solde_bas'", (c["id"],))
            elif r["solde_bas"] and not _deja(con, c["id"], "solde_bas", "1"):
                _marquer(con, c["id"], "solde_bas", "1")
                a_envoyer.append(("solde_bas", c, "1",
                                  f"{nom_atelier} — votre solde de crédits est bas",
                                  f"Bonjour,\n\nIl vous reste {c['credits']} crédit(s) sur votre compte {nom_atelier}.\n"
                                  f"Pour continuer à envoyer vos fichiers sans attente, rechargez votre compte :\n"
                                  f"{lien('/credits')}\n\n{nom_atelier}"))
        if r["non_telecharge"]:
            limite = (maintenant - dt.timedelta(hours=r["delai_h"])).strftime("%Y-%m-%d %H:%M:%S")
            rows = con.execute(
                "SELECT d.id, d.numero, d.client_id, c.email, MAX(l.version) v, MAX(l.cree_le) livre"
                " FROM demandes d JOIN clients c ON c.id = d.client_id JOIN livrables l ON l.demande_id = d.id"
                " WHERE d.statut = 'pret' AND d.telecharge_le IS NULL AND c.statut = 'actif'"
                " GROUP BY d.id HAVING livre <= ?", (limite,)).fetchall()
            for d in rows:
                cle = f"{d['id']}:{d['v']}"
                if _deja(con, d["client_id"], "non_telecharge", cle):
                    continue
                _marquer(con, d["client_id"], "non_telecharge", cle)
                a_envoyer.append(("non_telecharge", {"id": d["client_id"], "email": d["email"]}, cle,
                                  f"{nom_atelier} — {d['numero']} : votre fichier vous attend",
                                  f"Bonjour,\n\nVotre fichier {d['numero']} est prêt mais n'a pas encore été téléchargé.\n"
                                  f"Il est disponible dans votre espace client :\n{lien('/fichiers/' + d['numero'])}\n\n"
                                  f"{nom_atelier}"))
    for type_, c, cle, sujet, texte in a_envoyer:
        envoyer(c["email"], sujet, texte)
        faites.append((type_, c["id"], cle))
    return faites
