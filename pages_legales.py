"""
Pages légales de l'espace client : CGV, mentions légales, confidentialité.

Le texte est modifiable dans l'outil interne (Fileservice → Réglages) et stocké
dans data/portal_config.json, clé "pages". Sans texte enregistré, le modèle
ci-dessous est utilisé : c'est une BASE DE TRAVAIL à faire relire, pas un avis juridique.

Mise en forme volontairement simple (le texte est échappé, aucun HTML accepté) :
    ## Titre        -> intertitre
    - élément       -> liste à puces
    ligne vide      -> nouveau paragraphe
    {raison_sociale}, {siret}… -> remplacés par l'identité légale des réglages
"""
import re

from markupsafe import Markup, escape

PAGES = {
    "cgv": "Conditions générales de vente",
    "mentions-legales": "Mentions légales",
    "confidentialite": "Politique de confidentialité",
}

CHAMPS = ("raison_sociale", "forme", "capital", "adresse", "code_postal", "ville", "siret", "rcs", "tva",
          "email", "tel", "directeur_publication", "hebergeur")

MODELES = {
    "cgv": """Les présentes conditions générales de vente (CGV) s'appliquent à toute utilisation du service de préparation de fichiers moteur (« fileservice ») proposé par {raison_sociale}, {forme} au capital de {capital}, dont le siège est situé {adresse}, {code_postal} {ville}, immatriculée sous le numéro SIRET {siret} ({rcs}).

## 1. Objet et clientèle
Le service est réservé aux professionnels (garages, préparateurs, ateliers). L'ouverture d'un compte est soumise à la validation de {raison_sociale}, qui peut la refuser sans avoir à se justifier.

## 2. Compte client
Le client s'engage à fournir des informations exactes et à les tenir à jour. Il est responsable de la confidentialité de ses identifiants et de toute utilisation de son compte.

## 3. Crédits
- Les prestations se règlent en crédits, achetés à l'avance par packs. Les prix sont indiqués hors taxes ; la TVA applicable est ajoutée au moment du paiement.
- Les crédits n'ont pas de date d'expiration. Ils ne sont ni remboursables, ni convertibles en argent, ni cessibles, sauf dans les cas prévus à l'article 5.
- Chaque achat donne lieu à une facture disponible dans l'espace client.

## 4. Commande et exécution
- Le client dépose la lecture d'origine complète du calculateur et choisit ses prestations. Le nombre de crédits correspondant est débité à l'envoi.
- {raison_sociale} traite la demande dans les meilleurs délais pendant ses heures d'ouverture. Les délais indiqués sont indicatifs.
- Le fichier modifié est mis à disposition dans l'espace client ; le client est prévenu par e-mail.

## 5. Refus et remboursement
{raison_sociale} peut refuser une demande (fichier incomplet ou illisible, calculateur non pris en charge, prestation impossible). Les crédits débités pour cette demande sont alors intégralement recrédités sur le compte du client.

## 6. Révision
Le client peut demander une révision gratuite du fichier livré pendant 30 jours après la livraison, depuis la page de la demande, en décrivant le problème constaté.

## 7. Obligations du client
- Le client garantit disposer des droits sur le véhicule et le calculateur concernés, et fournir des fichiers non altérés.
- Il vérifie le fichier livré (notamment les checksums) avec son outil avant toute écriture dans le calculateur, et conserve une sauvegarde de la lecture d'origine.
- Il est seul responsable de l'installation du fichier et de l'utilisation du véhicule en conformité avec la réglementation en vigueur, notamment en matière d'homologation, d'émissions polluantes et de circulation sur la voie publique, ainsi que de l'information de son propre client final.

## 8. Responsabilité
La responsabilité de {raison_sociale} est limitée au montant, en crédits, de la prestation concernée. Elle ne saurait être engagée pour les dommages indirects, ni pour les conséquences d'une installation, d'un usage ou d'une modification du fichier non conformes aux présentes CGV.

## 9. Données personnelles
Les données collectées sont traitées conformément à la politique de confidentialité disponible sur le site.

## 10. Droit applicable
Les présentes CGV sont soumises au droit français. À défaut de résolution amiable, tout litige relève des tribunaux compétents du ressort du siège de {raison_sociale}.
""",
    "mentions-legales": """## Éditeur du site
{raison_sociale}, {forme} au capital de {capital}
{adresse}, {code_postal} {ville}
SIRET {siret} · {rcs}
TVA intracommunautaire {tva}
Contact : {email} · {tel}

## Directeur de la publication
{directeur_publication}

## Hébergement
{hebergeur}

## Propriété intellectuelle
Les contenus du site (textes, logos, visuels) sont la propriété de {raison_sociale}. Toute reproduction sans autorisation est interdite.
""",
    "confidentialite": """{raison_sociale} ({adresse}, {code_postal} {ville}) est responsable du traitement des données personnelles collectées via l'espace client. Contact : {email}.

## Données collectées
- Identité professionnelle : société, SIRET, numéro de TVA, adresse, nom du contact, e-mail, téléphone.
- Données des demandes : informations sur le véhicule (dont VIN et immatriculation), fichiers de calculateur déposés et livrés, messages échangés.
- Données de facturation et historique des crédits.

## Finalités et bases légales
- Gestion du compte et exécution des prestations : exécution du contrat.
- Facturation et comptabilité : obligation légale.
- Sécurité du service (prévention des abus, journal des connexions) : intérêt légitime.

## Durées de conservation
- Compte et demandes : pendant la relation commerciale, puis 3 ans après le dernier contact.
- Factures et pièces comptables : 10 ans (obligation légale).

## Destinataires
Les données sont destinées à {raison_sociale}. Elles peuvent être transmises à ses prestataires techniques, uniquement pour les besoins du service : hébergement, envoi des e-mails et, le cas échéant, paiement en ligne (Stripe). Elles ne sont jamais vendues.

## Vos droits
Vous disposez d'un droit d'accès, de rectification, d'effacement, de limitation, d'opposition et de portabilité de vos données. Vous pouvez télécharger vos données depuis la page Paramètres de votre espace client, ou exercer vos droits en écrivant à {email}. Vous pouvez également adresser une réclamation à la CNIL (www.cnil.fr).

## Cookies
Le site n'utilise qu'un cookie de session, strictement nécessaire à la connexion, et mémorise dans votre navigateur le choix du thème clair ou sombre. Aucun cookie publicitaire ni de mesure d'audience n'est utilisé.
""",
}


