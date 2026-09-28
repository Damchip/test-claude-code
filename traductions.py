"""
Traduction de l'espace client (français / anglais).

Le français est la langue de référence : les clés du dictionnaire SONT les
textes français. `t("Se connecter")` renvoie la traduction dans la langue
courante, ou le texte français si la traduction manque (jamais de clé nue à
l'écran). La langue est mémorisée dans un cookie « lang ».
"""
import re

LANGUES = {"fr": "Français", "en": "English"}
DEFAUT = "fr"

# clé française -> anglais. Une clé absente reste en français.
EN = {
    # Navigation & cadre
    "Tableau de bord": "Dashboard",
    "Nouveau fichier": "New file",
    "Mes fichiers": "My files",
    "Compte": "Account",
    "Crédits & factures": "Credits & invoices",
    "Support": "Support",
    "Paramètres": "Settings",
    "Besoin d'aide ?": "Need help?",
    "Posez votre question directement dans la demande concernée, ou contactez l'atelier.":
        "Ask your question directly on the relevant request, or contact the shop.",
    "Contacter l'atelier": "Contact the shop",
    "Service ouvert": "Service open",
    "Service fermé": "Service closed",
    "crédits": "credits",
    "Se déconnecter": "Log out",
    "File service": "File service",
    "Espace client": "Client area",
    # Connexion / inscription
    "Se connecter": "Log in",
    "Connexion": "Log in",
    "Bon retour": "Welcome back",
    "Connectez-vous à votre espace client.": "Sign in to your client area.",
    "E-mail": "Email",
    "Mot de passe": "Password",
    "Oublié ?": "Forgot?",
    "Rester connecté sur cet appareil": "Stay signed in on this device",
    "Pas encore de compte ?": "No account yet?",
    "Créer un compte pro": "Create a pro account",
    "Déjà client ?": "Already a client?",
    "Vos fichiers moteur,": "Your engine files,",
    "préparés par des spécialistes.": "prepared by specialists.",
    "Déposez votre lecture, choisissez vos prestations, récupérez un fichier testé et prêt à écrire.":
        "Upload your read, choose your services, get a tested file ready to flash.",
    "Livraison rapide": "Fast delivery",
    "La plupart des fichiers sont prêts en moins de 30 minutes.":
        "Most files are ready in under 30 minutes.",
    "Checksums corrigés": "Checksums corrected",
    "Chaque fichier est vérifié avant de vous être livré.":
        "Every file is verified before it is delivered.",
    "Un technicien dédié": "A dedicated technician",
    "Une question sur un fichier ? Réponse directe dans la demande.":
        "A question about a file? Answer directly on the request.",
    "Accès réservé aux professionnels": "Reserved for professionals",
    "Votre compte est validé par l'atelier sous 24 h ouvrées.":
        "Your account is approved by the shop within one business day.",
    "Société": "Company",
    "Nom et prénom": "Full name",
    "Téléphone": "Phone",
    "Adresse": "Address",
    "Code postal": "Postal code",
    "Ville": "City",
    "Pays": "Country",
    "E-mail professionnel": "Business email",
    "10 caractères minimum.": "10 characters minimum.",
    "Demander l'ouverture du compte": "Request account opening",
    # Tableau de bord
    "Bonjour": "Hello",
    "compte": "account",
    "Envoyer un fichier": "Send a file",
    "Solde": "Balance",
    "Recharger": "Top up",
    "En cours": "In progress",
    "en attente de votre réponse": "awaiting your reply",
    "Délai moyen": "Average time",
    "Livrés ce mois": "Delivered this month",
    "au total": "in total",
    "Fichiers récents": "Recent files",
    "Vos dernières demandes": "Your latest requests",
    "Tout voir": "See all",
    "Véhicule": "Vehicle",
    "Prestations": "Services",
    "Statut": "Status",
    "Date": "Date",
    "Aucun fichier pour l'instant.": "No files yet.",
    "Envoyez votre première lecture : l'atelier vous prévient par e-mail dès que le fichier est prêt.":
        "Send your first read: the shop emails you as soon as the file is ready.",
    "Horaires du service": "Opening hours",
    "Ouvert": "Open",
    "Fermé": "Closed",
    # Statuts
    "Reçu": "Received", "En traitement": "In progress", "Info requise": "Info needed",
    "Prêt": "Ready", "Refusé": "Declined",
    # Fichiers / détail
    "Toutes vos demandes, leur statut et les fichiers livrés.":
        "All your requests, their status and delivered files.",
    "Tous": "All", "Prêts": "Ready", "Refusés": "Declined",
    "Calculateur": "ECU", "Crédits": "Credits", "N°": "No.",
    "Vous n'avez encore envoyé aucun fichier.": "You haven't sent any file yet.",
    "Aucun fichier ne correspond.": "No matching file.",
    "Fichiers": "Files", "Versions livrées et lecture d'origine": "Delivered versions and original read",
    "Télécharger": "Download", "Lecture d'origine": "Original read",
    "Conversation": "Conversation",
    "Échanges avec l'atelier sur cette demande": "Messages with the shop about this request",
    "Pas encore de message. L'atelier vous écrira ici si besoin.":
        "No message yet. The shop will write here if needed.",
    "Écrire à l'atelier…": "Write to the shop…", "Envoyer": "Send", "Vous": "You", "Atelier": "Shop",
    "Demander une révision": "Request a revision",
    "Révision gratuite pendant": "Free revision for", "jours": "days",
    "L'atelier attend une précision de votre part.": "The shop is waiting for a detail from you.",
    "Débité": "Charged", "Remboursé": "Refunded",
    # Envoi
    "Renseignez le véhicule et la lecture, choisissez vos prestations. Les crédits sont débités à l'envoi et remboursés si le fichier ne peut pas être traité.":
        "Fill in the vehicle and the read, choose your services. Credits are charged on submit and refunded if the file cannot be processed.",
    "Type de véhicule": "Vehicle type", "Marque": "Make", "Modèle": "Model", "Motorisation": "Engine",
    "Année": "Year", "Boîte": "Gearbox", "Kilométrage": "Mileage", "Immatriculation": "Plate",
    "Lecture": "Read", "Outil de lecture": "Reading tool", "Méthode": "Method",
    "Déposez la lecture d'origine": "Drop the original read",
    "Commentaire pour l'atelier": "Comment for the shop", "Services": "Services",
    "Récapitulatif": "Summary", "Aucune prestation sélectionnée.": "No service selected.",
    "Total": "Total", "Solde actuel": "Current balance", "Solde après envoi": "Balance after submit",
    "Envoyer la demande": "Submit request",
    "Ouverture du boîtier au siège": "ECU opening at our workshop",
    "Vous nous envoyez le calculateur": "You send us the ECU",
    "Retour du boîtier": "ECU return", "Garantie": "Warranty", "Aucune": "None",
    # Crédits
    "Les crédits n'expirent pas ; les packs les plus gros donnent des crédits offerts.":
        "Credits never expire; larger packs include bonus credits.",
    "Solde disponible": "Available balance", "Pack": "Pack", "offerts": "bonus", "de bonus": "bonus",
    "Acheter": "Buy", "Par virement": "By transfer",
    "Mouvements": "Transactions", "Achats, débits et remboursements": "Purchases, charges and refunds",
    "Libellé": "Label", "Aucun mouvement pour l'instant. Rechargez des crédits pour envoyer votre premier fichier.":
        "No transaction yet. Top up credits to send your first file.",
    "Factures": "Invoices", "Ouvrez-les pour les imprimer ou les enregistrer en PDF":
        "Open them to print or save as PDF", "Aucune facture pour l'instant.": "No invoice yet.",
    # Paramètres
    "Coordonnées de facturation et sécurité du compte.": "Billing details and account security.",
    "Ces informations figurent sur vos factures": "This information appears on your invoices",
    "E-mail de connexion": "Login email", "Enregistrer": "Save",
    "Mot de passe actuel": "Current password", "Nouveau mot de passe": "New password",
    "Confirmation": "Confirmation", "Changer le mot de passe": "Change password",
    "Mes données": "My data", "Télécharger mes données": "Download my data",
    "API revendeur": "Reseller API",
    # Support
    "Une question sur un fichier ? Le plus rapide est d'écrire dans la conversation de la demande concernée.":
        "A question about a file? The fastest way is to write in the conversation of the relevant request.",
    "Questions fréquentes": "Frequently asked questions", "Horaires": "Hours",
    # Catalogue (noms et descriptions affichés)
    "Adaptation E85": "E85 conversion", "Fichier sur mesure": "Custom file",
    "Fichier sur mesure bioéthanol": "Custom bioethanol file", "Speed limit": "Speed limit",
    "Réglage du limiteur de vitesse": "Speed limiter setting", "Réglage du Start & Stop": "Start & Stop setting",
    "Suppression DTC": "DTC removal", "Codes défaut à préciser en commentaire": "Fault codes to specify in the comment",
    "Réglage IMMO": "IMMO setting", "Antidémarrage": "Immobilizer",
    "Volet collecteur d'admission": "Intake manifold flap", "Réglage du volet d'admission": "Intake flap setting",
    "Volet d'échappement": "Exhaust flap", "Réglage du volet d'échappement": "Exhaust flap setting",
    "Limitation de couple": "Torque limitation", "Stage 1 – PL": "Stage 1 – Truck", "Suppression DTC – PL": "DTC removal – Truck",
    "Réglage IMMO – PL": "IMMO setting – Truck", "Stage 1 – Moto": "Stage 1 – Motorbike",
    "Speed limit – Moto/Quad": "Speed limit – Motorbike/Quad", "Clonage calculateur": "ECU cloning",
    "Copie vers un calculateur de remplacement": "Copy to a replacement ECU", "Nettoyage injecteurs": "Injector cleaning",
    "Nettoyage et test d'équilibrage · envoi des injecteurs": "Cleaning and balance test · send the injectors",
    "Pack E85 + débridage moteur": "E85 + engine tuning pack", "Garantie Sérénité 1 an": "Serenity warranty 1 year",
    "Garantie Sérénité 2 ans": "Serenity warranty 2 years", "Véhicule léger": "Car / light vehicle",
    "Agricole / PL": "Agricultural / Truck", "Moto / Quad": "Motorbike / Quad",
    "au siège": "at our workshop", "(hors crédits)": "(not in credits)",
    "Garantie Sérénité sur le fichier livré.": "Serenity warranty on the delivered file.",
    "Le tarif pack s'applique automatiquement. Crédits remboursés si le fichier est refusé.":
        "Pack pricing applies automatically. Credits refunded if the file is declined.",
    "ou cliquez pour parcourir · .bin, .ori, .slave… · 64 Mo max": "or click to browse · .bin, .ori, .slave… · 64 MB max",
    "Changer": "Change", "Remise": "Discount",
    "Manuelle": "Manual", "Automatique": "Automatic", "Double embrayage": "Dual clutch", "Banc": "Bench",
    "OBD": "OBD", "Boot": "Boot", "17 caractères": "17 characters", "Détecté automatiquement si vide": "Detected automatically if empty",
    "Modifications mécaniques, codes défaut à traiter, carburant utilisé…": "Mechanical changes, fault codes to handle, fuel used…",
    "Ce qui doit être revu (comportement, codes défaut…)": "What needs to be reviewed (behaviour, fault codes…)",
    "Véhicule, calculateur, n°…": "Vehicle, ECU, no.…", "Rechercher": "Search",
    "SIRET": "Company no. (SIRET in France)",
    "CGV": "Terms of sale", "Mentions légales": "Legal notice", "Confidentialité": "Privacy",
    "Demande envoyée": "Request sent",
    "Merci, la demande pour {societe} est bien reçue. L'atelier la vérifie sous 24 h ouvrées ; vous recevrez un e-mail dès que votre compte sera actif.":
        "Thank you, the request for {societe} has been received. The shop reviews it within one business day; you will get an email as soon as your account is active.",
    "Retour à la connexion": "Back to sign in", "Mot de passe oublié": "Forgot password",
    "Si un compte existe pour cette adresse, un e-mail vient de partir avec un lien valable 1 heure. Pensez à vérifier les indésirables.":
        "If an account exists for this address, an email with a link valid for 1 hour has just been sent. Check your spam folder.",
    "Indiquez l'e-mail de votre compte, nous vous envoyons un lien pour choisir un nouveau mot de passe.":
        "Enter your account email and we will send you a link to choose a new password.",
    "Envoyer le lien": "Send the link", "Ce lien a expiré ou a déjà servi.": "This link has expired or has already been used.",
    "Refaire une demande": "Request a new link",
    "Choisissez un nouveau mot de passe (10 caractères minimum).": "Choose a new password (10 characters minimum).",
    "J'accepte les": "I accept the", "conditions générales de vente": "terms and conditions of sale",
    # JavaScript du formulaire d'envoi
    "Tarif pack appliqué : {n} crédits économisés": "Pack price applied: {n} credits saved",
    "Tarif indisponible, réessayez.": "Price unavailable, please retry.", "cr.": "cr.", "Pièce jointe : ": "Attachment: ", "Aucune": "None", "1 an": "1 year", "2 ans": "2 years",
    # Phrases avec variables (format « {} »)
    "sur vos fichiers des 30 derniers jours": "on your files over the last 30 days",
    "Calculateur à identifier": "ECU to be identified", "envoyé le": "sent on",
    "Aucun fichier ne correspond.": "No matching file.",
    "1 crédit = {ht} € HT ({ttc} € TTC).": "1 credit = €{ht} excl. VAT (€{ttc} incl. VAT).",
    "1 crédit = {ht} € HT.": "1 credit = €{ht} excl. VAT.",
    "soit {prix} € HT / crédit reçu": "i.e. €{prix} excl. VAT / credit received",
    "HT": "excl. VAT", "TTC": "incl. VAT", "TVA autoliquidée": "VAT reverse-charged", "LE PLUS CHOISI": "MOST POPULAR",
    "Paiement en ligne bientôt disponible. En attendant, commandez votre pack auprès de l'atelier : les crédits et la facture sont ajoutés à réception du virement.":
        "Online payment coming soon. Meanwhile, order your pack from the shop: credits and invoice are added once the transfer is received.",
    "Client professionnel UE hors France : facturation HT, TVA autoliquidée.":
        "EU business client outside France: invoiced excl. VAT, VAT reverse-charged.",
    "Connectez votre propre logiciel ou site : envoi de fichiers, suivi et téléchargement automatiques.":
        "Connect your own software or website: automatic file upload, tracking and download.",
    "Clés et documentation": "Keys and documentation",
    "Pour changer la société, le SIRET ou l'e-mail de connexion, contactez l'atelier.":
        "To change the company, SIRET or login email, contact the shop.",
    "Téléchargez toutes les données de votre compte (coordonnées, demandes, messages, crédits, factures). Pour supprimer votre compte, écrivez à l'atelier.":
        "Download all your account data (details, requests, messages, credits, invoices). To delete your account, write to the shop.",
    "Écrivez-nous depuis la conversation d'une demande.": "Write to us from a request's conversation.",
    # FAQ
    "Quel fichier dois-je envoyer ?": "Which file should I send?",
    "La lecture d'origine complète du calculateur, telle que sortie de votre outil (KESS3, Autotuner, Flex…). En lecture banc ou boot, envoyez le fichier complet. Précisez dans le commentaire toute modification mécanique.":
        "The complete original ECU read, as output by your tool (KESS3, Autotuner, Flex…). For bench or boot reads, send the full file. Mention any mechanical modification in the comment.",
    "Quand mes crédits sont-ils débités ?": "When are my credits charged?",
    "À l'envoi de la demande. Si l'atelier ne peut pas traiter le fichier, la demande est refusée avec son motif et les crédits sont remboursés automatiquement.":
        "When the request is submitted. If the shop cannot process the file, the request is declined with the reason and the credits are refunded automatically.",
    "Comment serai-je prévenu ?": "How will I be notified?",
    "Par e-mail dès que le fichier est prêt, ou si l'atelier a besoin d'une précision. Tout reste visible dans « Mes fichiers ».":
        "By email as soon as the file is ready, or if the shop needs a detail. Everything stays visible in “My files”.",
    "Et si le fichier ne me convient pas ?": "What if the file doesn't suit me?",
    "Depuis la page de la demande, cliquez sur « Demander une révision » en décrivant le problème. La révision est gratuite pendant {jours} jours après la livraison.":
        "From the request page, click “Request a revision” and describe the problem. The revision is free for {jours} days after delivery.",
    "Les checksums sont-ils corrigés ?": "Are checksums corrected?",
    "Oui, chaque fichier livré est vérifié et ses checksums corrigés. Vérifiez tout de même avec votre outil avant d'écrire le calculateur.":
        "Yes, every delivered file is checked and its checksums corrected. Still verify with your tool before writing the ECU.",
    "Comment obtenir une facture ?": "How do I get an invoice?",
    "Chaque achat de crédits génère une facture, disponible dans « Crédits & factures ». Ouvrez-la puis « Imprimer / enregistrer en PDF ».":
        "Every credit purchase generates an invoice, available in “Credits & invoices”. Open it, then “Print / save as PDF”.",
    # --- v1.56 : API revendeur, messages et erreurs ---
    'Ouvrir le menu': 'Open menu',
    'Changer de thème': 'Toggle theme',
    'Téléchargé': 'Downloaded',
    'Joindre un fichier (lecture EEPROM, photo…)': 'Attach a file (EEPROM read, photo…)',
    'Joindre': 'Attach',
    'Type': 'Type',
    'Moteur': 'Engine',
    'VIN': 'VIN',
    'retour': 'return',
    'Révision gratuite pendant {n} jours': 'Free revision for {n} days',
    '+ {n} offerts · {p} % de bonus': '+ {n} free · {p}% bonus',
    'Ouvrir': 'Open',
    'Connectez votre logiciel ou votre site à {atelier} : envoi de fichiers, suivi et téléchargement automatiques.': 'Connect your software or website to {atelier}: automatic file upload, tracking and download.',
    "L'API n'est pas encore activée pour votre compte. Contactez l'atelier pour l'activer.": 'The API is not enabled for your account yet. Contact the shop to enable it.',
    'Votre nouvelle clé': 'Your new key',
    'Copiez-la maintenant : pour votre sécurité, elle ne sera plus jamais affichée.': 'Copy it now: for your security, it will never be shown again.',
    'Mes clés': 'My keys',
    'créée le': 'created',
    'utilisée le': 'used',
    'jamais utilisée': 'never used',
    "Révoquer cette clé ? Tout logiciel qui l'utilise cessera de fonctionner.": 'Revoke this key? Any software using it will stop working.',
    'Révoquer': 'Revoke',
    "Aucune clé pour l'instant.": 'No keys yet.',
    'Nom (ex. mon site, KESS3 atelier)': 'Name (e.g. my website, workshop KESS3)',
    'Générer une clé': 'Generate a key',
    'Prise en main': 'Getting started',
    "Envoyez la clé dans l'en-tête": 'Send the key in the',
    'Base :': 'Base URL:',
    'Types de véhicule :': 'Vehicle types:',
    'Codes des prestations et prix :': 'Service codes and prices:',
    "Points d'accès": 'Endpoints',
    'Chemin': 'Path',
    'Rôle': 'Purpose',
    'Solde, niveau, remise': 'Balance, level, discount',
    'Prestations, packs, garanties et prix': 'Services, packs, warranties and prices',
    "Tarif d'une sélection (JSON : categorie, prestations[])": 'Price of a selection (JSON: categorie, prestations[])',
    'Liste de vos demandes (filtre ?statut=)': 'List of your requests (filter ?statut=)',
    'Envoyer un fichier (multipart : file + champs) — débite les crédits': 'Upload a file (multipart: file + fields) — charges credits',
    'Détail : statut, fichiers livrés, messages': 'Details: status, delivered files, messages',
    "Télécharger la lecture d'origine": 'Download the original read',
    'Télécharger le fichier livré': 'Download the delivered file',
    "Écrire à l'atelier (JSON : texte)": 'Message the shop (JSON: texte)',
    'Réponses en JSON. Limite : 120 requêtes par minute. Une erreur renvoie un code HTTP 4xx et': 'JSON responses. Limit: 120 requests per minute. Errors return an HTTP 4xx code and',
    'Session expirée, merci de réessayer.': 'Session expired, please try again.',
    'Mot de passe modifié. Vous pouvez vous connecter.': 'Password changed. You can now log in.',
    "Demande de révision envoyée à l'atelier.": 'Revision request sent to the shop.',
    "Le paiement en ligne n'est pas encore activé : contactez l'atelier pour un virement.": 'Online payment is not enabled yet: contact the shop to pay by bank transfer.',
    "Le paiement est momentanément indisponible. Réessayez ou contactez l'atelier.": 'Payment is temporarily unavailable. Try again or contact the shop.',
    'Paiement en cours de confirmation : vos crédits apparaîtront dans quelques instants.': 'Payment being confirmed: your credits will appear in a moment.',
    'Mot de passe modifié.': 'Password changed.',
    'Coordonnées enregistrées.': 'Details saved.',
    'Clé révoquée : elle ne fonctionne plus.': 'Key revoked: it no longer works.',
    'Trop de tentatives. Réessayez dans 15 minutes ou réinitialisez votre mot de passe.': 'Too many attempts. Try again in 15 minutes or reset your password.',
    'E-mail ou mot de passe incorrect.': 'Incorrect email or password.',
    "Votre compte est en attente de validation par l'atelier. Vous recevrez un e-mail dès son ouverture.": 'Your account is awaiting approval by the shop. You will get an email as soon as it is opened.',
    "Ce compte est suspendu. Contactez l'atelier.": 'This account is suspended. Contact the shop.',
    "Merci d'accepter les conditions générales de vente.": 'Please accept the terms and conditions of sale.',
    'Trop de demandes depuis votre connexion. Réessayez plus tard.': 'Too many requests from your connection. Try again later.',
    'Les deux mots de passe ne correspondent pas.': 'The two passwords do not match.',
    'Les deux nouveaux mots de passe ne correspondent pas.': 'The two new passwords do not match.',
    'Le fichier est vide.': 'The file is empty.',
    'Fichier trop volumineux (64 Mo maximum).': 'File too large (64 MB maximum).',
    'Choisissez au moins une prestation.': 'Choose at least one service.',
    'Compte inactif.': 'Inactive account.',
    'Mot de passe trop long.': 'Password too long.',
    'Indiquez le nom de la société.': 'Enter the company name.',
    'SIRET invalide : 14 chiffres attendus.': 'Invalid SIRET: 14 digits expected.',
    'Numéro de TVA invalide (ex. FR12345678901).': 'Invalid VAT number (e.g. FR12345678901).',
    'Adresse e-mail invalide.': 'Invalid email address.',
    'Un compte existe déjà avec cette adresse e-mail.': 'An account already exists with this email address.',
    'Message vide.': 'Empty message.',
    'Pièce jointe trop volumineuse (64 Mo maximum).': 'Attachment too large (64 MB maximum).',
    'Demande introuvable.': 'Request not found.',
    'Mot de passe actuel incorrect.': 'Current password is incorrect.',
    'Ce lien a expiré ou a déjà servi. Refaites une demande.': 'This link has expired or was already used. Please request a new one.',
    'Décrivez ce qui doit être revu.': 'Describe what needs to be revised.',
    'Type de véhicule inconnu.': 'Unknown vehicle type.',
    "L'API n'est pas activée par l'atelier.": 'The API is not enabled by the shop.',
    'Session expirée, rechargez la page.': 'Session expired, reload the page.',
    'Connexion requise.': 'Login required.',
    'Lundi': 'Monday',
    'Mardi': 'Tuesday',
    'Mercredi': 'Wednesday',
    'Jeudi': 'Thursday',
    'Vendredi': 'Friday',
    'Samedi': 'Saturday',
    'Dimanche': 'Sunday',
    'Navigation principale': 'Main navigation',
    'N° TVA': 'VAT number',
    '−{p} % sur vos prestations': '−{p}% on your services',
    'Autre': 'Other',
    'Créer un compte': 'Create an account',
    'Conditions générales de vente': 'Terms and conditions of sale',
    'Politique de confidentialité': 'Privacy policy',
    "Retour à l'espace client": 'Back to the client area',
    'Texte en français, seule version faisant foi.': 'French text, the only legally binding version.',
    'Récapitulatif PDF': 'PDF summary',
    'Retour': 'Back',
    'Imprimer / enregistrer en PDF': 'Print / save as PDF',
    'Récapitulatif de demande': 'Request summary',
    'Envoyée le': 'Sent on',
    'livrée le': 'delivered on',
    'Client': 'Client',
    'Outil': 'Tool',
    'Désignation': 'Description',
    'Total débité': 'Total charged',
    'Valeur indicative': 'Indicative value',
    'Motif du refus': 'Reason for declining',
    'Fichier': 'File',
    'Taille': 'Size',
    'Fichier complémentaire': 'Additional file',
    'Fichier livré': 'Delivered file',
    'Commentaire': 'Comment',
    'Document récapitulatif, sans valeur de facture : les crédits sont facturés à leur achat (Crédits & factures).': 'Summary document, not an invoice: credits are invoiced when purchased (Credits & invoices).',
}

