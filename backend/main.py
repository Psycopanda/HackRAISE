import uuid
import json
import asyncio
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status
from pydantic import BaseModel
from database import sessions_collection, files_collection
from agent_service import SystemAgent, PersonalAgent
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="VibeCode Backend Core API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Autorise toutes les origines pour le dev local (fichiers file:// ou localhost)
    allow_credentials=True,
    allow_methods=["*"],  # Autorise POST, GET, OPTIONS, etc.
    allow_headers=["*"],  # Autorise tous les headers (Content-Type, Authorization...)
)

class ConnectionManager:
    """Gestionnaire temps réel des routeurs WebSockets par session."""
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    async def broadcast_to_session(self, session_id: str, message: dict):
        if session_id in self.active_connections:
            payload = json.dumps(message)
            for connection in self.active_connections[session_id]:
                try:
                    await connection.send_text(payload)
                except Exception:
                    pass

manager = ConnectionManager()

def merge_attachments_into_message(message: str, attachments: list) -> str:
    """Inline any uploaded document text into the prompt sent to the LLM."""
    docs_text = "\n\n".join(
        f"[Document joint: {att.get('filename', 'fichier')}]\n{att.get('textContent', '')}"
        for att in attachments
        if att.get("textContent")
    )
    if not docs_text:
        return message
    return f"{message}\n\n{docs_text}" if message else docs_text

class InitSessionResponse(BaseModel):
    session_code: str
    status: str

@app.post("/sessions/init", response_model=InitSessionResponse)
async def initialize_session():
    """Crée l'instance de session vierge en attente d'initialisation par son créateur."""
    session_code = str(uuid.uuid4())[:8].upper()
    
    await sessions_collection.insert_one({
        "_id": session_code,
        "status": "INITIALIZING",
        "master_context": {},
        "dynamic_context": {
            "active_intents": [],
            "completed_tasks_log": []
        },
        "version": 1
    })
    return {"session_code": session_code, "status": "INITIALIZING"}

@app.get("/sessions/check/{session_id}")
async def check_session_status(session_id: str):
    """Point de contrôle d'accès pour les collaborateurs externes."""
    session = await sessions_collection.find_one({"_id": session_id.upper()})
    if not session:
        raise HTTPException(status_code=404, detail="Session introuvable")
    return {"session_code": session_id, "status": session.get("status")}

@app.websocket("/ws/{session_id}/{client_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, client_id: str):
    session_id = session_id.upper()
    session = await sessions_collection.find_one({"_id": session_id})
    
    if not session:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, session_id)
    
    try:
        # Initialisation dynamique de la bonne typologie d'agent au branchement
        if session.get("status") == "INITIALIZING":
            system_agent = SystemAgent(session_id=session_id, agent_id=client_id)
            init_response = await system_agent.interact(user_message="")
            await websocket.send_text(json.dumps({
                "event": "agent_reply",
                "status": "interviewing",
                "message": init_response.get("message", "Bonjour ! Parle-moi de ton projet pour configurer l'espace.")
            }))
        else:
            personal_agent = PersonalAgent(session_id=session_id, agent_id=client_id)

        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            action_type = data.get("action")
            
            current_session = await sessions_collection.find_one({"_id": session_id})
            is_initializing = current_session.get("status") == "INITIALIZING"

            # --- FLUX INITIALIZATION : Dialogue exclusif créateur <-> Système ---
            if is_initializing:
                if action_type == "agent_chat":
                    user_msg = merge_attachments_into_message(
                        data.get("message", ""), data.get("attachments") or []
                    )
                    agent_res = await system_agent.interact(user_msg)
                    
                    if agent_res["status"] == "interviewing":
                        await websocket.send_text(json.dumps({
                            "event": "agent_reply",
                            "status": "interviewing",
                            "message": agent_res["message"]
                        }))
                        
                    elif agent_res["status"] == "complete":
                        master_ctx = agent_res["master_context"]
                        
                        # Mutation de l'état de la session
                        await sessions_collection.update_one(
                            {"_id": session_id},
                            {"$set": {"status": "ACTIVE", "master_context": master_ctx}}
                        )
                        
                        # Génération du canevas de document initial
                        await files_collection.insert_one({
                            "session_id": session_id,
                            "filename": "document_principal.txt",
                            "content": f"# {master_ctx.get('project_title', 'Sans Titre')}\n\nObjectif : {master_ctx.get('project_goal', '')}\n",
                            "version": 1
                        })
                        
                        # Notification de réussite de la transition d'état
                        await websocket.send_text(json.dumps({
                            "event": "session_activated",
                            "message": "Configuration terminée ! La session passe en mode collaboratif actif.",
                            "master_context": master_ctx
                        }))
                        
                        # Bascule immédiate sur l'agent de collaboration standard
                        personal_agent = PersonalAgent(session_id=session_id, agent_id=client_id)
            
            # --- FLUX COLLABORATION : Multi-utilisateurs et agents autonomes ---
            else:
                if action_type == "text_update":
                    target_file = data.get("filename", "document_principal.txt")
                    new_content = data.get("content", "")
                    
                    await files_collection.update_one(
                        {"session_id": session_id, "filename": target_file},
                        {"$set": {"content": new_content}, "$inc": {"version": 1}}
                    )
                    await manager.broadcast_to_session(session_id, {
                        "event": "file_modified",
                        "client_id": client_id,
                        "filename": target_file,
                        "content": new_content
                    })
                    
                elif action_type == "agent_chat":
                    user_msg = merge_attachments_into_message(
                        data.get("message", ""), data.get("attachments") or []
                    )
                    agent_response = await personal_agent.interact(user_msg)
                    
                    # L'agent a réussi à poser un verrou sur le fichier cible
                    if agent_response["status"] == "intent_locked":
                        await manager.broadcast_to_session(session_id, {
                            "event": "context_updated",
                            "message": f"L'agent de {client_id} a pris la main sur '{agent_response['target_file']}'."
                        })
                        
                        # Déclenchement de la rédaction autonome en arrière-plan sans bloquer le thread WS
                        asyncio.create_task(personal_agent.execute_autonomous_task(
                            task_desc=agent_response["task"],
                            target_file=agent_response["target_file"],
                            websocket_manager=manager
                        ))
                    
                    await websocket.send_text(json.dumps({
                        "event": "agent_reply",
                        "status": agent_response["status"],
                        "message": agent_response["message"]
                    }))

    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
        current_session = await sessions_collection.find_one({"_id": session_id})
        if current_session and current_session.get("status") == "ACTIVE":
            # Libération immédiate des verrous de l'utilisateur déconnecté
            cleanup_agent = PersonalAgent(session_id=session_id, agent_id=client_id)
            await cleanup_agent.release_all_intents()
            
        await manager.broadcast_to_session(session_id, {
            "event": "user_left",
            "client_id": client_id
        })