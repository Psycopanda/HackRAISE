"""VibeCode backend — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import close_mongo_connection, connect_to_mongo
from app.logging_config import configure_logging
from app.routers import sessions, ws

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await connect_to_mongo()
    except Exception as exc:  # keep the API (and static frontend) up even offline
        logging.getLogger("vibecode").warning(
            "MongoDB indisponible au démarrage (%s). L'API démarre tout de même : "
            "le frontend est servi, mais les endpoints nécessitant la base "
            "échoueront tant que la connexion n'est pas rétablie.",
            exc,
        )
    yield
    await close_mongo_connection()


settings = get_settings()
app = FastAPI(title="VibeCode Backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "vibecode-backend",
        "mistral_configured": bool(settings.mistral_api_key),
    }


# Serve the vanilla HTML/CSS/JS frontend from the same origin so the browser
# WebSocket connections work without any extra configuration. Mounted LAST so
# it never shadows the /api, /ws and /docs routes registered above.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount(
        "/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend"
    )
