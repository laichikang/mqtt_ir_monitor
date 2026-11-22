"""Tests for MQTT message parsing, device status handling, and pacing."""

import json
import time
from unittest.mock import MagicMock

from mqtt_ir_monitor.db import get_devices, get_notifications
from mqtt_ir_monitor.mqtt_service import MqttService


def test_parse_status_message(test_settings):
    broadcasts = []
    service = MqttService(test_settings, on_broadcast=lambda msg: broadcasts.append(msg))

    mock_msg = MagicMock()
    mock_msg.topic = "/irdevs/dev1/status"
    mock_msg.payload = json.dumps({"online": True}).encode("utf-8")

    service._on_message(None, None, mock_msg)

    devices = get_devices(test_settings.db_path)
    assert len(devices) == 1
    assert devices[0]["device_id"] == "dev1"
    assert devices[0]["online"] == 1
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "device_update"

    # LWT offline message
    mock_msg.payload = json.dumps({"online": False}).encode("utf-8")
    service._on_message(None, None, mock_msg)
    devices = get_devices(test_settings.db_path)
    assert devices[0]["online"] == 0


def test_parse_ir_notification_message(test_settings):
    broadcasts = []
    service = MqttService(test_settings, on_broadcast=lambda msg: broadcasts.append(msg))

    mock_msg = MagicMock()
    mock_msg.topic = "/irdevs/dev1/notification"
    payload = {"event": "ir_rx", "prot": 8, "addr": 0, "cmd": 18, "flags": 0}
    mock_msg.payload = json.dumps(payload).encode("utf-8")

    service._on_message(None, None, mock_msg)

    rows, total = get_notifications(test_settings.db_path)
    assert total == 1
    assert rows[0]["device_id"] == "dev1"
    assert rows[0]["event_type"] == "ir_rx"
    assert rows[0]["cmd"] == 18
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "notification"


def test_parse_command_reply_message(test_settings):
    service = MqttService(test_settings)

    mock_msg = MagicMock()
    mock_msg.topic = "/irdevs/dev1/notification"
    payload = {"return_value": 1, "id": "dev1"}
    mock_msg.payload = json.dumps(payload).encode("utf-8")

    service._on_message(None, None, mock_msg)

    rows, total = get_notifications(test_settings.db_path, hide_noise=False)
    assert total == 1
    assert rows[0]["event_type"] == "reply"
    assert rows[0]["prot"] is None


def test_publish_pacing(test_settings):
    service = MqttService(test_settings)
    service.connected = True
    service._client = MagicMock()
    service._client.publish.return_value.rc = 0

    t0 = time.time()
    service.publish_event("dev1", 8, 0, 1, 0)
    service.publish_event("dev1", 8, 0, 2, 0)
    t1 = time.time()

    # Second publish to same device must have taken at least 120 ms (0.12s)
    elapsed = t1 - t0
    assert elapsed >= 0.115  # allowing slight timer precision margin

    # Verify publish payload format
    call_args = service._client.publish.call_args_list
    assert len(call_args) == 2
    topic, payload = call_args[0][0][0], call_args[0][0][1]
    assert topic == "/irdevs/dev1/events"
    assert payload == "sendircode?prot=8&addr=0&cmd=1&flags=0"
