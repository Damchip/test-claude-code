"""
Couche conversationnelle via l'API Anthropic.

CONFIDENTIALITÉ : cette fonction n'envoie QUE le résumé déjà calculé en local
(libellés, types de solution, scores, statut de test). Aucun octet de fichier,
aucune table complète, aucun savoir-faire n'est transmis. Si aucune clé API
n'est configurée, on retourne un résumé généré localement (mode 100% hors-ligne).
"""

import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM = (
    "Tu es l'assistant interne d'un atelier de reprogrammation moteur. "
    "On te donne le résultat d'une recherche de solution dans la base locale. "
    "Explique en français, de façon concise et professionnelle, s'il existe "
    "une solution réutilisable, son niveau de confiance et son statut de test. "
    "Sois honnête sur l'incertitude. Ne révèle jamais de détail technique de la "
    "modification elle-même."
)


def _sanitize(match_result: dict) -> dict:
    """Ne garder que ce qui est non sensible et utile à l'explication."""
    nm = match_result["incoming"].get("name_meta") or {}
    return {
        "plateforme_ecu": match_result["incoming"].get("platform"),
        "fabricant": match_result["incoming"].get("manufacturer"),
        "infos_nom_fichier": {
            "marque": nm.get("brand"),
            "vehicule": nm.get("vehicle"),
            "type_solution": nm.get("solution_type"),
        },
        "identifiants_fichier_client": match_result["incoming"]["candidate_ids"][:10],
        "taille_octets": match_result["incoming"]["size"],
        "taille_base": match_result["db_size"],
        "correspondances": [
            {
                "vehicule": m["vehicle_label"],
                "type_solution": m["solution_type"],
                "statut_test": m["tested_status"],
                "score": m["score"],
                "raison": m["reason"],
            }
            for m in match_result["matches"]
        ],
    }


def _offline_summary(payload: dict) -> str:
    ms = payload["correspondances"]
    if not ms:
        return ("Aucune solution correspondante trouvée dans la base locale "
                f"({payload['taille_base']} entrées). Il s'agit probablement "
                "d'un nouveau dossier à traiter manuellement.")
    best = ms[0]
    lines = [
        f"Meilleure correspondance : {best['vehicule'] or 'véhicule non libellé'} "
        f"— {best['type_solution'] or 'type non précisé'} "
        f"(confiance {int(best['score'] * 100)}%, statut : {best['statut_test']}).",
        f"Raison : {best['raison']}.",
    ]
    if len(ms) > 1:
        lines.append(f"{len(ms) - 1} autre(s) piste(s) disponible(s).")
    return " ".join(lines)


def explain(match_result: dict, question: str = "", history=None, api_key="") -> dict:
    payload = _sanitize(match_result)
    api_key = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()

    if not api_key:
        return {"text": _offline_summary(payload), "online": False}

    user_content = (
        "Résultat de recherche (JSON) :\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    if question:
        user_content += f"\n\nQuestion du technicien : {question}"

    messages = list(history or [])
    messages.append({"role": "user", "content": user_content})

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 700,
        "system": SYSTEM,
        "messages": messages,
    }).encode("utf-8")

    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")

    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        return {"text": text.strip() or _offline_summary(payload), "online": True}
    except Exception as e:
        return {
            "text": _offline_summary(payload) + f"\n\n(Assistant en ligne indisponible : {e})",
            "online": False,
        }


SYSTEM_ZONES = (
    "Tu es un assistant expert en reprogrammation moteur (ECU). On te donne la "
    "liste des ZONES d'un fichier qui ont été modifiées entre un fichier d'origine "
    "et sa solution, décrites uniquement par des métadonnées (offset, taille, forme "
    "de la courbe, motif de changement, plages de valeurs) — JAMAIS les octets bruts. "
    "Pour chaque zone, propose en français la fonction la plus probable (ex. : axe "
    "régime, axe charge, table de couple, table d'injection, limiteur, table de "
    "pression de rail, désactivation FAP/EGR/SCR, drapeau d'activation, code). "
    "Appuie-toi sur la plateforme ECU et le type de solution. Sois honnête : si tu "
    "n'es pas sûr, mets 'incertain'. Ne prétends pas connaître une adresse de carto "
    "précise que tu ne peux pas déduire. Réponds UNIQUEMENT par un tableau JSON, sans "
    "texte autour : [{\"i\": <indice>, \"label\": \"...\", \"confidence\": "
    "\"faible|moyenne|élevée\"}]."
)


def label_zones(summary: dict, api_key="") -> dict:
    """Étiquetage sémantique des zones modifiées via l'API.
    `summary` ne contient que des métadonnées (offsets, tailles, formes, plages),
    jamais d'octets ni la base. Retourne {'online': bool, 'labels': [...], ...}."""
    api_key = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not api_key:
        return {"online": False,
                "error": "IA non configurée (variable ANTHROPIC_API_KEY absente)."}

    user_content = ("Zones modifiées (JSON, sans aucun octet brut) :\n"
                    + json.dumps(summary, ensure_ascii=False, indent=2))
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 900,
        "system": SYSTEM_ZONES,
        "messages": [{"role": "user", "content": user_content}],
    }).encode("utf-8")

    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")

    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()
        # extraire le tableau JSON même si le modèle entoure de ``` ou de texte
        s, e = text.find("["), text.rfind("]")
        labels = []
        if s >= 0 and e > s:
            try:
                labels = json.loads(text[s:e + 1])
            except Exception:
                labels = []
        return {"online": True, "labels": labels, "raw": text if not labels else ""}
    except Exception as ex:
        return {"online": False, "error": f"Assistant en ligne indisponible : {ex}"}


def ping(api_key="") -> dict:
    """Vérifie qu'une clé API est valide avec une requête minimale."""
    api_key = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not api_key:
        return {"ok": False, "error": "Aucune clé fournie."}
    body = json.dumps({
        "model": MODEL, "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    try:
        with urllib.request.urlopen(req, timeout=20):
            return {"ok": True, "model": MODEL}
    except urllib.error.HTTPError as e:
        msg = "Clé refusée" if e.code in (401, 403) else f"Erreur HTTP {e.code}"
        return {"ok": False, "error": msg}
    except Exception as ex:
        return {"ok": False, "error": f"Connexion impossible : {ex}"}
