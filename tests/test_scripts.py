"""Tests for Action Scripts CRUD, conflict handling, conversion, and concurrent execution."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from mqtt_ir_monitor.app import app
from mqtt_ir_monitor.config import settings
from mqtt_ir_monitor.db import (
    ScriptConflictError,
    delete_script,
    get_script_by_name,
    list_scripts,
    record_notification,
    save_script,
    upsert_device_status,
)
from mqtt_ir_monitor.mqtt_service import MqttService
from mqtt_ir_monitor.replay import ReplayEngine, compile_notifications_to_steps, format_step_payload


@pytest.fixture(autouse=True)
def setup_scripts_db(temp_db_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", temp_db_path)
    upsert_device_status(temp_db_path, "dev1", True)
    upsert_device_status(temp_db_path, "dev2", True)


def test_db_script_crud_and_conflict(temp_db_path):
    steps = [{"type": "sendircode", "prot": 8, "addr": 0, "cmd": 12, "flags": 0, "delay_ms": 0}]
    script, created = save_script(temp_db_path, "power_on", steps, overwrite=False)
    assert created is True
    assert script["name"] == "power_on"
    assert script["step_count"] == 1

    # Conflict without overwrite
    with pytest.raises(ScriptConflictError):
        save_script(temp_db_path, "power_on", steps, overwrite=False)

    # Overwrite succeeds
    new_steps = steps + [{"type": "delay", "delay_ms": 500}]
    updated, created2 = save_script(temp_db_path, "power_on", new_steps, overwrite=True)
    assert created2 is False
    assert updated["step_count"] == 2

    fetched = get_script_by_name(temp_db_path, "power_on")
    assert fetched is not None
    assert fetched["step_count"] == 2

    all_scripts = list_scripts(temp_db_path)
    assert len(all_scripts) == 1

    assert delete_script(temp_db_path, "power_on") is True
    assert delete_script(temp_db_path, "power_on") is False
    assert get_script_by_name(temp_db_path, "power_on") is None


def test_format_all_five_action_types():
    assert (
        format_step_payload(
            {"type": "sendircode", "prot": 8, "addr": 1, "cmd": 18, "flags": 0, "delay_ms": 0}
        )
        == "sendircode?prot=8&addr=1&cmd=18&flags=0"
    )
    assert (
        format_step_payload({"type": "digital", "pin": 5, "value": 1, "delay_ms": 0})
        == "digital/5/1"
    )
    assert (
        format_step_payload({"type": "clearkeydetail", "delay_ms": 0}) == "clearkeydetail"
    )
    assert format_step_payload({"type": "replaylast", "delay_ms": 0}) == "replaylast"
    assert format_step_payload({"type": "delay", "delay_ms": 250}) is None


def test_compile_notifications_to_steps(temp_db_path):
    id1 = record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00.000Z",
        timestamp_ms=1000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=10,
        flags=0,
        raw_payload="1",
    )
    id2 = record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00.350Z",
        timestamp_ms=1350,
        event_type="ir_tx",
        prot=8,
        addr=0,
        cmd=20,
        flags=1,
        raw_payload="2",
    )
    id3 = record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:10.350Z",
        timestamp_ms=11350,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=30,
        flags=0,
        raw_payload="3",
    )
    # Reply notification (non-IR) should be skipped
    id_reply = record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:11.000Z",
        timestamp_ms=12000,
        event_type="reply",
        prot=None,
        addr=None,
        cmd=None,
        flags=None,
        raw_payload='{"return_value":0}',
    )

    # Default: no 5000ms clamp, preserves full 10000ms delta
    steps = compile_notifications_to_steps(temp_db_path, [id3, id1, id_reply, id2])
    assert len(steps) == 3
    assert steps[0] == {
        "type": "sendircode",
        "prot": 8,
        "addr": 0,
        "cmd": 10,
        "flags": 0,
        "delay_ms": 0,
    }
    assert steps[1]["cmd"] == 20
    assert steps[1]["delay_ms"] == 350
    assert steps[2]["cmd"] == 30
    assert steps[2]["delay_ms"] == 10000

    # Optional explicit max_delay_ms still clamps when passed
    clamped = compile_notifications_to_steps(
        temp_db_path, [id3, id1, id_reply, id2], max_delay_ms=5000
    )
    assert clamped[2]["delay_ms"] == 5000


@pytest.mark.asyncio
async def test_concurrent_multi_device_script_execution_with_pacing(test_settings):
    service = MqttService(test_settings)
    service.connected = True
    service._client = MagicMock()
    service._client.publish.return_value.rc = 0

    broadcasts: list[dict] = []
    engine = ReplayEngine(
        service,
        test_settings.db_path,
        on_broadcast=lambda msg: broadcasts.append(msg),
    )

    steps = [
        {"type": "sendircode", "prot": 8, "addr": 0, "cmd": 12, "flags": 0, "delay_ms": 0},
        {"type": "digital", "pin": 5, "value": 1, "delay_ms": 0},
        {"type": "clearkeydetail", "delay_ms": 0},
        {"type": "replaylast", "delay_ms": 0},
        {"type": "delay", "delay_ms": 50},
    ]

    t0 = time.time()
    await engine._run_script("combo", steps, ["dev1", "dev2"], speed=1.0)
    elapsed = time.time() - t0

    # 4 publish steps per device * 2 devices = 8 publishes total
    assert service._client.publish.call_count == 8
    # Per-device pacing: 4 publishes on one device => at least 3 * 120ms = 360ms.
    # Because dev1 and dev2 run concurrently, total time should be < 2 * 360ms + margin.
    assert elapsed >= 0.34
    assert elapsed < 0.70

    topics_payloads = [
        (call.args[0], call.args[1]) for call in service._client.publish.call_args_list
    ]
    assert ("/irdevs/dev1/events", "sendircode?prot=8&addr=0&cmd=12&flags=0") in topics_payloads
    assert ("/irdevs/dev2/events", "digital/5/1") in topics_payloads
    assert ("/irdevs/dev1/events", "clearkeydetail") in topics_payloads
    assert ("/irdevs/dev2/events", "replaylast") in topics_payloads

    script_statuses = [
        b["data"]["status"] for b in broadcasts if b.get("type") == "script_status"
    ]
    assert "running" in script_statuses
    assert "completed" in script_statuses


@pytest.mark.asyncio
async def test_script_execution_cancellation(test_settings):
    mock_mqtt = MagicMock()
    mock_mqtt.publish_command.return_value = True

    broadcasts: list[dict] = []
    engine = ReplayEngine(
        mock_mqtt,
        test_settings.db_path,
        on_broadcast=lambda msg: broadcasts.append(msg),
    )

    steps = [
        {"type": "clearkeydetail", "delay_ms": 0},
        {"type": "delay", "delay_ms": 1000},
        {"type": "replaylast", "delay_ms": 0},
    ]

    task = asyncio.create_task(engine._run_script("slow_script", steps, ["dev1", "dev2"], 1.0))
    await asyncio.sleep(0.05)
    engine.cancel()
    await task

    # replaylast should not have been published
    published_payloads = [call.args[1] for call in mock_mqtt.publish_command.call_args_list]
    assert "replaylast" not in published_payloads
    cancelled = [
        b["data"]
        for b in broadcasts
        if b.get("type") == "script_status" and b["data"].get("status") == "cancelled"
    ]
    assert len(cancelled) >= 1


def test_api_scripts_endpoints_and_conflict(temp_db_path):
    notif_id = record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00Z",
        timestamp_ms=1000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=15,
        flags=0,
        raw_payload="nec",
    )

    with TestClient(app) as client:
        # 1. Create script via POST /api/scripts
        payload = {
            "name": "test_script",
            "steps": [
                {"type": "sendircode", "prot": 8, "addr": 0, "cmd": 15, "flags": 0, "delay_ms": 0},
                {"type": "digital", "pin": 4, "value": 1, "delay_ms": 100},
            ],
        }
        res_create = client.post("/api/scripts", json=payload)
        assert res_create.status_code == 201
        assert res_create.json()["step_count"] == 2

        # 2. Conflict returns 409
        res_conflict = client.post("/api/scripts", json=payload)
        assert res_conflict.status_code == 409
        assert res_conflict.json() == {"detail": "Script name already exists", "conflict": True}

        # 3. Overwrite returns 200
        payload["overwrite"] = True
        payload["steps"].append({"type": "clearkeydetail", "delay_ms": 0})
        res_overwrite = client.post("/api/scripts", json=payload)
        assert res_overwrite.status_code == 200
        assert res_overwrite.json()["step_count"] == 3

        # 4. List and Get
        res_list = client.get("/api/scripts")
        assert res_list.status_code == 200
        assert len(res_list.json()["scripts"]) == 1

        res_get = client.get("/api/scripts/test_script")
        assert res_get.status_code == 200
        assert res_get.json()["name"] == "test_script"

        # 5. From notifications + conflict
        res_from = client.post(
            "/api/scripts/from-notifications",
            json={"name": "from_notif", "notification_ids": [notif_id]},
        )
        assert res_from.status_code == 201
        assert res_from.json()["steps"][0]["type"] == "sendircode"

        res_from_conflict = client.post(
            "/api/scripts/from-notifications",
            json={"name": "from_notif", "notification_ids": [notif_id]},
        )
        assert res_from_conflict.status_code == 409
        assert res_from_conflict.json()["conflict"] is True

        # 6. Run script concurrently across devices
        res_run = client.post(
            "/api/scripts/test_script/run",
            json={"target_device_ids": ["dev1", "dev2"], "speed": 2.0},
        )
        assert res_run.status_code == 200
        assert res_run.json()["status"] == "started"

        # Cancel
        res_cancel = client.post("/api/replay/cancel")
        assert res_cancel.status_code == 200

        # 7. Delete
        res_del = client.delete("/api/scripts/test_script")
        assert res_del.status_code == 200
        assert client.get("/api/scripts/test_script").status_code == 404
