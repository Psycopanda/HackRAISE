# VibeCode — Backend API

Backend temps réel pour VibeCode : une plateforme de collaboration entre
utilisateurs humains et agents IA (Mistral). Construit avec **FastAPI**,
**WebSockets**, **MongoDB** (Motor, async) et l'**API Mistral**.

> Ce dépôt contient le backend **et** un frontend HTML/CSS/JS vanilla servi
> par FastAPI. Backend : logique serveur, base de données, permissions/verrous
> et orchestration des agents IA. Frontend : voir la section 6.

---

## 1. Démarrage rapide

```powershell
# 1. Environnement virtuel
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Dépendances
pip install -r requirements.txt

# 3. Configuration
copy .env.example .env
#   -> renseigner MISTRAL_API_KEY dans .env

# 4. Lancer l'API
uvicorn app.main:app --reload
```

- Documentation interactive : `http://localhost:8000/docs`
- Santé : `http://localhost:8000/api/health`
- **Interface web : `http://localhost:8000`** (le frontend est servi par FastAPI)

Les identifiants MongoDB (`hackathon` / `hackathon`, cluster
`cluster0.feewznr.mongodb.net`) sont les valeurs par défaut ; l'URI est
construite avec `urllib.parse.quote_plus`. Sans `MISTRAL_API_KEY`, l'API
démarre mais les agents renvoient un message d'erreur explicite tant que la clé
n'est pas fournie.

---

## 2. Stratégie de gestion des conflits

**Verrouillage optimiste, à granularité fine, par revendication atomique avec
baux à expiration.** Objectif : personne n'est jamais bloqué.

1. **Lecture toujours libre.** Le contexte partagé et les fichiers sont
   lisibles à tout moment, sans verrou. La majorité des accès étant des
   lectures, il n'y a quasiment aucune contention.
2. **Revendication atomique (`acquire_lock`).** Avant toute modification,
   l'agent tente de verrouiller **la ressource précise** (un fichier, pas toute
   la session) via un unique `find_one_and_update(upsert=True)` sur la
   collection `locks`, protégée par un index unique `(session_id,
   resource_id)`. MongoDB garantit qu'un seul agent gagne. L'opération réussit
   ou échoue **instantanément** — jamais d'attente.
3. **Baux à expiration (TTL).** Chaque verrou porte un `expires_at`. Un index
   TTL supprime automatiquement les verrous expirés : un agent qui plante ou se
   déconnecte ne bloque donc jamais une ressource durablement.
4. **Concurrence optimiste sur les fichiers (OCC).** Chaque fichier a un champ
   `version`. Les écritures ne réussissent que si la version attendue
   correspond (`find_one_and_update` + `$inc`). En cas de divergence, l'agent
   relit et réessaie. C'est le filet de sécurité contre toute écriture
   concurrente (y compris les éditions humaines directes).
5. **Journal d'intentions.** Dès qu'un verrou est obtenu, l'agent inscrit son
   intention dans `active_tasks` du contexte partagé (`$push` atomique). À la
   fin, la tâche est déplacée vers `completed_tasks` avec un résumé concis
   (`$pull` + `$push` dans une seule mise à jour atomique).
6. **Diffusion temps réel.** Chaque changement (intention, fichier, contexte)
   est diffusé par WebSocket à tous les participants, ce qui réduit la fenêtre
   de conflit à quasiment zéro : les agents « voient » les tâches en cours.
7. **Politique de non-attente.** Si la ressource est déjà verrouillée, l'agent
   n'attend pas : il informe immédiatement l'utilisateur (« un autre
   collaborateur modifie déjà ce fichier ») et aucune tâche en double n'est
   lancée. L'utilisateur sera notifié dès la libération via le broadcast.

Ainsi, la seule étape réellement en compétition (la revendication) est résolue
en une opération atomique de quelques microsecondes, sans verrou global ni
file d'attente bloquante.

---

## 3. Structure de la base de données (MongoDB)

Six collections, séparant proprement sessions, utilisateurs, fichiers, contexte
partagé, verrous et messages.

| Collection       | Rôle                                              | Index clés |
|------------------|---------------------------------------------------|------------|
| `sessions`       | Métadonnées de session, code d'accès, statut      | `access_code` unique *partiel* (string) |
| `users`          | Participants (créateur / collaborateurs)          | `session_id` |
| `files`          | Fichiers de travail + `version` (OCC)             | `session_id` |
| `shared_context` | **Source de vérité** : master context + tâches    | `session_id` unique |
| `locks`          | Verrous éphémères de ressources (baux)            | `(session_id, resource_id)` unique + TTL `expires_at` |
| `messages`       | Historique de chat (mémoire des agents)           | `(session_id, created_at)` |

