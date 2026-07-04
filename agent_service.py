import os
import json
from datetime import datetime
from database import sessions_collection, messages_collection, files_collection
from langchain_mistralai import ChatMistralAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

class SystemAgent:
    """Agent temporaire chargé de cadrer le projet et de générer le Master Context."""
    def __init__(self, session_id: str, agent_id: str):
        self.session_id = session_id
        self.agent_id = agent_id
        
        # Initialisation du LLM via le SDK officiel de LangChain
        api_key = os.getenv("MISTRAL_API_KEY")
        self.llm = ChatMistralAI(
            model="mistral-large-latest", 
            api_key=api_key,
            temperature=0.3
        ).bind(response_format={"type": "json_object"})
        
        self.system_instruction = (
            "Tu es l'Agent Système de la plateforme collaborative VibeCode.\n"
            "Ton rôle est d'interroger le créateur d'une session pour définir les bases, les objectifs "
            "et le cadre de son projet (exposé, rédaction, code ou autre).\n"
            "Règles strictes :\n"
            "1. Pose une seule question claire et concise à la fois.\n"
            "2. Ne fais pas d'introduction inutile, va droit au but.\n"
            "3. Après 2 ou 3 échanges maximum, si tu as compris l'objectif global et le thème, "
            "tu dois clore l'interview et générer le master context.\n\n"
            "Tu dois impérativement répondre sous la forme d'un objet JSON unique respectant STRICTEMENT l'un de ces deux schémas :\n\n"
            "Si tu continues l'interview et poses une question :\n"
            '{"status": "interviewing", "content": "Ta question ici ?"}\n\n'
            "Si tu as collecté assez d'informations et que tu valides le projet :\n"
            '{"status": "complete", "master_context": {"project_title": "Titre du projet", "project_goal": "Description globale de l\'objectif", "language": "fr", "guidelines": ["Règle 1", "Règle 2"]}}\n'
        )

    async def _build_history(self) -> list:
        """Récupère l'historique en base et le formate en objets de messages LangChain."""
        messages = [SystemMessage(content=self.system_instruction)]
        
        cursor = messages_collection.find({
            "session_id": self.session_id,
            "agent_id": self.agent_id
        }).sort("timestamp", 1)
        
        async for msg in cursor:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))
        return messages

    async def _persist_message(self, role: str, content: str):
        await messages_collection.insert_one({
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow()
        })

    async def interact(self, user_message: str) -> dict:
        """Pilote l'échange d'initialisation avec le créateur."""
        if user_message:
            await self._persist_message(role="user", content=user_message)
        
        # Construction de l'historique complet pour LangChain
        langchain_messages = await self._build_history()
        
        try:
            # Invocation asynchrone native du SDK LangChain
            response = await self.llm.ainvoke(langchain_messages)
            mistral_json = json.loads(response.content)
            
            if mistral_json.get("status") == "interviewing":
                ai_reply = mistral_json.get("content")
                await self._persist_message(role="assistant", content=ai_reply)
                return {"status": "interviewing", "message": ai_reply}
            
            elif mistral_json.get("status") == "complete":
                return {"status": "complete", "master_context": mistral_json.get("master_context")}
                
            return {"status": "error", "message": "Format de réponse de l'agent invalide."}
        except Exception as e:
            return {"status": "error", "message": f"Erreur technique de l'agent : {str(e)}"}


