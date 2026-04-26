"""FastAPI dashboard server for Jackbot_V1.

Run:
    uvicorn jackbot.dashboard.app:app --host 0.0.0.0 --port 8080
Or via docker-compose dashboard service.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from jackbot.dashboard.fetcher import DataFetcher

logger = structlog.get_logger(__name__)

SYMBOLS = [s.strip() for s in os.getenv("DASHBOARD_SYMBOLS", "BTCUSDT,ETHUSDT").split(",")]
REFRESH_INTERVAL = float(os.getenv("DASHBOARD_INTERVAL", "3"))
STATIC_DIR = Path(__file__).parent / "static"


class _ConnectionManager:
    def __init__(self) -> None:
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)
        logger.info("ws_client_connected", total=len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._clients:
            self._clients.remove(ws)
        logger.info("ws_client_disconnected", total=len(self._clients))

    async def broadcast(self, payload: str) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = _ConnectionManager()
_fetcher: DataFetcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _fetcher
    _fetcher = DataFetcher(SYMBOLS)
    logger.info("dashboard_started", symbols=SYMBOLS, interval=REFRESH_INTERVAL)

    async def _broadcast_loop() -> None:
        while True:
            try:
                if _manager._clients:
                    data = _fetcher.fetch()
                    await _manager.broadcast(json.dumps(data, ensure_ascii=False))
            except Exception as e:
                logger.warning("broadcast_error", error=str(e))
            await asyncio.sleep(REFRESH_INTERVAL)

    task = asyncio.create_task(_broadcast_loop())
    yield
    task.cancel()
    if _fetcher:
        _fetcher.close()


app = FastAPI(title="Jackbot V1 Dashboard", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/api/snapshot")
async def snapshot() -> dict:
    if _fetcher is None:
        return {"error": "fetcher not ready"}
    return _fetcher.fetch()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await _manager.connect(websocket)
    try:
        if _fetcher:
            data = _fetcher.fetch()
            await websocket.send_text(json.dumps(data, ensure_ascii=False))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _manager.disconnect(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