TABLE = {"fr": {}, "en": EN}


def normaliser(code):
    return code if code in LANGUES else DEFAUT


# Messages dont une partie varie (numéro, montant…) : motif français -> gabarit anglais
MOTIFS_EN = [
    (re.compile(r"^Demande (F-\d+) : votre fichier est déjà prêt ! (\d+) crédits débités\.$"),
     "Request {0}: your file is already ready! {1} credits charged."),
    (re.compile(r"^Demande (F-\d+) envoyée : (\d+) crédits débités\. Vous serez prévenu par e-mail\.$"),
     "Request {0} sent: {1} credits charged. You will be notified by email."),
    (re.compile(r"^Paiement reçu, merci ! Crédits ajoutés, facture (\S+) disponible ci-dessous\.$"),
     "Payment received, thank you! Credits added, invoice {0} available below."),
    (re.compile(r"^Solde insuffisant : (\d+) crédits nécessaires, (-?\d+) disponibles\.$"),
     "Insufficient balance: {0} credits needed, {1} available."),
    (re.compile(r"^Le mot de passe doit faire au moins (\d+) caractères\.$"), "The password must be at least {0} characters long."),
    (re.compile(r"^Révision possible uniquement dans les (\d+) jours suivant la livraison\.$"),
     "A revision is only possible within {0} days of delivery."),
    (re.compile(r"^Prestation indisponible pour ce véhicule : (.+)$"), "Service unavailable for this vehicle: {0}"),
    (re.compile(r"^(\d+) clés actives au maximum : révoquez-en une\.$"), "{0} active keys at most: revoke one."),
]


