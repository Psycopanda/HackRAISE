import uuid
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from database import sessions_collection, files_collection
from ws_manager import manager
from agent_service import PersonalAgent

app = FastAPI(title="VibeCode Backend Core API")

class InitSessionRequest(BaseModel):
    initial_prompt: str

@app.post("/sessions/init")
async def initialize_session(req: InitSessionRequest):
    """
    Phase d'initialisation : Génère le Master Context initial à partir des directives
    de l'agent système et prépare l'environnement de la session.
    """
    session_code = str(uuid.uuid4())[:8].upper()
    
    # Structure initiale du document de session (Master & Dynamic)
    master_context = {
        "project_goal": f"Création conjointe basée sur le prompt : {req.initial_prompt}",
        "parameters": {"language": "fr", "mode": "text_collaboration"}
    }
    
    await sessions_collection.insert_one({
        "_id": session_code,
        "master_context": master_context,
        "dynamic_context": {
            "active_intents": [],
            "completed_tasks_log": []
        },
        "version": 1
    })
    
    # Création du document texte partagé initial pour la zone de gauche
    await files_collection.insert_one({
        "session_id": session_code,
        "filename": "document_principal.txt",
        "content": "Bienvenue dans votre espace collaboratif VibeCode.\n",
        "version": 1
    })
    
    return {"session_code": session_code, "master_context": master_context}

@app.websocket("/ws/{session_id}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, client_id: str):
    """
    Passerelle de communication temps réel. Centralise les frappes de texte (gauche)
    et le chat avec l'agent IA personnel (droite).
    """
    await manager.connect(websocket, session_id)
    
    # Instanciation de l'agent dédié pour ce canal WebSocket
    agent = PersonalAgent(session_id=session_id, agent_id=client_id)
    
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            action_type = data.get("action")
            
            # --- ZONE GAUCHE : Édition collaborative du texte ---
            if action_type == "text_update":
                target_file = data.get("filename", "document_principal.txt")
                new_content = data.get("content", "")
                
                # Mise à jour globale du fichier dans MongoDB
                await files_collection.update_one(
                    {"session_id": session_id, "filename": target_file},
                    {"$set": {"content": new_content}, "$inc": {"version": 1}}
                )
                
                # Broadcast de la modification à tous les autres terminaux connectés
                await manager.broadcast_to_session(session_id, {
                    "event": "file_modified",
                    "client_id": client_id,
                    "filename": target_file,
                    "content": new_content
                })
                
            # --- ZONE DROITE : Interaction Chat & Agent IA ---
            elif action_type == "agent_chat":
                user_msg = data.get("message", "")
                
                # Sollicitation de la logique de l'agent
                agent_response = await agent.interact(user_msg)
                
                if agent_response["status"] == "intent_locked":
                    # Alerte globale : Un agent s'est emparé d'une tâche
                    await manager.broadcast_to_session(session_id, {
                        "event": "context_updated",
                        "message": f"L'agent de l'utilisateur {client_id} a verrouillé le fichier '{agent_response['target_file']}'."
                    })
                    
                    # NOTE : Le traitement autonome en tâche de fond de l'agent (modification effective 
                    # du fichier, écriture du log et libération du verrou) s'exécute à la suite de cette étape.
                
                # Envoi immédiat de la réponse textuelle dans le panneau droit de l'utilisateur
                await websocket.send_text(json.dumps({
                    "event": "agent_reply",
                    "status": agent_response["status"],
                    "message": agent_response["message"]
                }))

    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
        # Nettoyage automatique : Libération des verrous de l'agent pour éviter les blocages permanents
        await agent.release_all_intents()
        await manager.broadcast_to_session(session_id, {
            "event": "user_left",
            "client_id": client_id
        })