### `sessions`
```jsonc
{ "_id", "title", "status": "initializing|active|archived",
  "mode": "text|code", "access_code": "A1B2C3", "creator_id", "created_at", "updated_at" }
```
Le `access_code` n'est créé qu'à la finalisation (index unique partiel → plusieurs
sessions peuvent coexister sans code pendant l'initialisation).

### `shared_context` (le cœur de la coordination)
```jsonc
{ "_id", "session_id",
  "master_context": { "title", "objective", "scope", "target_audience",
                      "deliverables": [], "constraints": [], "parameters": {} },
  "state_summary": "…",
  "active_tasks":    [ { "task_id", "user_id", "agent_id", "resource_id",
                         "target", "description", "status", "started_at" } ],
  "completed_tasks": [ { "task_id", "user_id", "description", "summary",
                         "files_touched": [], "completed_at" } ],
  "version", "created_at", "updated_at" }
```

### `files`
```jsonc
{ "_id", "session_id", "name", "type": "text|code", "language",
  "content", "version", "last_modified_by", "created_at", "updated_at" }
```

### `locks`
```jsonc
{ "_id", "session_id", "resource_id": "file:<id>", "owner_user_id",
  "owner_agent_id", "description", "acquired_at", "expires_at" }
```

L'architecture supporte nativement l'évolution vers plusieurs fichiers de code
complexes : `files` est déjà multi-fichiers/typé, `locks` verrouille par
ressource, et `resource_id` peut désigner n'importe quelle granularité future
(fichier, section, symbole…).

---

## 4. Cycle de vie & endpoints

### HTTP (bootstrap et inspection)
| Méthode | Chemin | Description |
|--------|--------|-------------|
| `POST` | `/api/sessions` | Créer une session → renvoie `system_ws_url` |
| `POST` | `/api/sessions/join` | Rejoindre via `access_code` → `session_ws_url` |
| `GET`  | `/api/sessions/{id}` | Infos de session |
| `GET`  | `/api/sessions/{id}/context` | Contexte partagé |
| `GET`  | `/api/sessions/{id}/files` | Liste des fichiers |
| `GET`  | `/api/sessions/{id}/files/{file_id}` | Un fichier |
| `GET`  | `/api/sessions/{id}/locks` | Verrous actifs |
| `GET`  | `/api/health` | Santé |

### WebSockets (temps réel)

**Initialisation — `/ws/system/{session_id}/{user_id}`**
Le créateur discute avec l'agent système jusqu'à la génération du master
context, puis l'agent disparaît et un code d'accès est émis.

**Collaboration — `/ws/session/{session_id}/{user_id}`**
Chat avec l'agent personnel + édition de texte collaborative + présence.

#### Messages client → serveur
```jsonc
{ "type": "chat",      "content": "…" }                                  // parler à l'agent
{ "type": "text_edit", "file_id": "…", "content": "…", "base_version": 3 } // édition humaine (OCC)
{ "type": "cursor",    "file_id": "…", "position": 42 }                   // présence
{ "type": "ping" }
```

#### Messages serveur → client
```jsonc
{ "type": "system_message",       "content": "…" }
{ "type": "master_context_ready", "access_code": "A1B2C3", "master_context": {…}, "session_ws_url": "…" }
{ "type": "snapshot",             "context": {…}, "files": [...], "active_locks": [...], "users": [...] }
{ "type": "agent_status",         "status": "thinking|modifying|idle" }
{ "type": "agent_message",        "content": "…" }
{ "type": "task_claimed",         "task": {…} }     // une intention a été enregistrée
{ "type": "file_update",          "file": { "file_id", "name", "content", "version" } }
{ "type": "task_completed",       "task": { "task_id", "target", "summary", "agent_id" } }
{ "type": "context_update",       "context": {…} }
{ "type": "edit_conflict",        "file": {…} }     // rebaser sur cette version
{ "type": "user_joined" | "user_left", … }
{ "type": "error",                "message": "…" }
{ "type": "pong" }
```

---

## 5. Arborescence

```
app/
  main.py                     # Application FastAPI (lifespan, CORS, routers)
  config.py                   # Paramètres + construction de l'URI MongoDB
  database.py                 # Connexion Motor + accès collections + index
  models/schemas.py           # Schémas Pydantic (I/O + master context)
  services/
    session_service.py        # Cycle de vie des sessions
    context_service.py        # Contexte partagé (source de vérité)
    file_service.py           # CRUD fichiers + OCC
    lock_service.py           # Verrous atomiques (résolution de conflits)
    user_service.py           # Participants
    message_service.py        # Historique de chat
    modification_service.py   # Orchestration du processus de modification
    mistral_service.py        # Client API Mistral (httpx)
  agents/
    prompts.py                # Instructions fixes + schémas d'outils
    system_agent.py           # Agent système (initialisation)
    personal_agent.py         # Agent personnel (détection d'intention)
  websockets/
    connection_manager.py     # Registre des connexions WebSocket
  routers/
    sessions.py               # Endpoints HTTP
    ws.py                     # Endpoints WebSocket
```

---

## 6. Frontend (HTML/CSS/JS vanilla)

Interface minimaliste inspirée de ChatGPT (blanc / gris / noir), servie
directement par FastAPI (`app.mount` sur `/`). Ouvrir `http://localhost:8000`.

Trois vues dynamiques :
1. **Accueil** — bouton « Créer une session » + formulaire « Rejoindre » (PIN +
   pseudo obligatoire).
2. **Création** — chat avec l'agent système (WebSocket) puis affichage du PIN.
3. **Atelier** — écran scindé : éditeur partagé à gauche (temps réel, **jamais
   bloqué/grisé**), chat privé avec l'agent personnel à droite (badges discrets
   d'intention / de tâche terminée), séparateur redimensionnable.

```
frontend/
  index.html                  # 3 vues (home / create / workspace)
  css/styles.css              # Charte grayscale type ChatGPT
  js/
    config.js                 # Résolution des URLs HTTP/WS
    api.js                    # Client REST
    socket.js                 # Wrapper WebSocket (handlers, ping, reconnect)
    protocol.js               # Enveloppe { type, sender, content } + normalize()
    ui.js                     # Helpers DOM, rendu chat, badges, toasts
    editor.js                 # Éditeur collaboratif (non bloquant, OCC)
    resizer.js                # Séparateur redimensionnable
    home.js / create.js / workspace.js   # Contrôleurs de vue
    app.js                    # Point d'entrée
```

Enveloppe de messages applicative `{ type, sender, content }` avec trois
catégories logiques (`chat_message`, `file_update`, `agent_status`) ; le module
`protocol.js` mappe les types du backend sur ce modèle.