def traduire(texte, langue):
    if langue == "fr" or not isinstance(texte, str):
        return texte
    table = TABLE.get(langue, {})
    if texte in table:
        return table[texte]
    if langue == "en":
        for motif, gabarit in MOTIFS_EN:
            m = motif.match(texte)
            if m:
                return gabarit.format(*m.groups())
    return texte


# --- E-mails aux clients : (sujet, corps) par langue ----------------------------
# {atelier} = nom de l'atelier ; les autres champs sont fournis à l'appel.
MAILS = {
    "inscription": {
        "fr": ("{atelier} — demande d'ouverture de compte reçue",
               "Bonjour,\n\nNous avons bien reçu la demande d'ouverture de compte pour {societe}.\n"
               "L'atelier la vérifie sous 24 h ouvrées ; vous recevrez un e-mail dès que votre compte sera actif.\n\n{atelier}"),
        "en": ("{atelier} — account request received",
               "Hello,\n\nWe have received the account request for {societe}.\n"
               "The shop reviews it within one business day; you will get an email as soon as your account is active.\n\n{atelier}"),
    },
    "compte_ouvert": {
        "fr": ("{atelier} — votre compte est ouvert",
               "Bonjour,\n\nVotre compte fileservice pour {societe} est maintenant actif.\n{acces}\n\n{atelier}"),
        "en": ("{atelier} — your account is open",
               "Hello,\n\nYour file service account for {societe} is now active.\n{acces}\n\n{atelier}"),
    },
    "acces_lien": {
        "fr": ("", "Connectez-vous avec votre e-mail et le mot de passe choisi à l'inscription :\n{lien}"),
        "en": ("", "Sign in with your email and the password chosen at registration:\n{lien}"),
    },
    "acces_sans_lien": {
        "fr": ("", "Connectez-vous à votre espace client avec votre e-mail et le mot de passe choisi à l'inscription."),
        "en": ("", "Sign in to your client area with your email and the password chosen at registration."),
    },
    "invitation": {
        "fr": ("{atelier} — votre compte fileservice est prêt",
               "Bonjour,\n\n{atelier} vous a ouvert un compte fileservice pour {societe}.\n"
               "Choisissez votre mot de passe avec ce lien (valable 7 jours) :\n{lien}\n\n"
               "Vous pourrez ensuite vous connecter avec l'adresse {email}.\n\n{atelier}"),
        "en": ("{atelier} — your file service account is ready",
               "Hello,\n\n{atelier} has opened a file service account for {societe}.\n"
               "Choose your password with this link (valid for 7 days):\n{lien}\n\n"
               "You can then sign in with {email}.\n\n{atelier}"),
    },
    "reset": {
        "fr": ("{atelier} — réinitialisation du mot de passe",
               "Bonjour,\n\nPour choisir un nouveau mot de passe, ouvrez ce lien (valable 1 heure) :\n{lien}\n\n"
               "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail.\n\n{atelier}"),
        "en": ("{atelier} — password reset",
               "Hello,\n\nTo choose a new password, open this link (valid for 1 hour):\n{lien}\n\n"
               "If you did not request this, you can ignore this email.\n\n{atelier}"),
    },
    "fichier_pret": {
        "fr": ("{atelier} — {numero} : fichier prêt",
               "Bonjour,\n\nVotre fichier {numero} est prêt{version}. Téléchargez-le depuis votre espace client :\n{lien}\n\n{atelier}"),
        "en": ("{atelier} — {numero}: file ready",
               "Hello,\n\nYour file {numero} is ready{version}. Download it from your client area:\n{lien}\n\n{atelier}"),
    },
    "message": {
        "fr": ("{atelier} — {numero} : message de l'atelier",
               "Bonjour,\n\nL'atelier vous a écrit au sujet de votre fichier {numero} :\n\n{texte}\n\n"
               "Répondez depuis votre espace client :\n{lien}\n\n{atelier}"),
        "en": ("{atelier} — {numero}: message from the shop",
               "Hello,\n\nThe shop wrote to you about your file {numero}:\n\n{texte}\n\n"
               "Reply from your client area:\n{lien}\n\n{atelier}"),
    },
    "info_requise": {
        "fr": ("{atelier} — {numero} : information requise",
               "Bonjour,\n\nL'atelier a besoin d'une précision pour terminer votre fichier {numero} :\n\n{texte}\n\n"
               "Répondez depuis votre espace client :\n{lien}\n\n{atelier}"),
        "en": ("{atelier} — {numero}: information needed",
               "Hello,\n\nThe shop needs a detail to finish your file {numero}:\n\n{texte}\n\n"
               "Reply from your client area:\n{lien}\n\n{atelier}"),
    },
    "refus": {
        "fr": ("{atelier} — {numero} : fichier refusé",
               "Bonjour,\n\nNous ne pouvons pas traiter votre fichier {numero} : {motif}\n"
               "Vos {credits} crédits ont été remboursés sur votre compte. Détails :\n{lien}\n\n{atelier}"),
        "en": ("{atelier} — {numero}: file declined",
               "Hello,\n\nWe cannot process your file {numero}: {motif}\n"
               "Your {credits} credits have been refunded to your account. Details:\n{lien}\n\n{atelier}"),
    },
    "facture": {
        "fr": ("{atelier} — facture {numero}",
               "Bonjour,\n\nMerci pour votre achat : {credits} crédits ont été ajoutés à votre compte.\n"
               "Votre facture {numero} est disponible dans votre espace client, rubrique Crédits & factures.\n\n{atelier}"),
        "en": ("{atelier} — invoice {numero}",
               "Hello,\n\nThank you for your purchase: {credits} credits have been added to your account.\n"
               "Your invoice {numero} is available in your client area, under Credits & invoices.\n\n{atelier}"),
    },
    "solde_bas": {
        "fr": ("{atelier} — votre solde de crédits est bas",
               "Bonjour,\n\nIl vous reste {credits} crédit(s) sur votre compte {atelier}.\n"
               "Pour continuer à envoyer vos fichiers sans attente, rechargez votre compte :\n{lien}\n\n{atelier}"),
        "en": ("{atelier} — your credit balance is low",
               "Hello,\n\nYou have {credits} credit(s) left on your {atelier} account.\n"
               "To keep sending files without delay, top up your account:\n{lien}\n\n{atelier}"),
    },
    "non_telecharge": {
        "fr": ("{atelier} — {numero} : votre fichier vous attend",
               "Bonjour,\n\nVotre fichier {numero} est prêt mais n'a pas encore été téléchargé.\n"
               "Il est disponible dans votre espace client :\n{lien}\n\n{atelier}"),
        "en": ("{atelier} — {numero}: your file is waiting",
               "Hello,\n\nYour file {numero} is ready but has not been downloaded yet.\n"
               "It is available in your client area:\n{lien}\n\n{atelier}"),
    },
}


class _Defaut(dict):
    def __missing__(self, k):
        return ""


def mail(cle, langue, **champs):
    """(sujet, corps) de l'e-mail `cle` dans la langue du client (français si inconnue)."""
    modele = MAILS[cle].get(normaliser(langue)) or MAILS[cle]["fr"]
    ch = _Defaut(champs)
    return modele[0].format_map(ch), modele[1].format_map(ch)
