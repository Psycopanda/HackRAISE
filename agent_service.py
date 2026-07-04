import json
from database import sessions_collection, files_collection


async def call_mistral_api(prompt: str, context_data: dict) -> dict:
    """
    Fonction simulée pour l'API Mistral.
    À terme, intégration de la clé API et requêtes HTTP asynchrones.
    L'agent est forcé de répondre en JSON avec un champ "type": "chat" ou "action".
    """
    # TODO: Remplacer par httpx.post vers api.mistral.ai avec la clé d'authentification
    pass


async def process_personal_agent_interaction(session_id: str, agent_id: str, user_message: str) -> dict:
    # 1. Lire la source de vérité unique (Contexte partagé dynamique)
    session = await sessions_collection.find_one({"_id": session_id})
    if not session:
        return {"error": "Session introuvable"}

    dynamic_context = session.get("dynamic_context", {})

    # 2. Construire le prompt pour Mistral
    system_prompt = f"""
    Tu es l'agent IA personnel {agent_id}. Ton contexte partagé est : {dynamic_context}.
    Si l'utilisateur discute, réponds ("type": "chat").
    S'il te demande une modification de fichier, vérifie le contexte. Si la tâche n'est pas dans 'active_intents', génère une action ("type": "action", "task": "...", "target_file": "...").
    Ne communique jamais avec d'autres agents, lis uniquement ce contexte.
    """

    # 3. Appel à Mistral (mocké pour la structure)
    mistral_response = await call_mistral_api(system_prompt + "\nUser: " + user_message, dynamic_context)

    # --- LOGIQUE CONDITIONNELLE ---
    if mistral_response.get("type") == "action":
        # Stratégie d'Intent Locking Atomique
        target_file = mistral_response["target_file"]
        task_desc = mistral_response["task"]

        # Mise à jour atomique : on push l'intention SEULEMENT si le fichier n'est pas déjà verrouillé
        update_result = await sessions_collection.update_one(
            {
                "_id": session_id,
                "dynamic_context.active_intents.target_file": {"$ne": target_file}
            },
            {
                "$push": {
                    "dynamic_context.active_intents": {
                        "agent_id": agent_id,
                        "task": task_desc,
                        "target_file": target_file
                    }
                }
            }
        )

        if update_result.modified_count == 1:
            return {"status": "intent_locked", "message": f"Je commence à travailler sur : {task_desc}",
                    "task": task_desc}
        else:
            return {"status": "conflict",
                    "message": "Un autre agent travaille déjà sur ce fichier. Que puis-je faire d'autre ?"}

    # Si c'est juste du texte
    return {"status": "chat", "message": mistral_response.get("content")}