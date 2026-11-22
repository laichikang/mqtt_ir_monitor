"""Replay engine for single and timed sequence IR events."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from mqtt_ir_monitor.db import get_notification_by_id, get_notifications_by_ids
from mqtt_ir_monitor.mqtt_service import MqttService

logger = logging.getLogger(__name__)


class ReplayEngine:
    """Manages IR event playback with timestamp delta preservation and cancellation."""

    def __init__(
        self,
        mqtt_service: MqttService,
        db_path: str,
        on_broadcast: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.mqtt_service = mqtt_service
        self.db_path = db_path
        self.on_broadcast = on_broadcast
        self._active_task: asyncio.Task[None] | None = None
        self._cancel_requested = False

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
                logger.error("Error broadcasting replay status: %s", e)

    def replay_single(self, notification_id: int, target_device_id: str) -> bool:
        """Replay a single notification record to target device."""
        notif = get_notification_by_id(self.db_path, notification_id)
        if not notif:
            raise ValueError(f"Notification {notification_id} not found.")

        prot = notif.get("prot")
        addr = notif.get("addr")
        cmd = notif.get("cmd")
        flags = notif.get("flags")

        if prot is None or addr is None or cmd is None:
            msg = (
                f"Notification {notification_id} is not a valid IR code "
                f"(prot={prot}, addr={addr}, cmd={cmd})."
            )
            raise ValueError(msg)

        flags = flags if flags is not None else 0
        success = self.mqtt_service.publish_event(
            target_device_id=target_device_id,
            prot=prot,
            addr=addr,
            cmd=cmd,
            flags=flags,
        )
        return success

    async def _run_sequence(
        self,
        notification_ids: list[int],
        target_device_id: str,
        speed: float = 1.0,
        max_delay_ms: int = 5000,
    ) -> None:
        """Asynchronous execution loop for sequence replay."""
        items = get_notifications_by_ids(self.db_path, notification_ids)
        # Filter only items that have prot, addr, cmd
        valid_items = [
            it
            for it in items
            if it.get("prot") is not None
            and it.get("addr") is not None
            and it.get("cmd") is not None
        ]

        total = len(valid_items)
        if total == 0:
            self._broadcast(
                {
                    "type": "replay_status",
                    "data": {
                        "status": "completed",
                        "current": 0,
                        "total": 0,
                        "target_device_id": target_device_id,
                        "message": "No valid IR events found in selection.",
                    },
                }
            )
            return

        self._broadcast(
            {
                "type": "replay_status",
                "data": {
                    "status": "running",
                    "current": 0,
                    "total": total,
                    "target_device_id": target_device_id,
                    "message": f"Starting sequence of {total} events...",
                },
            }
        )

        prev_ms: int | None = None
        for index, item in enumerate(valid_items):
            if self._cancel_requested:
                self._broadcast(
                    {
                        "type": "replay_status",
                        "data": {
                            "status": "cancelled",
                            "current": index,
                            "total": total,
                            "target_device_id": target_device_id,
                            "message": f"Sequence cancelled at {index}/{total}.",
                        },
                    }
                )
                return

            # Compute timing delta from previous event
            curr_ms = item["timestamp_ms"]
            if prev_ms is not None:
                raw_delta = max(0, curr_ms - prev_ms)
                # Scale delta by speed factor
                scaled_delta = raw_delta / speed if speed > 0 else raw_delta
                # Clamp delta between [120ms, max_delay_ms]
                clamped_delay = max(120, min(max_delay_ms, int(scaled_delta)))
                await asyncio.sleep(clamped_delay / 1000.0)

            if self._cancel_requested:
                self._broadcast(
                    {
                        "type": "replay_status",
                        "data": {
                            "status": "cancelled",
                            "current": index,
                            "total": total,
                            "target_device_id": target_device_id,
                            "message": f"Sequence cancelled at {index}/{total}.",
                        },
                    }
                )
                return

            prot = item["prot"]
            addr = item["addr"]
            cmd = item["cmd"]
            flags = item["flags"] if item.get("flags") is not None else 0

            self.mqtt_service.publish_event(
                target_device_id=target_device_id,
                prot=prot,
                addr=addr,
                cmd=cmd,
                flags=flags,
            )
            prev_ms = curr_ms

            self._broadcast(
                {
                    "type": "replay_status",
                    "data": {
                        "status": "running",
                        "current": index + 1,
                        "total": total,
                        "target_device_id": target_device_id,
                        "message": f"Replayed {index + 1}/{total} (cmd=0x{cmd:02X})",
                    },
                }
            )

        self._broadcast(
            {
                "type": "replay_status",
                "data": {
                    "status": "completed",
                    "current": total,
                    "total": total,
                    "target_device_id": target_device_id,
                    "message": f"Finished sequence replay of {total} events.",
                },
            }
        )

    def start_sequence(
        self,
        notification_ids: list[int],
        target_device_id: str,
        speed: float = 1.0,
        max_delay_ms: int = 5000,
    ) -> None:
        """Cancel active sequence if running and start new sequence task."""
        self.cancel()
        self._cancel_requested = False
        loop = asyncio.get_event_loop()
        self._active_task = loop.create_task(
            self._run_sequence(notification_ids, target_device_id, speed, max_delay_ms)
        )

    def cancel(self) -> None:
        """Cancel current active sequence replay."""
        self._cancel_requested = True
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
            self._broadcast(
                {
                    "type": "replay_status",
                    "data": {
                        "status": "cancelled",
                        "message": "Replay cancelled by user.",
                    },
                }
            )
