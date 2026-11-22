"""FastAPI application entry point, REST API routes, and WebSocket connection manager."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from mqtt_ir_monitor.config import settings
from mqtt_ir_monitor.db import (
    ScriptConflictError,
    clear_notifications,
    delete_script,
    get_devices,
    get_notifications,
    get_script_by_name,
    init_db,
    list_scripts,
    save_script,
)
from mqtt_ir_monitor.mqtt_service import MqttService
from mqtt_ir_monitor.protocols import get_protocol_label
from mqtt_ir_monitor.replay import ReplayEngine, compile_notifications_to_steps

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mqtt_ir_monitor.app")

# Connected WebSocket clients
connected_clients: set[WebSocket] = set()


async def broadcast_ws(message: dict[str, Any]) -> None:
    """Send JSON payload to all active WebSocket clients."""
    if not connected_clients:
        return
    text = json.dumps(message)
    dead_clients = set()
    for ws in list(connected_clients):
        try:
            await ws.send_text(text)
        except Exception:  # noqa: BLE001
            dead_clients.add(ws)
    connected_clients.difference_update(dead_clients)


mqtt_service: MqttService
replay_engine: ReplayEngine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle manager."""
    global mqtt_service, replay_engine
    init_db(settings.db_path)

    main_loop = asyncio.get_running_loop()

    def dispatch_ws(message: dict[str, Any]) -> None:
        with suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(broadcast_ws(message), main_loop)

    # Initialize MQTT and Replay engine
    mqtt_service = MqttService(settings=settings, on_broadcast=dispatch_ws)
    replay_engine = ReplayEngine(
        mqtt_service=mqtt_service,
        db_path=settings.db_path,
        on_broadcast=dispatch_ws,
    )
    mqtt_service.start()
    logger.info("Application started. MQTT service initiated.")
    try:
        yield
    finally:
        replay_engine.cancel()
        mqtt_service.stop()
        logger.info("Application shutdown complete.")


app = FastAPI(title="MQTT IR Monitor", lifespan=lifespan)

# Mount static folder
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Pydantic Request Models
class ReplaySingleRequest(BaseModel):
    notification_id: int
    target_device_id: str


class ReplaySequenceRequest(BaseModel):
    notification_ids: list[int]
    target_device_id: str
    speed: float = Field(default=1.0, ge=0.1, le=5.0)
    max_delay_ms: int = Field(default=5000, ge=120)


class MqttSettingsRequest(BaseModel):
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None


class ActionStepModel(BaseModel):
    type: Literal["sendircode", "digital", "clearkeydetail", "replaylast", "delay"]
    delay_ms: int = Field(default=0, ge=0)
    prot: int | None = None
    addr: int | None = None
    cmd: int | None = None
    flags: int | None = None
    pin: int | None = None
    value: Literal[0, 1] | None = None

    @model_validator(mode="after")
    def validate_step_fields(self) -> "ActionStepModel":
        if self.type == "sendircode":
            if self.prot is None or self.addr is None or self.cmd is None:
                raise ValueError("sendircode step requires prot, addr, and cmd.")
            if self.flags is None:
                self.flags = 0
        elif self.type == "digital" and (self.pin is None or self.value is None):
            raise ValueError("digital step requires pin and value (0 or 1).")
        return self

    def to_step_dict(self) -> dict[str, Any]:
        if self.type == "sendircode":
            return {
                "type": "sendircode",
                "prot": self.prot,
                "addr": self.addr,
                "cmd": self.cmd,
                "flags": self.flags if self.flags is not None else 0,
                "delay_ms": self.delay_ms,
            }
        if self.type == "digital":
            return {
                "type": "digital",
                "pin": self.pin,
                "value": self.value,
                "delay_ms": self.delay_ms,
            }
        return {"type": self.type, "delay_ms": self.delay_ms}


class ScriptSaveRequest(BaseModel):
    name: str = Field(min_length=1)
    steps: list[ActionStepModel]
    overwrite: bool = False


class ScriptFromNotificationsRequest(BaseModel):
    name: str = Field(min_length=1)
    notification_ids: list[int]
    max_delay_ms: int | None = Field(default=None, ge=0)
    overwrite: bool = False


class ScriptRunRequest(BaseModel):
    target_device_ids: list[str]
    speed: float = Field(default=1.0, ge=0.1, le=5.0)


@app.get("/")
async def root() -> FileResponse:
    """Serve the dashboard index.html."""
    index_path = os.path.join(static_dir, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="Dashboard UI not found.")
    return FileResponse(index_path)


@app.get("/api/devices")
async def api_get_devices() -> dict[str, Any]:
    """Return registered devices, counts, and broker status."""
    devices = get_devices(settings.db_path)
    total_count = len(devices)
    online_count = sum(1 for d in devices if d.get("online") == 1)
    status = mqtt_service.get_status()
    return {
        "devices": devices,
        "total_count": total_count,
        "online_count": online_count,
        "broker_connected": status["connected"],
        "broker_host": status["host"],
        "broker_port": status["port"],
    }


