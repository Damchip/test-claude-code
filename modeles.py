"""
Réponses types de l'atelier : messages préenregistrés insérés dans la conversation d'une demande
(onglet Fileservice, liste « Réponse type… »), puis relus et modifiés avant envoi.

Champs remplacés à l'insertion : {contact} {client} {numero} {vehicule} {atelier}
"""
import datetime as dt

from comptes import ErreurCompte, connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS modeles_reponses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titre TEXT NOT NULL,
    texte TEXT NOT NULL,
    attente INTEGER NOT NULL DEFAULT 0,   -- coche « demander une info » en l'insérant
    cree_le TEXT NOT NULL
);
"""
MAX = 50

DEFAUT = [
    ("Fichier illisible ou incomplet",
     "Bonjour {contact},\n\nLe fichier envoyé pour la demande {numero} est incomplet ou illisible. Pourriez-vous refaire "
     "une lecture complète du calculateur (en banc ou en boot si possible) et nous l'envoyer ici ?\n\nMerci,\n{atelier}", 1),
    ("Préciser l'outil et la méthode de lecture",
     "Bonjour {contact},\n\nPour traiter {numero} ({vehicule}), pouvez-vous nous préciser l'outil et la méthode de lecture "
     "utilisés (OBD, banc, boot), ainsi que la version du protocole ?\n\nMerci,\n{atelier}", 1),
    ("Lecture EEPROM nécessaire",
     "Bonjour {contact},\n\nPour cette prestation, nous avons besoin de la lecture de l'EEPROM en plus de la flash. "
     "Vous pouvez l'ajouter ici en pièce jointe.\n\nMerci,\n{atelier}", 1),
    ("Codes défaut présents",
     "Bonjour {contact},\n\nPouvez-vous nous indiquer les codes défaut présents sur le véhicule ({vehicule}) avant "
     "l'intervention, avec une capture si possible ?\n\nMerci,\n{atelier}", 1),
    ("Fichier en cours de traitement",
     "Bonjour {contact},\n\nVotre fichier {numero} est en cours de traitement, il sera disponible dans votre espace "
     "dans la journée.\n\n{atelier}", 0),
    ("Conseils après flash",
     "Bonjour {contact},\n\nVotre fichier {numero} est prêt. Après écriture : contact coupé 30 secondes, effacement des "
     "codes défaut, puis essai progressif. N'hésitez pas à nous faire un retour.\n\n{atelier}", 0),
]


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db(db_path):
    with connect(db_path) as con:
        existait = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'modeles_reponses'").fetchone()
        con.executescript(SCHEMA)
        if not existait:   # exemples proposés une seule fois : l'atelier peut tous les supprimer
            con.executemany("INSERT INTO modeles_reponses (titre, texte, attente, cree_le) VALUES (?, ?, ?, ?)",
                            [(t, x, a, _now()) for t, x, a in DEFAUT])


def lister(db_path):
    with connect(db_path) as con:
        return [dict(r) for r in con.execute("SELECT id, titre, texte, attente FROM modeles_reponses ORDER BY titre COLLATE NOCASE")]


def enregistrer(db_path, titre, texte, attente=False, modele_id=None):
    titre, texte = (titre or "").strip()[:80], (texte or "").strip()[:4000]
    if not titre or not texte:
        raise ErreurCompte("Donne un titre et un texte à la réponse type.")
    with connect(db_path) as con:
        if modele_id:
            cur = con.execute("UPDATE modeles_reponses SET titre = ?, texte = ?, attente = ? WHERE id = ?",
                              (titre, texte, int(bool(attente)), modele_id))
            if not cur.rowcount:
                raise ErreurCompte("Réponse type introuvable.")
            return modele_id
        if con.execute("SELECT COUNT(*) n FROM modeles_reponses").fetchone()["n"] >= MAX:
            raise ErreurCompte(f"{MAX} réponses types au maximum.")
        return con.execute("INSERT INTO modeles_reponses (titre, texte, attente, cree_le) VALUES (?, ?, ?, ?)",
                           (titre, texte, int(bool(attente)), _now())).lastrowid


def supprimer(db_path, modele_id):
    with connect(db_path) as con:
        con.execute("DELETE FROM modeles_reponses WHERE id = ?", (modele_id,))
