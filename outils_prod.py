"""
Outils de mise en production (hébergeur type O2switch, cPanel → Terminal ou tâche cron).

    python outils_prod.py admin      crée un compte administrateur de l'outil atelier
                                     (obligatoire avant la première connexion en ligne)
    python outils_prod.py taches     tâches planifiées : sauvegarde du jour, sauvegarde externe,
                                     relances, alerte e-mail si problème — à lancer toutes les heures (cron)
    python outils_prod.py sante      affiche l'état du service
    python outils_prod.py verifier   vérifie l'installation (dossiers, droits, réglages)
    python outils_prod.py droits     corrige les droits des fichiers (erreur « Passenger error #2 … Permission denied »)

Les variables d'environnement sont les mêmes que pour les applications (CARTO_DB,
CARTO_PUBLIC_URL…) : dans une tâche cron, précise-les sur la ligne de commande si tu les as changées.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _portail():
    import portal
    return portal.app


def cmd_admin():
    import comptes
    import equipe
    app = _portail()
    db_path = app.config["FS_DB"]
    comptes.init_db(db_path)
    equipe.init_db(db_path)
    print("Création d'un compte administrateur de l'outil atelier.")
    ident = input("Identifiant (ex. damien) : ").strip()
    nom = input("Nom affiché (signature des messages) : ").strip()
    while True:
        mdp = getpass.getpass("Mot de passe (10 caractères minimum) : ")
        if mdp == getpass.getpass("Confirme le mot de passe : "):
            break
        print("Les deux saisies diffèrent, recommence.")
    try:
        equipe.creer(db_path, ident, nom, "admin", mdp)
    except comptes.ErreurCompte as e:
        print(f"Erreur : {e}")
        return 1
    print(f"Compte administrateur « {ident} » créé. Connecte-toi à l'outil atelier, puis active la double "
          "authentification (onglet Fileservice → Équipe).")
    return 0


def cmd_taches():
    import taches
    app = _portail()
    r = taches.executer(app)
    print("Tâches :", ", ".join(r["fait"]) or "rien à faire")
    for p in r["sante"]["problemes"]:
        print("  ⚠", p)
    return 0


def cmd_sante():
    import mailer
    import sante
    app = _portail()
    s = sante.verifier(app.config["FS_DB"], app.config["FS_DATA_DIR"], mailer.lire_config(app.config["FS_CONFIG"]))
    print("État :", "OK" if s["ok"] else "PROBLÈMES")
    for p in s["problemes"]:
        print("  ⚠", p)
    for a in s["avertissements"]:
        print("  ·", a)
    return 0 if s["ok"] else 2


def cmd_verifier():
    import mailer
    app = _portail()
    c = app.config
    cfg = mailer.lire_config(c["FS_CONFIG"])
    ok = True
    for nom, chemin in (("Données", c["FS_DATA_DIR"]), ("Fichiers clients", c["FS_FILES"])):
        os.makedirs(chemin, exist_ok=True)
        ecriture = os.access(chemin, os.W_OK)
        ok &= ecriture
        print(f"{'✓' if ecriture else '✗'} {nom} : {chemin}{'' if ecriture else ' (pas de droit d’écriture)'}")
    for texte, cond in (("CARTO_PROD=1 (cookies sécurisés, comptes atelier obligatoires)", os.environ.get("CARTO_PROD") == "1"),
                        ("Adresse publique du portail (public_url)", bool(c.get("FS_PUBLIC_URL") or cfg.get("public_url"))),
                        ("E-mails (SMTP)", mailer.configure(cfg.get("smtp") or {})),
                        ("Paiement Stripe", bool((cfg.get("stripe") or {}).get("secret_key"))),
                        ("Sauvegarde externe", bool((cfg.get("sauvegarde_externe") or {}).get("mode")))):
        print(f"{'✓' if cond else '·'} {texte}{'' if cond else ' — à configurer'}")
    import equipe
    equipe.init_db(c["FS_DB"])
    admin = equipe.existe(c["FS_DB"])
    print(f"{'✓' if admin else '✗'} Compte administrateur de l'outil atelier" + ("" if admin else " — python outils_prod.py admin"))
    return 0 if ok and admin else 1


def cmd_droits():
    """Droits d'accès attendus par Apache/Passenger : dossiers 755, fichiers 644 ; data/ reste privé (700/600).
    Corrige l'erreur « Passenger error #2 … Permission denied (errno=13) »."""
    import stat
    racine = os.path.dirname(os.path.abspath(__file__))
    donnees = os.path.join(racine, "data")
    n = 0
    for base, dossiers, fichiers in os.walk(racine):
        prive = os.path.realpath(base).startswith(os.path.realpath(donnees))
        os.chmod(base, 0o700 if prive else 0o755)
        n += 1
        for f in fichiers:
            p = os.path.join(base, f)
            if os.path.islink(p):
                continue
            os.chmod(p, 0o600 if prive else 0o644)
            n += 1
    maison = os.path.expanduser("~")
    mode = stat.S_IMODE(os.stat(maison).st_mode)
    print(f"✓ Droits corrigés sur {n} dossiers et fichiers ({racine}) ; data/ reste privé.")
    if not mode & 0o001:
        print(f"⚠ Votre dossier personnel {maison} est en {oct(mode)} : Apache ne peut pas le traverser.")
        print(f"  Corrigez avec : chmod 711 {maison}")
    print("Redémarrez ensuite les deux applications (Setup Python App → Restart).")
    return 0


COMMANDES = {"admin": cmd_admin, "taches": cmd_taches, "sante": cmd_sante, "verifier": cmd_verifier, "droits": cmd_droits}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDES:
        print(__doc__)
        sys.exit(1)
    sys.exit(COMMANDES[sys.argv[1]]())
