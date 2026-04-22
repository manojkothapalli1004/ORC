"""FastAPI application — orchestrator control tower."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.api.routes import router
from backend.security import LocalConsoleSecurityMiddleware

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


@app.get("/")
async def root():
    return FileResponse(str(ui_dir / "index.html"))
