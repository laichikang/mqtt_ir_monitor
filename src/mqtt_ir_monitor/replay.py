"""Replay engine for single and timed sequence IR events."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from mqtt_ir_monitor.db import get_notification_by_id, get_notifications_by_ids
from mqtt_ir_monitor.mqtt_service import MqttService

logger = logging.getLogger(__name__)


def compile_notifications_to_steps(
    db_path: str, notification_ids: list[int], max_delay_ms: int | None = None
) -> list[dict[str, Any]]:
    """Convert selected IR notifications into ordered sendircode action steps."""
    items = get_notifications_by_ids(db_path, notification_ids)
    valid_items = [
        it
        for it in items
        if it.get("prot") is not None
        and it.get("addr") is not None
        and it.get("cmd") is not None
    ]
    steps: list[dict[str, Any]] = []
    prev_ms: int | None = None
    for item in valid_items:
        curr_ms = int(item["timestamp_ms"])
        if prev_ms is None:
            delay_ms = 0
        else:
            raw_delta = max(0, curr_ms - prev_ms)
            delay_ms = min(max_delay_ms, raw_delta) if max_delay_ms is not None else raw_delta
        prev_ms = curr_ms
        steps.append(
            {
                "type": "sendircode",
                "prot": int(item["prot"]),
                "addr": int(item["addr"]),
                "cmd": int(item["cmd"]),
                "flags": int(item["flags"]) if item.get("flags") is not None else 0,
                "delay_ms": delay_ms,
            }
        )
    return steps


def format_step_payload(step: dict[str, Any]) -> str | None:
    """Format an action step into an MQTT command payload, or None for delay steps."""
    step_type = step.get("type")
    if step_type == "sendircode":
        prot = int(step["prot"])
        addr = int(step["addr"])
        cmd = int(step["cmd"])
        flags = int(step.get("flags", 0))
        return f"sendircode?prot={prot}&addr={addr}&cmd={cmd}&flags={flags}"
    if step_type == "digital":
        pin = int(step["pin"])
        value = int(step["value"])
        return f"digital/{pin}/{value}"
    if step_type == "clearkeydetail":
        return "clearkeydetail"
    if step_type == "replaylast":
        return "replaylast"
    if step_type == "delay":
        return None
    raise ValueError(f"Unsupported action step type: {step_type}")


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

    async def _run_script_for_device(
        self,
        script_name: str,
        steps: list[dict[str, Any]],
        device_id: str,
        speed: float,
        progress: dict[str, int],
        total_ops: int,
    ) -> None:
        """Execute all script steps sequentially for a single target device."""
        for index, step in enumerate(steps):
            if self._cancel_requested:
                return

            delay_ms = max(0, int(step.get("delay_ms", 0)))
            scaled_ms = delay_ms / speed if speed > 0 else float(delay_ms)
            if scaled_ms > 0:
                await asyncio.sleep(scaled_ms / 1000.0)

            if self._cancel_requested:
                return

            payload = format_step_payload(step)
            if payload is not None:
                await asyncio.to_thread(
                    self.mqtt_service.publish_command,
                    device_id,
                    payload,
                )

            progress["completed"] += 1
            status_payload = {
                "status": "running",
                "script_name": script_name,
                "current": progress["completed"],
                "total": total_ops,
                "target_device_id": device_id,
                "step_index": index + 1,
                "message": (
                    f"Script '{script_name}': {progress['completed']}/{total_ops} "
                    f"({device_id} step {index + 1}/{len(steps)})"
                ),
            }
            self._broadcast({"type": "script_status", "data": status_payload})
            self._broadcast({"type": "replay_status", "data": status_payload})

    async def _run_script(
        self,
        script_name: str,
        steps: list[dict[str, Any]],
        target_device_ids: list[str],
        speed: float = 1.0,
    ) -> None:
        """Execute a saved action script concurrently across all target devices."""
        total_ops = len(steps) * len(target_device_ids)
        if total_ops == 0:
            done_payload = {
                "status": "completed",
                "script_name": script_name,
                "current": 0,
                "total": 0,
                "target_device_ids": target_device_ids,
                "message": f"Script '{script_name}' has no steps or targets.",
            }
            self._broadcast({"type": "script_status", "data": done_payload})
            self._broadcast({"type": "replay_status", "data": done_payload})
            return

        start_payload = {
            "status": "running",
            "script_name": script_name,
            "current": 0,
            "total": total_ops,
            "target_device_ids": target_device_ids,
            "message": (
                f"Starting script '{script_name}' ({len(steps)} steps) "
                f"on {len(target_device_ids)} device(s)..."
            ),
        }
        self._broadcast({"type": "script_status", "data": start_payload})
        self._broadcast({"type": "replay_status", "data": start_payload})

        progress = {"completed": 0}
        try:
            await asyncio.gather(
                *(
                    self._run_script_for_device(
                        script_name=script_name,
                        steps=steps,
                        device_id=dev_id,
                        speed=speed,
                        progress=progress,
                        total_ops=total_ops,
                    )
                    for dev_id in target_device_ids
                )
            )
        except asyncio.CancelledError:
            cancel_payload = {
                "status": "cancelled",
                "script_name": script_name,
                "current": progress["completed"],
                "total": total_ops,
                "target_device_ids": target_device_ids,
                "message": (
                    f"Script '{script_name}' cancelled at "
                    f"{progress['completed']}/{total_ops}."
                ),
            }
            self._broadcast({"type": "script_status", "data": cancel_payload})
            self._broadcast({"type": "replay_status", "data": cancel_payload})
            return

        if self._cancel_requested:
            cancel_payload = {
                "status": "cancelled",
                "script_name": script_name,
                "current": progress["completed"],
                "total": total_ops,
                "target_device_ids": target_device_ids,
                "message": (
                    f"Script '{script_name}' cancelled at "
                    f"{progress['completed']}/{total_ops}."
                ),
            }
            self._broadcast({"type": "script_status", "data": cancel_payload})
            self._broadcast({"type": "replay_status", "data": cancel_payload})
            return

        done_payload = {
            "status": "completed",
            "script_name": script_name,
            "current": total_ops,
            "total": total_ops,
            "target_device_ids": target_device_ids,
            "message": f"Finished script '{script_name}' on {len(target_device_ids)} device(s).",
        }
        self._broadcast({"type": "script_status", "data": done_payload})
        self._broadcast({"type": "replay_status", "data": done_payload})

    def start_script(
        self,
        script_name: str,
        steps: list[dict[str, Any]],
        target_device_ids: list[str],
        speed: float = 1.0,
    ) -> None:
        """Cancel active task if running and start concurrent multi-device script execution."""
        self.cancel()
        self._cancel_requested = False
        loop = asyncio.get_event_loop()
        self._active_task = loop.create_task(
            self._run_script(script_name, steps, target_device_ids, speed)
        )

    def cancel(self) -> None:
        """Cancel current active sequence replay or script execution."""
        self._cancel_requested = True
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
            cancel_data = {
                "status": "cancelled",
                "message": "Replay cancelled by user.",
            }
            self._broadcast({"type": "script_status", "data": cancel_data})
            self._broadcast({"type": "replay_status", "data": cancel_data})
