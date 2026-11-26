"""Tests for Live Device Recording engine and REST API endpoints."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from mqtt_ir_monitor import app as app_module
from mqtt_ir_monitor.config import settings
from mqtt_ir_monitor.db import get_script_by_name, save_script, upsert_device_status
from mqtt_ir_monitor.recording import RecordingEngine


@pytest.fixture(autouse=True)
def setup_recording_db(temp_db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_path", temp_db_path)
    upsert_device_status(temp_db_path, "deldev02", True)
    upsert_device_status(temp_db_path, "otherdev", True)


def _make_notif(
    device_id: str = "deldev02",
    event_type: str = "ir_rx",
    prot: int = 8,
    addr: int = 0x1234,
    cmd: int = 0x56,
    flags: int = 0,
    timestamp_ms: int = 1000,
) -> dict[str, Any]:
    return {
        "id": 1,
        "device_id": device_id,
        "timestamp": "2026-11-26T10:00:00.000Z",
        "timestamp_ms": timestamp_ms,
        "event_type": event_type,
        "prot": prot,
        "addr": addr,
        "cmd": cmd,
        "flags": flags,
        "raw_payload": "{}",
    }


def test_recording_engine_capture_filters_and_exact_delays() -> None:
    broadcasts: list[dict[str, Any]] = []
    engine = RecordingEngine(on_broadcast=broadcasts.append)

    status = engine.start(device_id="deldev02", include_tx=False, hide_noise=True)
    assert status["active"] is True
    assert status["device_id"] == "deldev02"
    assert status["step_count"] == 0

    # Other device is ignored
    assert engine.handle_notification(_make_notif(device_id="otherdev", timestamp_ms=1000)) is False
    # ir_tx is ignored when include_tx=False
    assert engine.handle_notification(_make_notif(event_type="ir_tx", timestamp_ms=1100)) is False
    # prot == 2 noise is ignored when hide_noise=True
    assert engine.handle_notification(_make_notif(prot=2, timestamp_ms=1200)) is False

    # First qualifying ir_rx has delay_ms == 0
    assert engine.handle_notification(_make_notif(cmd=0x01, timestamp_ms=2000)) is True
    # Second qualifying ir_rx 12,345 ms later preserves exact >5000 ms delay
    assert engine.handle_notification(_make_notif(cmd=0x02, timestamp_ms=14345)) is True

    current = engine.get_status()
    assert current["step_count"] == 2
    assert current["steps"] == [
        {"type": "sendircode", "prot": 8, "addr": 0x1234, "cmd": 0x01, "flags": 0, "delay_ms": 0},
        {
            "type": "sendircode",
            "prot": 8,
            "addr": 0x1234,
            "cmd": 0x02,
            "flags": 0,
            "delay_ms": 12345,
        },
    ]
    assert len(broadcasts) == 3  # start + 2 captured steps


def test_recording_engine_include_tx_and_noise_toggle() -> None:
    engine = RecordingEngine()
    engine.start(device_id="deldev02", include_tx=True, hide_noise=False)

    assert engine.handle_notification(
        _make_notif(event_type="ir_tx", cmd=0x10, timestamp_ms=1000)
    )
    assert engine.handle_notification(
        _make_notif(event_type="ir_rx", prot=2, cmd=0x20, timestamp_ms=1500)
    )

    steps = engine.get_status()["steps"]
    assert len(steps) == 2
    assert steps[0]["cmd"] == 0x10
    assert steps[0]["delay_ms"] == 0
    assert steps[1]["prot"] == 2
    assert steps[1]["cmd"] == 0x20
    assert steps[1]["delay_ms"] == 500


def test_api_recording_start_status_cancel_and_validation() -> None:
    with TestClient(app_module.app) as client:
        # Empty device_id -> 400
        res_empty = client.post("/api/recording/start", json={"device_id": "   "})
        assert res_empty.status_code == 400

        # Start recording
        res_start = client.post(
            "/api/recording/start",
            json={"device_id": "deldev02", "include_tx": False, "hide_noise": True},
        )
        assert res_start.status_code == 200
        data = res_start.json()
        assert data["active"] is True
        assert data["device_id"] == "deldev02"

        # Starting again while active -> 409
        res_dup = client.post("/api/recording/start", json={"device_id": "deldev02"})
        assert res_dup.status_code == 409

        # Status reflects active session
        res_status = client.get("/api/recording/status")
        assert res_status.status_code == 200
        assert res_status.json()["active"] is True

        # Cancel discards and resets
        res_cancel = client.post("/api/recording/cancel")
        assert res_cancel.status_code == 200
        assert res_cancel.json()["active"] is False
        assert res_cancel.json()["step_count"] == 0


def test_api_recording_stop_save_conflict_overwrite(temp_db_path: str) -> None:
    save_script(
        temp_db_path,
        "existing_script",
        [{"type": "delay", "delay_ms": 100}],
        overwrite=False,
    )

    with TestClient(app_module.app) as client:
        client.post("/api/recording/start", json={"device_id": "deldev02"})

        # Empty recording cannot be saved -> 400
        res_empty = client.post(
            "/api/recording/stop",
            json={"name": "empty_rec", "save": True, "overwrite": False},
        )
        assert res_empty.status_code == 400
        assert client.get("/api/recording/status").json()["active"] is True

        # Inject two live notifications via recording_engine
        app_module.recording_engine.handle_notification(_make_notif(cmd=0x0A, timestamp_ms=5000))
        app_module.recording_engine.handle_notification(_make_notif(cmd=0x0B, timestamp_ms=13500))

        # Conflict on existing script name -> 409, keeps recording active and steps intact
        res_conflict = client.post(
            "/api/recording/stop",
            json={"name": "existing_script", "save": True, "overwrite": False},
        )
        assert res_conflict.status_code == 409
        assert res_conflict.json()["conflict"] is True
        status_after_conflict = client.get("/api/recording/status").json()
        assert status_after_conflict["active"] is True
        assert status_after_conflict["step_count"] == 2

        # Retry with overwrite=True succeeds and stops recording
        res_overwrite = client.post(
            "/api/recording/stop",
            json={"name": "existing_script", "save": True, "overwrite": True},
        )
        assert res_overwrite.status_code == 200
        body = res_overwrite.json()
        assert body["saved"] is True
        assert body["step_count"] == 2
        assert body["steps"][1]["delay_ms"] == 8500
        assert client.get("/api/recording/status").json()["active"] is False

        saved = get_script_by_name(temp_db_path, "existing_script")
        assert saved is not None
        assert saved["step_count"] == 2


def test_api_recording_stop_edit_without_saving(temp_db_path: str) -> None:
    with TestClient(app_module.app) as client:
        client.post("/api/recording/start", json={"device_id": "deldev02"})
        app_module.recording_engine.handle_notification(_make_notif(cmd=0xAA, timestamp_ms=1000))
        app_module.recording_engine.handle_notification(_make_notif(cmd=0xBB, timestamp_ms=7200))

        res_stop = client.post("/api/recording/stop", json={"save": False})
        assert res_stop.status_code == 200
        body = res_stop.json()
        assert body["saved"] is False
        assert body["step_count"] == 2
        assert body["steps"][0]["cmd"] == 0xAA
        assert body["steps"][1]["delay_ms"] == 6200
        assert client.get("/api/recording/status").json()["active"] is False
