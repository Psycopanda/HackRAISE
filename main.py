from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
import uuid
import json

from database import sessions_collection, files_collection
from ws_manager import manager
from agent_service import process_personal_agent_interaction

app = FastAPI(title="VibeCode Backend API")


class InitSessionRequest(BaseModel):
    initial_prompt: str


@app.post("/sessions/init")
async def initialize_session(req: InitSessionRequest):
    """
    Phase 1 : L'utilisateur initie le projet. L'agent système crée le Master Context.
    """
    # MOCK: L'agent système de Mistral génèrerait ce JSON après quelques échanges
    master_context = {
        "project_goal": "Rédaction d'un exposé sur l'IA collaborative",
        "parameters": {"language": "fr", "tone": "academic"}
    }

    session_code = str(uuid.uuid4())[:8]  # Code d'accès unique

    await sessions_collection.insert_one({
        "_id": session_code,
        "master_context": master_context,
        "dynamic_context": {
            "active_intents": [],
            "completed_tasks_log": []
        },
        "version": 1
    })

    # Création du fichier vierge initial
    await files_collection.insert_one({
        "session_id": session_code,
        "filename": "document_principal.txt",
        "content": "",
        "version": 1
    })

    return {"session_code": session_code, "master_context": master_context}


@app.websocket("/ws/{session_id}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, client_id: str):
    """
    Phase 2 : Collaboration en temps réel (Humains + Agents)
    """
    await manager.connect(websocket, session_id)
    try:
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)

            action_type = data.get("action")

            # Gestion des modifications humaines en temps réel (OCC géré en frontend/backend)
            if action_type == "text_update":
                # On broadcast la modif aux autres pour le rendu gauche de l'interface
                await manager.broadcast_to_session(session_id, {
                    "event": "file_modified",
                    "client_id": client_id,
                    "content": data.get("content")
                })

            # Gestion de la discussion avec l'agent personnel (Interface droite)
            elif action_type == "agent_chat":
                user_msg = data.get("message")

                # Traitement de la logique conditionnelle de l'agent
                agent_response = await process_personal_agent_interaction(session_id, client_id, user_msg)

                if agent_response["status"] == "intent_locked":
                    # Broadcast à tous que le contexte dynamique a changé (mise à jour UI potentielle)
                    await manager.broadcast_to_session(session_id, {
                        "event": "context_updated",
                        "message": f"Agent de {client_id} a verrouillé une tâche."
                    })

                    # L'agent commence son travail asynchrone ici, modifie le fichier, puis déverrouille (non implémenté ici pour concision)

                # Réponse dans le chat privé de l'utilisateur
                await websocket.send_text(json.dumps({
                    "event": "agent_reply",
                    "message": agent_response["message"]
                }))

    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
        await manager.broadcast_to_session(session_id, {"event": "user_left", "client_id": client_id})