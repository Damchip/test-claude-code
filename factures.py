"""
Factures des packs de crédits.

  - numérotation continue par année : FA-2026-0001, FA-2026-0002…
  - prix HT + TVA 20 % ; autoliquidation (TVA 0 %) pour un client professionnel
    d'un autre pays de l'UE qui a un numéro de TVA intracommunautaire ;
  - l'identité du vendeur et du client est figée dans la facture au moment de
    l'émission (une facture ne change plus ensuite) ;
  - un paiement (session Stripe, référence de virement) ne crédite qu'une fois.
"""
import datetime as dt
import json
import sqlite3

import catalogue
from comptes import ErreurCompte, connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS factures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero TEXT NOT NULL UNIQUE,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    date TEXT NOT NULL,
    client TEXT NOT NULL,
    vendeur TEXT NOT NULL,
    designation TEXT NOT NULL,
    credits INTEGER NOT NULL,
    ht REAL NOT NULL,
    taux_tva REAL NOT NULL,
    tva REAL NOT NULL,
    ttc REAL NOT NULL,
    mention TEXT NOT NULL DEFAULT '',
    paiement TEXT NOT NULL,
    reference TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS factures_client ON factures(client_id, id);
"""

MENTION_AUTOLIQUIDATION = ("Autoliquidation : TVA due par le preneur "
                           "(article 196 de la directive 2006/112/CE).")
MENTION_PENALITES = ("Facture acquittée. En cas de retard de paiement : pénalités au taux de 3 fois le taux "
                     "d'intérêt légal et indemnité forfaitaire pour frais de recouvrement de 40 € "
                     "(art. L441-10 du Code de commerce). Pas d'escompte pour paiement anticipé.")


def init_db(db_path):
    with connect(db_path) as con:
        con.executescript(SCHEMA)


def autoliquidation(client):
    """Client pro UE hors France avec numéro de TVA : facturé HT, TVA autoliquidée."""
    tva = (client.get("tva") or "").upper()
    return bool(tva) and not tva.startswith("FR") and not tva.startswith("MC")


def montants(ht, client):
    taux = 0.0 if autoliquidation(client) else catalogue.TVA
    ht = round(float(ht), 2)
    tva = round(ht * taux, 2)
    return {"ht": ht, "taux": taux, "tva": tva, "ttc": round(ht + tva, 2)}


def _snapshot_client(c):
    return {k: c.get(k, "") for k in ("societe", "contact", "email", "siret", "tva", "adresse",
                                       "code_postal", "ville", "pays")}


def _prochain_numero(con, annee):
    row = con.execute("SELECT numero FROM factures WHERE numero LIKE ? ORDER BY numero DESC LIMIT 1",
                      (f"FA-{annee}-%",)).fetchone()
    n = int(row["numero"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"FA-{annee}-{n:04d}"


def _emettre(db_path, client, *, credits, designation, ht, paiement, reference, vendeur):
    """Crée la facture ET crédite le compte dans la même transaction. Idempotent sur `reference`."""
    m = montants(ht, client)
    now = dt.datetime.now()
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        if reference:
            deja = con.execute("SELECT * FROM factures WHERE reference = ?", (reference,)).fetchone()
            if deja:
                out = _decoder(deja)
                out["nouvelle"] = False
                return out
        numero = _prochain_numero(con, now.year)
        mention = MENTION_AUTOLIQUIDATION if m["taux"] == 0 else ""
        try:
            con.execute(
                "INSERT INTO factures (numero, client_id, date, client, vendeur, designation, credits, ht, taux_tva,"
                " tva, ttc, mention, paiement, reference) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (numero, client["id"], now.strftime("%Y-%m-%d %H:%M:%S"),
                 json.dumps(_snapshot_client(client), ensure_ascii=False), json.dumps(vendeur or {}, ensure_ascii=False),
                 designation, credits, m["ht"], m["taux"], m["tva"], m["ttc"], mention, paiement, reference or None))
        except sqlite3.IntegrityError:
            raise ErreurCompte("Facture déjà enregistrée pour cette référence.")
        con.execute("UPDATE clients SET credits = credits + ? WHERE id = ?", (credits, client["id"]))
        con.execute("INSERT INTO mouvements (client_id, date, libelle, montant) VALUES (?, ?, ?, ?)",
                    (client["id"], now.strftime("%Y-%m-%d %H:%M:%S"), f"{designation} · facture {numero}"[:200], credits))
        row = con.execute("SELECT * FROM factures WHERE numero = ?", (numero,)).fetchone()
    out = _decoder(row)
    out["nouvelle"] = True
    return out


def designation_pack(pack):
    txt = f"Pack {pack['credits']} crédits fileservice"
    return txt + (f" + {pack['bonus']} crédits offerts" if pack["bonus"] else "")


def enregistrer_achat(db_path, client, pack, *, paiement, reference, vendeur, total_centimes=None):
    attendu = round(montants(pack["prix_eur"], client)["ttc"] * 100)
    if total_centimes is not None and int(total_centimes) != attendu:
        raise ErreurCompte(f"Montant payé inattendu ({total_centimes} centimes au lieu de {attendu}).")
    return _emettre(db_path, client, credits=pack["credits"] + pack["bonus"], designation=designation_pack(pack),
                    ht=pack["prix_eur"], paiement=paiement, reference=reference, vendeur=vendeur)


def enregistrer_manuel(db_path, client, *, credits, ht, designation, paiement, reference, vendeur):
    """Paiement reçu hors ligne (virement, chèque…) saisi par l'atelier."""
    credits = int(credits)
    if credits <= 0 or float(ht) <= 0:
        raise ErreurCompte("Crédits et montant HT doivent être positifs.")
    return _emettre(db_path, client, credits=credits, designation=(designation or "Crédits fileservice").strip()[:200],
                    ht=ht, paiement=(paiement or "Virement").strip()[:80], reference=(reference or "").strip() or None,
                    vendeur=vendeur)


def _decoder(row):
    d = dict(row)
    d["client"] = json.loads(d["client"] or "{}")
    d["vendeur"] = json.loads(d["vendeur"] or "{}")
    return d


def lister(db_path, client_id=None):
    sql, args = "SELECT * FROM factures", []
    if client_id is not None:
        sql += " WHERE client_id = ?"
        args.append(client_id)
    with connect(db_path) as con:
        return [_decoder(r) for r in con.execute(sql + " ORDER BY id DESC", args).fetchall()]


def get(db_path, numero, client_id=None):
    sql, args = "SELECT * FROM factures WHERE numero = ?", [numero]
    if client_id is not None:
        sql += " AND client_id = ?"
        args.append(client_id)
    with connect(db_path) as con:
        row = con.execute(sql, args).fetchone()
    return _decoder(row) if row else None
