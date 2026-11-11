"""Local HTTP API and WebSocket feed for the Jarvis desktop UI."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import __version__, actions, config
from .listener import Listener, list_devices
from .paths import data_dir, frontend_dir
from .state import Hub

log = logging.getLogger("jarvis.app")

hub = Hub()
_listener: Listener | None = None
_listener_lock = threading.Lock()


def _listener_or_create() -> Listener:
    global _listener
    with _listener_lock:
        if _listener is None:
            _listener = Listener(
                hub,
                lambda settings: actions.run_sequence(
                    settings, hub, source="double clap"
                ),
            )
        return _listener


def _status() -> dict:
    listener = _listener_or_create()
    return {
        "version": __version__,
        "platform": sys.platform,
        "settings": config.public_settings(config.load_settings()),
        "listener": listener.status(),
        "paths": {
            "data": str(data_dir()),
            "settings": str(config.settings_path()),
        },
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.bind_loop(asyncio.get_running_loop())
    settings = config.load_settings()
    if settings.auto_start_listener:
        ok, error = _listener_or_create().start()
        if not ok:
            log.warning("Listener did not start: %s", error)
    yield
    _listener_or_create().stop()


app = FastAPI(title="Jarvis Desktop", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def _guard(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            host = (urlparse(origin).hostname or "").lower()
            if host not in {"127.0.0.1", "localhost", "::1"}:
                return JSONResponse(
                    {"detail": "Cross-origin request rejected"}, status_code=403
                )
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class ApiKeyBody(BaseModel):
    api_key: str = ""


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status() -> dict:
    return _status()


@app.get("/api/devices")
def devices() -> dict:
    items, error = list_devices()
    return {"devices": items, "error": error}


@app.put("/api/settings")
async def put_settings(request: Request) -> dict:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    try:
        settings = config.Settings.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    previous = config.load_settings()
    config.save_settings(settings)

    listener = _listener_or_create()
    restarted = False
    if settings.audio != previous.audio and listener.running:
        listener.stop()
        restarted, error = listener.start()
        if not restarted:
            log.warning("Listener restart failed: %s", error)

    return {"ok": True, "restarted": restarted, "status": _status()}


@app.post("/api/listener/start")
def start_listener() -> dict:
    listener = _listener_or_create()
    ok, error = listener.start()
    return {"ok": ok, "error": error, "listener": listener.status()}


@app.post("/api/listener/stop")
def stop_listener() -> dict:
    listener = _listener_or_create()
    listener.stop()
    return {"ok": True, "listener": listener.status()}


@app.post("/api/trigger")
def trigger() -> dict:
    settings = config.load_settings()
    threading.Thread(
        target=actions.run_sequence,
        args=(settings, hub),
        kwargs={"source": "manual"},
        name="jarvis-manual-sequence",
        daemon=True,
    ).start()
    return {"ok": True}


@app.post("/api/voice/preview")
def preview_voice() -> dict:
    settings = config.load_settings()
    threading.Thread(
        target=actions.speak,
        args=(settings, hub),
        name="jarvis-voice-preview",
        daemon=True,
    ).start()
    return {"ok": True}


@app.put("/api/voice/key")
def set_api_key(body: ApiKeyBody) -> dict:
    config.set_api_key(body.api_key)
    return {"ok": True, "api_key_set": bool(config.get_api_key())}


@app.delete("/api/voice/key")
def clear_api_key() -> dict:
    config.set_api_key(None)
    return {"ok": True, "api_key_set": bool(config.get_api_key())}


@app.get("/api/events")
def events(limit: int = 100) -> dict:
    return {"events": hub.history(max(1, min(limit, 400)))}


@app.websocket("/ws")
async def websocket(sock: WebSocket) -> None:
    await sock.accept()
    queue = hub.subscribe()
    try:
        await sock.send_json(
            {
                "type": "snapshot",
                "status": _status(),
                "events": hub.history(40),
            }
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                await sock.send_json({"type": "ping"})
                continue
            await sock.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(queue)


_UI_DIR = frontend_dir()
if _UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
else:  # pragma: no cover - only hit by a broken build

    @app.get("/")
    def missing_ui() -> JSONResponse:
        return JSONResponse(
            {"detail": f"UI assets not found at {_UI_DIR}"}, status_code=500
        )
