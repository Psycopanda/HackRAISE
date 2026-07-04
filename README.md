# HackRAISE

A collaborative AI workspace: a Next.js frontend chats with a FastAPI backend that runs a Mistral-powered agent (via LangChain) backed by MongoDB.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package/dependency manager)
- Node.js 20+ and npm
- A Mistral API key (https://console.mistral.ai/)

## 1. Backend

Dependencies are managed by `uv` from the repo root (see `pyproject.toml` / `uv.lock`).

```bash
# from the repo root, install/sync Python dependencies
uv sync
```

Create `backend/.env` with your Mistral key:

```bash
cp backend/.env.example backend/.env
# then edit backend/.env and set MISTRAL_API_KEY=your-key-here
```

Run the API server:

```bash
cd backend
uv run --project .. uvicorn main:app --reload
```

The backend starts on `http://localhost:8000` and exposes:
- `POST /sessions/init` — create a new collaborative session
- `GET /sessions/check/{session_id}` — check a session's status
- `WS /ws/{session_id}/{client_id}` — realtime agent chat / file collaboration

## 2. Frontend

```bash
cd frontend
npm install
```

Configure `frontend/.env.local` (already present in the repo for local dev):
- `NEXT_PUBLIC_BACKEND_URL` — URL of the backend, defaults to `http://localhost:8000`
- `NEXT_PUBLIC_FIREBASE_*` — Firebase project config (used for auth + storing conversations)

Run the dev server:

```bash
npm run dev
```

Open `http://localhost:3000`, sign in with Google, and start a new chat.

## How it fits together

- Starting a new chat calls `POST /sessions/init` on the backend, then opens a WebSocket to `/ws/{session_id}/{client_id}`.
- The backend's `SystemAgent` interviews you to scope the project, then hands off to a `PersonalAgent` once the session is `ACTIVE`.
- Conversations and messages are persisted in Firestore; the backend session id is stored alongside each conversation so reopening it reconnects to the same session.