class PersonalAgent:
    """Agent personnel opérationnel en tâche de fond une fois la session active."""
    def __init__(self, session_id: str, agent_id: str):
        self.session_id = session_id
        self.agent_id = agent_id
        
        api_key = os.getenv("MISTRAL_API_KEY", "TA_CLE_MISTRAL")
        self.llm = ChatMistralAI(
            model="mistral-large-latest", 
            api_key=api_key,
            temperature=0.2
        ).bind(response_format={"type": "json_object"})
        
        self.system_instruction = (
            "Tu es l'agent IA personnel d'un membre de l'équipe sur la plateforme collaborative VibeCode.\n"
            "Tu agis de manière autonome et tu ne communiques JAMAIS directement avec les autres agents.\n"
            "Ton unique source de vérité est le contexte partagé fourni.\n\n"
            "Tu dois impérativement répondre sous la forme d'un objet JSON unique respectant STRICTEMENT l'un de ces deux schémas :\n"
            "Pour une simple discussion :\n"
            '{"type": "chat", "content": "Ton message de réponse ici."}\n\n'
            "Pour une demande de modification de fichier explicite de l'utilisateur :\n"
            '{"type": "action", "task": "Description concise de la modification à apporter", "target_file": "nom_du_fichier.txt"}\n'
        )

    async def _build_history(self, context: dict) -> list:
        messages = [
            SystemMessage(content=self.system_instruction),
            SystemMessage(content=f"Voici l'état actuel du contexte partagé de la session : {json.dumps(context)}")
        ]
        
        cursor = messages_collection.find({
            "session_id": self.session_id,
            "agent_id": self.agent_id
        }).sort("timestamp", 1)
        
        async for msg in cursor:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))
        return messages

    async def _persist_message(self, role: str, content: str):
        await messages_collection.insert_one({
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow()
        })

    async def _get_shared_context(self) -> dict:
        session = await sessions_collection.find_one({"_id": self.session_id})
        return session.get("dynamic_context", {}) if session else {}

    async def interact(self, user_message: str) -> dict:
        await self._persist_message(role="user", content=user_message)
        
        dynamic_context = await self._get_shared_context()
        langchain_messages = await self._build_history(dynamic_context)
        
        try:
            response = await self.llm.ainvoke(langchain_messages)
            mistral_response = json.loads(response.content)
            
            if mistral_response.get("type") == "action":
                return await self._try_execute_action(mistral_response)
                
            ai_reply = mistral_response.get("content", "Je n'ai pas pu analyser votre demande.")
            await self._persist_message(role="assistant", content=ai_reply)
            return {"status": "chat", "message": ai_reply}
        except Exception:
            error_msg = "L'agent rencontre des difficultés à formuler sa réponse."
            await self._persist_message(role="assistant", content=error_msg)
            return {"status": "chat", "message": error_msg}

    async def _try_execute_action(self, action_data: dict) -> dict:
        target_file = action_data.get("target_file", "document_principal.txt")
        task_desc = action_data.get("task", "Modification de fichier")
        
        # Acquisition atomique du verrou en base de données
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
            ai_reply = f"J'ai bloqué le fichier '{target_file}' et je commence la rédaction pour : {task_desc}."
            await self._persist_message(role="assistant", content=ai_reply)
            return {"status": "intent_locked", "message": ai_reply, "target_file": target_file, "task": task_desc}
        
        conflict_reply = f"Le fichier '{target_file}' est déjà en cours de modification par un autre agent."
        await self._persist_message(role="assistant", content=conflict_reply)
        return {"status": "conflict", "message": conflict_reply}

    async def execute_autonomous_task(self, task_desc: str, target_file: str, websocket_manager):
        """Tâche asynchrone en arrière-plan qui génère la modification de texte et met à jour les fichiers."""
        try:
            # 1. Lecture de l'état actuel du document cible
            file_doc = await files_collection.find_one({"session_id": self.session_id, "filename": target_file})
            current_content = file_doc.get("content", "") if file_doc else ""
            
            # 2. Demande de génération au LLM
            generation_instruction = [
                SystemMessage(content=(
                    "Tu es un rédacteur expert autonome. Tu dois appliquer les modifications demandées dans le texte actuel.\n"
                    "Tu dois renvoyer l'INTÉGRALITÉ du texte final mis à jour.\n"
                    "Format attendu : Réponds impérativement avec un JSON unique contenant la clé 'new_content'."
                )),
                HumanMessage(content=(
                    f"Tâche à accomplir : {task_desc}\n"
                    f"Contenu actuel du fichier :\n---\n{current_content}\n---\n"
                    "Génère le texte final corrigé et augmenté."
                ))
            ]
            
            response = await self.llm.ainvoke(generation_instruction)
            result_json = json.loads(response.content)
            updated_content = result_json.get("new_content", current_content)
            
            # 3. Écriture en base avec Contrôle de Concurrence Optimiste
            await files_collection.update_one(
                {"session_id": self.session_id, "filename": target_file},
                {"$set": {"content": updated_content}, "$inc": {"version": 1}}
            )
            
            # 4. Enregistrement de l'action dans le journal partagé
            log_entry = f"[{datetime.utcnow().isoformat()}] L'Agent de {self.agent_id} a complété : {task_desc}"
            await sessions_collection.update_one(
                {"_id": self.session_id},
                {"$push": {"dynamic_context.completed_tasks_log": log_entry}}
            )
            
            # 5. Broadcast en temps réel à toute l'équipe
            await websocket_manager.broadcast_to_session(self.session_id, {
                "event": "file_modified",
                "client_id": f"agent_{self.agent_id}",
                "filename": target_file,
                "content": updated_content
            })
            
        except Exception as e:
            print(f"Erreur d'exécution de tâche sur l'agent {self.agent_id} : {e}")
        finally:
            # RÈGLE D'OR : Libération inconditionnelle du verrou
            await self.release_all_intents()
            await websocket_manager.broadcast_to_session(self.session_id, {
                "event": "context_updated",
                "message": f"L'agent de {self.agent_id} a terminé son travail et libéré le fichier '{target_file}'."
            })

    async def release_all_intents(self):
        await sessions_collection.update_one(
            {"_id": self.session_id},
            {"$pull": {"dynamic_context.active_intents": {"agent_id": self.agent_id}}}
        )