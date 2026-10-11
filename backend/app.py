"""FastAPI application — orchestrator control tower."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.api.routes import router
from backend.security import LocalConsoleSecurityMiddleware
from backend.storage.search_index import get_search_index, rebuild_from_disk

logger = logging.getLogger("orchestrator.app")

app = FastAPI(
    title="Orchestrator Control Tower",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LocalConsoleSecurityMiddleware)

app.include_router(router)

# Serve static UI files
ui_dir = Path(__file__).resolve().parent.parent / "ui"
app.mount("/static", StaticFiles(directory=str(ui_dir)), name="static")


@app.on_event("startup")
async def _bootstrap_search_index() -> None:
    """Rebuild FTS5 index from JSON if the SQLite store is empty (first run, or wiped)."""
    try:
        if get_search_index().count() == 0:
            counts = rebuild_from_disk()
            logger.info("search index seeded from disk: %s", counts)
    except Exception as exc:
        logger.warning("search index bootstrap skipped: %s", exc)


@app.on_event("startup")
async def _start_scheduler() -> None:
    try:
        from backend.services.scheduler import get_scheduler
        get_scheduler().start()
    except Exception as exc:
        logger.warning("scheduler start skipped: %s", exc)


@app.on_event("shutdown")
async def _stop_scheduler() -> None:
    try:
        from backend.services.scheduler import get_scheduler
        get_scheduler().shutdown()
    except Exception as exc:
        logger.warning("scheduler shutdown error: %s", exc)


@app.get("/")
async def root():
    return FileResponse(str(ui_dir / "index.html"))
