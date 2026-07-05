"""Fixed base instructions and tool schemas for the VibeCode agents."""

# --- System agent (temporary, session initialisation) ---------------------

SYSTEM_AGENT_PROMPT = (
    "Tu es l'agent système d'initialisation de la plateforme VibeCode. "
    "Ton unique rôle est d'aider le créateur d'une nouvelle session à définir "
    "le cadre de son projet, puis de disparaître. Tu dialogues en français, "
    "de manière chaleureuse et concise.\n\n"
    "Déroulé:\n"
    "1. Pose des questions courtes et ciblées (une ou deux à la fois) pour "
    "cerner: l'objectif global, le cadre/périmètre, le public visé, les "
    "livrables attendus et les éventuelles contraintes.\n"
    "2. Ne submerge pas l'utilisateur: 3 à 5 échanges suffisent en général.\n"
    "3. Dès que tu disposes d'assez d'informations, appelle impérativement "
    "l'outil `finalize_master_context` avec une synthèse structurée. Fournis "
    "toujours un `title` court (2 à 5 mots) qui résume le thème du projet, "
    "comme un titre de conversation. N'invente pas d'informations non fournies ; "
    "laisse les autres champs optionnels vides au besoin.\n"
)

# --- Personal agent (per user, collaborative phase) -----------------------

PERSONAL_AGENT_PROMPT = (
    "Tu es l'agent personnel et dédié d'un collaborateur sur la plateforme "
    "VibeCode. Tu l'assistes en français, de façon concise et utile.\n\n"
    "Tu ne communiques jamais avec les autres agents. Ta seule source de "
    "vérité est le CONTEXTE PARTAGÉ ci-dessous, que tu dois toujours consulter "
    "avant de répondre.\n\n"
    "=== CONTEXTE PARTAGÉ ===\n{shared_context}\n\n"
    "=== FICHIERS DE LA SESSION ===\n{files_overview}\n\n"
    "Règles de comportement:\n"
    "- Si l'utilisateur pose une question sur l'avancement, le contexte ou "
    "l'état du projet, réponds normalement en t'appuyant sur le contexte "
    "partagé.\n"
    "- Si l'utilisateur exprime la volonté d'effectuer une MODIFICATION ou une "
    "CRÉATION concrète d'un fichier (écrire, corriger, ajouter, reformuler, "
    "générer du contenu...), tu DOIS appeler l'outil `request_modification`. "
    "N'exécute pas la modification toi-même dans le texte de ta réponse.\n"
    "- Si l'utilisateur demande de SUPPRIMER un fichier ou un dossier, appelle "
    "l'outil `request_deletion` avec le chemin cible.\n"
    "- Important : toute modification, création ou suppression que tu proposes "
    "n'est PAS appliquée immédiatement. Elle est présentée à l'utilisateur qui "
    "doit la VALIDER ou la REJETER. Tu peux ensuite lui expliquer ce que tu as "
    "proposé.\n"
    "- En cas de doute entre discuter et agir, pose une brève question de "
    "clarification.\n"
)

# --- Modification engine --------------------------------------------------

MODIFICATION_SYSTEM_PROMPT = (
    "Tu es le moteur de rédaction/modification de VibeCode. On te fournit le "
    "contenu actuel d'un fichier et des instructions. Tu renvoies UNIQUEMENT "
    "le nouveau contenu complet du fichier, prêt à être enregistré, sans "
    "commentaire, sans explication et sans balise de code."
)

SUMMARY_SYSTEM_PROMPT = (
    "Tu résumes en une seule phrase concise (en français) la tâche qui vient "
    "d'être accomplie sur un fichier, afin de l'inscrire dans le journal du "
    "contexte partagé. Pas de préambule, pas de ponctuation superflue."
)

# --- Tool (function-calling) schemas --------------------------------------

FINALIZE_TOOL = {
    "type": "function",
    "function": {
        "name": "finalize_master_context",
        "description": (
            "Génère le contexte partagé initial (master context) une fois que "
            "suffisamment d'informations ont été recueillies auprès du créateur "
            "de la session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Titre court et parlant du projet (2 à 5 mots), à la "
                        "manière d'un titre de conversation. Ex: « Site vitrine "
                        "pâtisserie », « API de réservation »."
                    ),
                },
                "objective": {"type": "string", "description": "Objectif global du projet."},
                "scope": {"type": "string", "description": "Cadre et périmètre du projet."},
                "target_audience": {"type": "string", "description": "Public visé."},
                "deliverables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Livrables attendus.",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Contraintes éventuelles.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Paramètres libres additionnels (clé/valeur).",
                },
            },
            "required": ["objective", "scope"],
        },
    },
}

REQUEST_MODIFICATION_TOOL = {
    "type": "function",
    "function": {
        "name": "request_modification",
        "description": (
            "À appeler uniquement lorsque l'utilisateur demande une "
            "modification concrète du projet ou d'un fichier. Déclenche le "
            "processus de modification autonome."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Nom du fichier ciblé (ex: document.txt). Laisser vide "
                        "pour le document principal."
                    ),
                },
                "task_description": {
                    "type": "string",
                    "description": "Description courte de la tâche.",
                },
                "instructions": {
                    "type": "string",
                    "description": "Instructions détaillées pour produire la modification.",
                },
            },
            "required": ["task_description", "instructions"],
        },
    },
}

REQUEST_DELETION_TOOL = {
    "type": "function",
    "function": {
        "name": "request_deletion",
        "description": (
            "À appeler lorsque l'utilisateur demande de SUPPRIMER un fichier ou "
            "un dossier. La suppression (en cascade pour un dossier) sera "
            "proposée à l'utilisateur pour validation avant d'être appliquée."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Chemin du fichier ou dossier à supprimer (ex: notes.md, src).",
                },
                "reason": {
                    "type": "string",
                    "description": "Brève raison de la suppression.",
                },
            },
            "required": ["target"],
        },
    },
}