def texte(cfg, cle):
    """Texte brut de la page : celui des réglages, sinon le modèle."""
    pages = cfg.get("pages") or {}
    return (pages.get(cle) or "").strip() or MODELES[cle].strip()


def _remplir(txt, societe):
    def rep(m):
        val = str((societe or {}).get(m.group(1)) or "").strip()
        return val or f"[{m.group(1).replace('_', ' ')} à compléter]"
    return re.sub(r"\{(" + "|".join(CHAMPS) + r")\}", rep, txt)


def rendre(cfg, cle):
    """HTML sûr de la page (texte échappé, puis mise en forme minimale)."""
    txt = _remplir(texte(cfg, cle), cfg.get("societe") or {})
    blocs, liste = [], []

    def fermer_liste():
        if liste:
            blocs.append("<ul>" + "".join(f"<li>{escape(x)}</li>" for x in liste) + "</ul>")
            liste.clear()

    para = []

    def fermer_para():
        if para:
            blocs.append("<p>" + "<br>".join(str(escape(x)) for x in para) + "</p>")
            para.clear()

    for ligne in txt.splitlines():
        s = ligne.strip()
        if s.startswith("## "):
            fermer_para(); fermer_liste()
            blocs.append(f"<h2>{escape(s[3:])}</h2>")
        elif s.startswith("- "):
            fermer_para()
            liste.append(s[2:])
        elif not s:
            fermer_para(); fermer_liste()
        else:
            fermer_liste()
            para.append(s)
    fermer_para(); fermer_liste()
    return Markup("\n".join(blocs))


def a_completer(cfg, cle):
    return "à compléter]" in str(rendre(cfg, cle))
