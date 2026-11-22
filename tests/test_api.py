"""Tests for FastAPI REST endpoints and WebSocket functionality."""

import pytest
from fastapi.testclient import TestClient

from mqtt_ir_monitor.app import app
from mqtt_ir_monitor.config import settings
from mqtt_ir_monitor.db import record_notification, upsert_device_status


@pytest.fixture(autouse=True)
def setup_app_db(temp_db_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", temp_db_path)
    upsert_device_status(temp_db_path, "dev1", True)


def test_api_devices():
    with TestClient(app) as client:
        res = client.get("/api/devices")
        assert res.status_code == 200
        data = res.json()
        assert data["total_count"] == 1
        assert data["online_count"] == 1
        assert data["devices"][0]["device_id"] == "dev1"


def test_api_notifications_and_filtering(temp_db_path):
    record_notification(
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
    record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:01Z",
        timestamp_ms=2000,
        event_type="ir_rx",
        prot=2,
        addr=0,
        cmd=0,
        flags=0,
        raw_payload="noise",
    )

    with TestClient(app) as client:
        # Default hide_noise=True
        res = client.get("/api/notifications")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["notifications"][0]["prot"] == 8
        assert data["notifications"][0]["prot_label"] == "NEC (8)"

        # With hide_noise=false
        res_all = client.get("/api/notifications?hide_noise=false")
        assert res_all.status_code == 200
        assert res_all.json()["total"] == 2

        # Filter by device
        res_dev = client.get("/api/notifications?device_id=dev1")
        assert res_dev.status_code == 200
        assert res_dev.json()["total"] == 1

        # Clear notifications
        del_res = client.delete("/api/notifications")
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] >= 1

        res_empty = client.get("/api/notifications?hide_noise=false")
        assert res_empty.json()["total"] == 0


def test_api_replay_endpoints(temp_db_path, monkeypatch):
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
        # Single replay
        res = client.post(
            "/api/replay/single",
            json={"notification_id": notif_id, "target_device_id": "dev1"},
        )
        assert res.status_code == 200

        # Sequence replay
        res_seq = client.post(
            "/api/replay/sequence",
            json={
                "notification_ids": [notif_id],
                "target_device_id": "dev1",
                "speed": 1.5,
                "max_delay_ms": 3000,
            },
        )
        assert res_seq.status_code == 200
        assert res_seq.json()["status"] == "started"

        # Cancel replay
        res_cancel = client.post("/api/replay/cancel")
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "cancelled"


def test_api_mqtt_settings():
    with TestClient(app) as client:
        res = client.get("/api/settings/mqtt")
        assert res.status_code == 200
        data = res.json()
        assert "host" in data
        assert "port" in data

        res_update = client.post(
            "/api/settings/mqtt",
            json={"host": "127.0.0.1", "port": 1884, "username": "admin", "password": "pwd"},
        )
        assert res_update.status_code == 200
        assert res_update.json()["broker"]["host"] == "127.0.0.1"
        assert res_update.json()["broker"]["port"] == 1884


def test_websocket_channel():
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        # First message should be initial broker status
        init_msg = ws.receive_json()
        assert init_msg["type"] == "broker_status"

        # Send ping
        ws.send_text("ping")
        pong = ws.receive_json()
        assert pong["type"] == "pong"
