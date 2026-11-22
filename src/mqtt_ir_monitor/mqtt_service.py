"""MQTT client service managing connection, subscriptions, and publication pacing."""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt

from mqtt_ir_monitor.config import Settings
from mqtt_ir_monitor.db import record_notification, upsert_device_status

logger = logging.getLogger(__name__)


class MqttService:
    """Manages MQTT broker connection, topic subscriptions, and rate-limited publishes."""

    def __init__(
        self,
        settings: Settings,
        on_broadcast: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.on_broadcast = on_broadcast
        self.connected = False
        self._client: mqtt.Client | None = None
        self._last_publish_per_device: dict[str, float] = {}
        self._publish_lock = threading.Lock()
        self._device_locks: dict[str, threading.Lock] = {}
        self._loop_thread: threading.Thread | None = None
        self._running = False

    def get_status(self) -> dict[str, Any]:
        """Return current broker connection status and configuration."""
        return {
            "connected": self.connected,
            "host": self.settings.mqtt_host,
            "port": self.settings.mqtt_port,
            "username": self.settings.mqtt_username,
            "topic_prefix": self.settings.mqtt_topic_prefix,
        }

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
            except Exception as e:
                logger.error("Error broadcasting message: %s", e)

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        rc: int,
        properties: Any = None,
    ) -> None:
        if rc == 0:
            self.connected = True
            logger.info(
                "Connected to MQTT broker at %s:%s",
                self.settings.mqtt_host,
                self.settings.mqtt_port,
            )
            prefix = self.settings.mqtt_topic_prefix.rstrip("/")
            status_topic = f"{prefix}/+/status"
            notif_topic = f"{prefix}/+/notification"
            client.subscribe(status_topic, qos=1)
            client.subscribe(notif_topic, qos=1)
            logger.info("Subscribed to %s and %s", status_topic, notif_topic)
        else:
            self.connected = False
            logger.warning("Failed to connect to MQTT broker, return code %s", rc)

        self._broadcast({"type": "broker_status", "data": self.get_status()})

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags_or_rc: Any = None,
        reason_code_or_properties: Any = None,
        properties: Any = None,
    ) -> None:
        self.connected = False
        logger.warning("Disconnected from MQTT broker.")
        self._broadcast({"type": "broker_status", "data": self.get_status()})

    def _parse_device_id_from_topic(self, topic: str, suffix: str) -> str | None:
        """Extract device_id from <prefix>/<device_id>/<suffix>."""
        prefix = self.settings.mqtt_topic_prefix.strip("/")
        parts = topic.strip("/").split("/")
        # Expected: [prefix_parts..., device_id, suffix]
        prefix_parts = prefix.split("/") if prefix else []
        if len(parts) == len(prefix_parts) + 2 and parts[-1] == suffix:
            return parts[-2]
        return None

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            payload_str = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload_str = str(msg.payload)

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        now_ms = int(now.timestamp() * 1000)

        # 1. Device status message: <prefix>/+/status
        dev_id = self._parse_device_id_from_topic(topic, "status")
        if dev_id:
            try:
                data = json.loads(payload_str)
                online_val = bool(data.get("online", False))
            except Exception:
                online_val = payload_str.strip().lower() in ("1", "true", "online")

            device_info = upsert_device_status(
                self.settings.db_path, dev_id, online_val, now_iso
            )
            self._broadcast({"type": "device_update", "data": device_info})
            return

        # 2. Notification message: <prefix>/+/notification
        dev_id = self._parse_device_id_from_topic(topic, "notification")
        if dev_id:
            event_type = "other"
            prot: int | None = None
            addr: int | None = None
            cmd: int | None = None
            flags: int | None = None

            try:
                data = json.loads(payload_str)
                if isinstance(data, dict):
                    if "event" in data and data["event"] in ("ir_rx", "ir_tx"):
                        event_type = str(data["event"])
                        prot = data.get("prot")
                        addr = data.get("addr")
                        cmd = data.get("cmd")
                        flags = data.get("flags")
                    elif "return_value" in data or "message" in data or "status" in data:
                        event_type = "reply"
            except Exception:
                event_type = "other"

            notif_id = record_notification(
                db_path=self.settings.db_path,
                device_id=dev_id,
                timestamp=now_iso,
                timestamp_ms=now_ms,
                event_type=event_type,
                prot=prot,
                addr=addr,
                cmd=cmd,
                flags=flags,
                raw_payload=payload_str,
                max_rows=self.settings.max_notifications,
            )

            notif_data = {
                "id": notif_id,
                "device_id": dev_id,
                "timestamp": now_iso,
                "timestamp_ms": now_ms,
                "event_type": event_type,
                "prot": prot,
                "addr": addr,
                "cmd": cmd,
                "flags": flags,
                "raw_payload": payload_str,
            }
            self._broadcast({"type": "notification", "data": notif_data})

    def start(self) -> None:
        """Start MQTT client and background processing loop."""
        if self._running:
            return
        self._running = True

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"mqtt_ir_monitor_{int(time.time())}",
        )
        if self.settings.mqtt_username:
            client.username_pw_set(
                self.settings.mqtt_username, self.settings.mqtt_password
            )

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=10)

        self._client = client

        try:
            client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=60)
            client.loop_start()
        except Exception as e:  # noqa: BLE001
            logger.error("Error connecting to broker: %s", e)

    def stop(self) -> None:
        """Stop MQTT client."""
        self._running = False
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.error("Error stopping MQTT client: %s", e)
        self.connected = False
        self._broadcast({"type": "broker_status", "data": self.get_status()})

    def update_config(
        self,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Update broker parameters and reconnect live."""
        self.stop()
        self.settings.mqtt_host = host
        self.settings.mqtt_port = port
        self.settings.mqtt_username = username
        self.settings.mqtt_password = password
        self.start()

    def _get_device_lock(self, device_id: str) -> threading.Lock:
        with self._publish_lock:
            lock = self._device_locks.get(device_id)
            if lock is None:
                lock = threading.Lock()
                self._device_locks[device_id] = lock
            return lock

    def publish_command(self, target_device_id: str, payload: str) -> bool:
        """Publish an arbitrary command payload to target device with ADR-007 per-device pacing."""
        prefix = self.settings.mqtt_topic_prefix.rstrip("/")
        topic = f"{prefix}/{target_device_id}/events"
        dev_lock = self._get_device_lock(target_device_id)

        with dev_lock:
            # Enforce minimum 120 ms interval per device
            last_time = self._last_publish_per_device.get(target_device_id, 0.0)
            min_gap = self.settings.min_publish_interval_ms / 1000.0
            elapsed = time.time() - last_time
            if elapsed < min_gap:
                time.sleep(min_gap - elapsed)

            if not self._client or not self.connected:
                logger.warning("Cannot publish: MQTT client is disconnected.")
                self._last_publish_per_device[target_device_id] = time.time()
                return False

            result = self._client.publish(topic, payload, qos=1)
            self._last_publish_per_device[target_device_id] = time.time()
            return result.rc == mqtt.MQTT_ERR_SUCCESS

    def publish_event(
        self, target_device_id: str, prot: int, addr: int, cmd: int, flags: int
    ) -> bool:
        """Publish sendircode command to target device with ADR-007 pacing."""
        payload = f"sendircode?prot={prot}&addr={addr}&cmd={cmd}&flags={flags}"
        return self.publish_command(target_device_id, payload)