@app.get("/api/notifications")
async def api_get_notifications(
    device_id: str | None = Query(None),
    event_type: str | None = Query(None),
    hide_noise: bool = Query(True),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Query stored notifications with filtering and pagination."""
    rows, total = get_notifications(
        db_path=settings.db_path,
        device_id=device_id,
        event_type=event_type,
        hide_noise=hide_noise,
        limit=limit,
        offset=offset,
    )
    # Augment items with protocol human label
    for r in rows:
        r["prot_label"] = get_protocol_label(r.get("prot"))
    return {"notifications": rows, "total": total, "limit": limit, "offset": offset}


@app.delete("/api/notifications")
async def api_clear_notifications(device_id: str | None = Query(None)) -> dict[str, Any]:
    """Clear notification history."""
    deleted = clear_notifications(settings.db_path, device_id=device_id)
    # Broadcast notification cleared update
    await broadcast_ws({"type": "history_cleared", "device_id": device_id})
    return {"status": "ok", "deleted": deleted}


@app.post("/api/replay/single")
async def api_replay_single(req: ReplaySingleRequest) -> dict[str, Any]:
    """Replay single IR notification."""
    try:
        success = replay_engine.replay_single(req.notification_id, req.target_device_id)
        return {
            "status": "ok" if success else "failed",
            "published": success,
            "notification_id": req.notification_id,
            "target_device_id": req.target_device_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/replay/sequence")
async def api_replay_sequence(req: ReplaySequenceRequest) -> dict[str, Any]:
    """Start sequence replay across selected notification IDs."""
    if not req.notification_ids:
        raise HTTPException(status_code=400, detail="notification_ids cannot be empty.")
    replay_engine.start_sequence(
        notification_ids=req.notification_ids,
        target_device_id=req.target_device_id,
        speed=req.speed,
        max_delay_ms=req.max_delay_ms,
    )
    return {
        "status": "started",
        "total_selected": len(req.notification_ids),
        "target_device_id": req.target_device_id,
        "speed": req.speed,
    }


@app.post("/api/replay/cancel")
async def api_replay_cancel() -> dict[str, Any]:
    """Cancel active sequence replay or script execution."""
    replay_engine.cancel()
    return {"status": "cancelled"}


@app.get("/api/scripts")
async def api_list_scripts() -> dict[str, Any]:
    """List all saved action scripts."""
    scripts = list_scripts(settings.db_path)
    return {"scripts": scripts}


@app.get("/api/scripts/{name}")
async def api_get_script(name: str) -> dict[str, Any]:
    """Fetch a single saved action script by name."""
    script = get_script_by_name(settings.db_path, name)
    if not script:
        raise HTTPException(status_code=404, detail=f"Script '{name}' not found.")
    return script


@app.post("/api/scripts", response_model=None)
async def api_save_script(req: ScriptSaveRequest, response: Response) -> dict[str, Any] | Response:
    """Create or overwrite a named action script."""
    clean_name = req.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Script name cannot be empty.")
    steps = [s.to_step_dict() for s in req.steps]
    try:
        script, created = save_script(
            db_path=settings.db_path,
            name=clean_name,
            steps=steps,
            overwrite=req.overwrite,
        )
    except ScriptConflictError:
        return JSONResponse(
            status_code=409,
            content={"detail": "Script name already exists", "conflict": True},
        )
    response.status_code = 201 if created else 200
    return script


@app.post("/api/scripts/from-notifications", response_model=None)
async def api_save_script_from_notifications(
    req: ScriptFromNotificationsRequest, response: Response
) -> dict[str, Any] | Response:
    """Compile selected IR notifications into a named action script."""
    clean_name = req.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Script name cannot be empty.")
    if not req.notification_ids:
        raise HTTPException(status_code=400, detail="notification_ids cannot be empty.")
    steps = compile_notifications_to_steps(
        db_path=settings.db_path,
        notification_ids=req.notification_ids,
        max_delay_ms=req.max_delay_ms,
    )
    if not steps:
        raise HTTPException(
            status_code=400, detail="No valid IR notifications found in selection."
        )
    try:
        script, created = save_script(
            db_path=settings.db_path,
            name=clean_name,
            steps=steps,
            overwrite=req.overwrite,
        )
    except ScriptConflictError:
        return JSONResponse(
            status_code=409,
            content={"detail": "Script name already exists", "conflict": True},
        )
    response.status_code = 201 if created else 200
    return script


@app.delete("/api/scripts/{name}")
async def api_delete_script(name: str) -> dict[str, Any]:
    """Delete a saved action script by name."""
    deleted = delete_script(settings.db_path, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Script '{name}' not found.")
    return {"status": "ok", "deleted": True, "name": name}


@app.post("/api/scripts/{name}/run")
async def api_run_script(name: str, req: ScriptRunRequest) -> dict[str, Any]:
    """Execute a saved action script concurrently across target devices."""
    script = get_script_by_name(settings.db_path, name)
    if not script:
        raise HTTPException(status_code=404, detail=f"Script '{name}' not found.")
    targets = [t.strip() for t in req.target_device_ids if t.strip()]
    if not targets:
        raise HTTPException(status_code=400, detail="target_device_ids cannot be empty.")
    replay_engine.start_script(
        script_name=script["name"],
        steps=script["steps"],
        target_device_ids=targets,
        speed=req.speed,
    )
    return {
        "status": "started",
        "script_name": script["name"],
        "step_count": script["step_count"],
        "target_device_ids": targets,
        "speed": req.speed,
    }


@app.get("/api/settings/mqtt")
async def api_get_mqtt_settings() -> dict[str, Any]:
    """Get current MQTT settings and broker connection state."""
    return mqtt_service.get_status()


@app.post("/api/settings/mqtt")
async def api_post_mqtt_settings(req: MqttSettingsRequest) -> dict[str, Any]:
    """Update broker parameters and reconnect live."""
    mqtt_service.update_config(
        host=req.host,
        port=req.port,
        username=req.username,
        password=req.password,
    )
    return {
        "status": "updated",
        "broker": mqtt_service.get_status(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Real-time updates websocket channel."""
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        # Send initial status
        await websocket.send_text(
            json.dumps({"type": "broker_status", "data": mqtt_service.get_status()})
        )
        while True:
            # Keep socket alive
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        connected_clients.discard(websocket)
