"""Live device recording engine for capturing incoming IR events as action steps."""

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mqtt_ir_monitor.db import save_script

logger = logging.getLogger(__name__)


class RecordingActiveError(Exception):
    """Raised when starting a recording while another session is already active."""


class RecordingNotActiveError(Exception):
    """Raised when stopping a recording while no session is active."""


class RecordingEngine:
    """Manages a single live IR recording session from an assigned input device."""

    def __init__(
        self,
        on_broadcast: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.on_broadcast = on_broadcast
        self._lock = threading.Lock()
        self._active = False
        self._device_id: str | None = None
        self._include_tx = False
        self._hide_noise = True
        self._started_at: str | None = None
        self._started_at_ms: int | None = None
        self._last_step_ms: int | None = None
        self._steps: list[dict[str, Any]] = []

    def _broadcast(self, msg: dict[str, Any]) -> None:
        if self.on_broadcast:
            try:
                res = self.on_broadcast(msg)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        res.close()
            except Exception as e:  # noqa: BLE001
                logger.error("Error broadcasting recording status: %s", e)

    def _status_unlocked(self) -> dict[str, Any]:
        elapsed_ms = 0
        if self._active and self._started_at_ms is not None:
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            elapsed_ms = max(0, now_ms - self._started_at_ms)
        return {
            "active": self._active,
            "device_id": self._device_id,
            "include_tx": self._include_tx,
            "hide_noise": self._hide_noise,
            "started_at": self._started_at,
            "elapsed_ms": elapsed_ms,
            "step_count": len(self._steps),
            "steps": [dict(step) for step in self._steps],
        }

    def _reset_unlocked(self) -> None:
        self._active = False
        self._device_id = None
        self._include_tx = False
        self._hide_noise = True
        self._started_at = None
        self._started_at_ms = None
        self._last_step_ms = None
        self._steps = []

    def get_status(self) -> dict[str, Any]:
        """Return current recording session state."""
        with self._lock:
            return self._status_unlocked()

    def start(
        self,
        device_id: str,
        include_tx: bool = False,
        hide_noise: bool = True,
    ) -> dict[str, Any]:
        """Start a new live recording session for the given input device."""
        clean_device_id = device_id.strip()
        if not clean_device_id:
            raise ValueError("device_id cannot be empty.")

        with self._lock:
            if self._active:
                raise RecordingActiveError("A recording session is already active.")
            now = datetime.now(UTC)
            self._active = True
            self._device_id = clean_device_id
            self._include_tx = include_tx
            self._hide_noise = hide_noise
            self._started_at = now.isoformat()
            self._started_at_ms = int(now.timestamp() * 1000)
            self._last_step_ms = None
            self._steps = []
            status = self._status_unlocked()

        self._broadcast({"type": "recording_status", "data": status})
        return status

    def handle_notification(self, notif: dict[str, Any]) -> bool:
        """Capture a qualifying incoming notification as a sendircode step."""
        with self._lock:
            if not self._active or notif.get("device_id") != self._device_id:
                return False

            event_type = notif.get("event_type")
            allowed_events = ("ir_rx", "ir_tx") if self._include_tx else ("ir_rx",)
            if event_type not in allowed_events:
                return False

            prot = notif.get("prot")
            addr = notif.get("addr")
            cmd = notif.get("cmd")
            flags = notif.get("flags")
            if prot is None or addr is None or cmd is None:
                return False

            if self._hide_noise and int(prot) == 2:
                return False

            now_ms = int(notif["timestamp_ms"])
            delay_ms = 0 if self._last_step_ms is None else max(0, now_ms - self._last_step_ms)
            self._last_step_ms = now_ms

            self._steps.append(
                {
                    "type": "sendircode",
                    "prot": int(prot),
                    "addr": int(addr),
                    "cmd": int(cmd),
                    "flags": int(flags) if flags is not None else 0,
                    "delay_ms": delay_ms,
                }
            )
            status = self._status_unlocked()

        self._broadcast({"type": "recording_status", "data": status})
        return True

    def stop(
        self,
        db_path: str,
        name: str | None = None,
        overwrite: bool = False,
        save: bool = True,
    ) -> dict[str, Any]:
        """Stop the active recording session, optionally saving it as an Action Script."""
        with self._lock:
            if not self._active:
                raise RecordingNotActiveError("No active recording session.")
            captured_steps = [dict(step) for step in self._steps]

            if save:
                clean_name = (name or "").strip()
                if not clean_name:
                    raise ValueError("Script name is required when save is true.")
                if not captured_steps:
                    raise ValueError("Cannot save an empty recording.")
                # Raises ScriptConflictError if name exists and overwrite is False;
                # recording stays active with steps intact so the caller can retry.
                script, created = save_script(
                    db_path=db_path,
                    name=clean_name,
                    steps=captured_steps,
                    overwrite=overwrite,
                )
                self._reset_unlocked()
                status = self._status_unlocked()
                result: dict[str, Any] = {
                    "status": "saved",
                    "saved": True,
                    "created": created,
                    "script": script,
                    "step_count": len(captured_steps),
                    "steps": captured_steps,
                }
            else:
                self._reset_unlocked()
                status = self._status_unlocked()
                result = {
                    "status": "stopped",
                    "saved": False,
                    "step_count": len(captured_steps),
                    "steps": captured_steps,
                }

        self._broadcast({"type": "recording_status", "data": status})
        return result

    def cancel(self) -> dict[str, Any]:
        """Stop recording and discard all captured steps."""
        with self._lock:
            self._reset_unlocked()
            status = self._status_unlocked()
        self._broadcast({"type": "recording_status", "data": status})
        return status
