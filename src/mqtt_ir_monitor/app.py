"""FastAPI application entry point, REST API routes, and WebSocket connection manager."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mqtt_ir_monitor.config import settings
from mqtt_ir_monitor.db import (
    clear_notifications,
    get_devices,
    get_notifications,
    init_db,
)
from mqtt_ir_monitor.mqtt_service import MqttService
from mqtt_ir_monitor.protocols import get_protocol_label
from mqtt_ir_monitor.replay import ReplayEngine

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
    """Cancel active sequence replay."""
    replay_engine.cancel()
    return {"status": "cancelled"}


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
