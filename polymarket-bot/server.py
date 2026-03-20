from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.state_manager import bot_state
from config import settings

app = FastAPI(title="Polymarket Bot API", version="1.0.0")

# CORS restrito a localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

_start_time = time.time()


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_dashboard() -> FileResponse:
    if not DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return FileResponse(str(DASHBOARD_PATH), media_type="text/html")


@app.get("/api/state")
async def get_state() -> JSONResponse:
    return JSONResponse(content=bot_state.get_state())


class StateUpdate(BaseModel):
    data: Dict[str, Any]


@app.post("/api/update")
async def update_state(body: StateUpdate) -> JSONResponse:
    bot_state.update(**body.data)
    return JSONResponse(content={"ok": True})


@app.get("/api/trades")
async def get_trades(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> JSONResponse:
    state = bot_state.get_state()
    resolved = state.get("resolved", [])
    total = len(resolved)
    start = (page - 1) * per_page
    end = start + per_page
    return JSONResponse(
        content={
            "trades": resolved[start:end],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
    )


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "ok",
            "uptime": int(time.time() - _start_time),
            "simulation_mode": settings.simulation_mode,
            "ts": int(time.time()),
        }
    )
