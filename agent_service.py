import os
import json
from datetime import datetime
import httpx
from database import sessions_collection, files_collection, messages_collection
from langchain.memory import ConversationBufferMemory

class PersonalAgent:
    def __init__(self, session_id: str, agent_id: str):
        self.session_id = session_id
        self.agent_id = agent_id
        
        # Clé API Mistral (pense à l'ajouter dans tes variables d'environnement)
        self.mistral_api_key = os.getenv("MISTRAL_API_KEY", "TA_CLE_MISTRAL")
        self.mistral_url = "https://api.mistral.ai/v1/chat/completions"
        
        # Initialisation de la mémoire tampon LangChain
        self.memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        
        # Instructions système strictes forçant le format de sortie JSON
        self.system_instruction = (
            "Tu es l'agent IA personnel d'un développeur sur la plateforme collaborative VibeCode.\n"
            "Tu agis de manière autonome et tu ne communiques JAMAIS directement avec les autres agents.\n"
            "Ton unique source de vérité est le contexte partagé fourni.\n\n"
            "Tu dois impérativement répondre sous la forme d'un objet JSON unique respectant STRICTEMENT l'un de ces deux schémas :\n"
            "Pour une simple discussion :\n"
            '{"type": "chat", "content": "Ton message de réponse ici."}\n\n'
            "Pour une demande de modification de fichier explicite de l'utilisateur :\n"
            '{"type": "action", "task": "Description concise de la modification", "target_file": "nom_du_fichier.txt"}\n'
        )

    async def _hydrate_memory(self):
        """Recharge l'historique de discussion depuis MongoDB vers la mémoire LangChain."""
        self.memory.clear()
        cursor = messages_collection.find({
            "session_id": self.session_id,
            "agent_id": self.agent_id
        }).sort("timestamp", 1)
        
        async for msg in cursor:
            if msg["role"] == "user":
                self.memory.chat_memory.add_user_message(msg["content"])
            elif msg["role"] == "assistant":
                self.memory.chat_memory.add_ai_message(msg["content"])

    async def _persist_message(self, role: str, content: str):
        """Enregistre un message dans la base MongoDB."""
        await messages_collection.insert_one({
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow()
        })

    async def _get_shared_context(self) -> dict:
        """Extrait le contexte dynamique de la session."""
        session = await sessions_collection.find_one({"_id": self.session_id})
        return session.get("dynamic_context", {}) if session else {}

    async def _call_mistral(self, user_message: str, chat_history: list, context: dict) -> dict:
        """Exécute l'appel HTTP asynchrone vers l'API de Mistral avec un format JSON strict."""
        headers = {
            "Authorization": f"Bearer {self.mistral_api_key}",
            "Content-Type": "application/json"
        }
        
        # Construction des messages pour l'API Mistral (System + Historique + Contexte + Nouveau message)
        messages = [{"role": "system", "content": self.system_instruction}]
        
        # Formatage de l'historique LangChain pour Mistral
        for msg in chat_history:
            role = "user" if msg.type == "human" else "assistant"
            messages.append({"role": "user", "content": msg.content})
            
        # Injection du contexte partagé dynamique dans l'échange
        messages.append({
            "role": "system",
            "content": f"Voici l'état actuel du contexte partagé dynamique de la session : {json.dumps(context)}"
        })
        
        # Ajout du dernier message utilisateur
        messages.append({"role": "user", "content": user_message})
        
        payload = {
            "model": "mistral-large-latest",
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(self.mistral_url, headers=headers, json=payload, timeout=30.0)
                if response.status_code == 200:
                    result = response.json()
                    content_str = result["choices"][0]["message"]["content"]
                    return json.loads(content_str)
                else:
                    return {"type": "chat", "content": f"Erreur API Mistral (Code {response.status_code})."}
            except Exception as e:
                return {"type": "chat", "content": f"Erreur de connexion à l'agent : {str(e)}"}

    async def interact(self, user_message: str) -> dict:
        """Gère le traitement complet d'une interaction utilisateur."""
        await self._hydrate_memory()
        await self._persist_message(role="user", content=user_message)
        
        dynamic_context = await self._get_shared_context()
        memory_variables = self.memory.load_memory_variables({})
        chat_history = memory_variables.get("chat_history", [])
        
        mistral_response = await self._call_mistral(user_message, chat_history, dynamic_context)
        
        if mistral_response.get("type") == "action":
            return await self._try_execute_action(mistral_response)
            
        ai_reply = mistral_response.get("content", "Je n'ai pas pu analyser votre demande.")
        await self._persist_message(role="assistant", content=ai_reply)
        return {"status": "chat", "message": ai_reply}

    async def _try_execute_action(self, action_data: dict) -> dict:
        """Tente de réserver une tâche via notre stratégie de verrouillage atomique."""
        target_file = action_data.get("target_file", "document_principal.txt")
        task_desc = action_data.get("task", "Modification de fichier")
        
        # Requête atomique avec condition d'absence du verrou
        result = await sessions_collection.update_one(
            {
                "_id": self.session_id,
                "dynamic_context.active_intents.target_file": {"$ne": target_file}
            },
            {
                "$push": {
                    "dynamic_context.active_intents": {
                        "agent_id": self.agent_id,
                        "task": task_desc,
                        "target_file": target_file,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                }
            }
        )
        
        if result.modified_count == 1:
            ai_reply = f"J'ai détecté votre demande de modification. J'ai verrouillé le fichier '{target_file}' pour accomplir la tâche suivante : {task_desc}."
            await self._persist_message(role="assistant", content=ai_reply)
            return {"status": "intent_locked", "message": ai_reply, "target_file": target_file, "task": task_desc}
        
        conflict_reply = f"Je ne peux pas modifier le fichier '{target_file}' pour le moment car un autre agent collabore actuellement dessus. Veuillez patienter ou choisir une autre tâche."
        await self._persist_message(role="assistant", content=conflict_reply)
        return {"status": "conflict", "message": conflict_reply}

    async def release_all_intents(self):
        """Supprime tous les verrous d'intention posés par cet agent."""
        await sessions_collection.update_one(
            {"_id": self.session_id},
            {"$pull": {"dynamic_context.active_intents": {"agent_id": self.agent_id}}}
        )