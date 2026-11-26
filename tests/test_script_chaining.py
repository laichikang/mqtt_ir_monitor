"""Tests for Script Chaining (run_script) and Multi-Script Sequence execution."""

import pytest
from fastapi.testclient import TestClient

from mqtt_ir_monitor import app as app_module
from mqtt_ir_monitor.config import settings
from mqtt_ir_monitor.db import save_script, upsert_device_status
from mqtt_ir_monitor.replay import resolve_script_sequence, resolve_script_steps


@pytest.fixture(autouse=True)
def setup_chaining_db(temp_db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_path", temp_db_path)
    upsert_device_status(temp_db_path, "dev1", True)
    upsert_device_status(temp_db_path, "dev2", True)


def test_resolve_nested_run_script_steps(temp_db_path: str) -> None:
    save_script(
        temp_db_path,
        "wifi_setting",
        [{"type": "sendircode", "prot": 8, "addr": 1, "cmd": 10, "flags": 0, "delay_ms": 0}],
    )
    save_script(
        temp_db_path,
        "picture_setting",
        [{"type": "sendircode", "prot": 8, "addr": 1, "cmd": 20, "flags": 0, "delay_ms": 150}],
    )
    save_script(
        temp_db_path,
        "full_setup",
        [
            {"type": "run_script", "script_name": "wifi_setting", "delay_ms": 0},
            {"type": "run_script", "script_name": "picture_setting", "delay_ms": 300},
        ],
    )

    resolved = resolve_script_steps(
        db_path=temp_db_path,
        steps=[
            {"type": "run_script", "script_name": "wifi_setting", "delay_ms": 0},
            {"type": "run_script", "script_name": "picture_setting", "delay_ms": 300},
        ],
        root_script_name="full_setup",
    )
    assert resolved == [
        {"type": "sendircode", "prot": 8, "addr": 1, "cmd": 10, "flags": 0, "delay_ms": 0},
        {"type": "delay", "delay_ms": 300},
        {"type": "sendircode", "prot": 8, "addr": 1, "cmd": 20, "flags": 0, "delay_ms": 150},
    ]


def test_api_run_script_missing_reference_returns_400(temp_db_path: str) -> None:
    save_script(
        temp_db_path,
        "broken_parent",
        [{"type": "run_script", "script_name": "does_not_exist", "delay_ms": 0}],
    )
    with TestClient(app_module.app) as client:
        res = client.post(
            "/api/scripts/broken_parent/run",
            json={"target_device_ids": ["dev1"], "speed": 1.0},
        )
        assert res.status_code == 400
        assert "Referenced script 'does_not_exist' not found" in res.json()["detail"]


def test_api_run_script_circular_reference_returns_400(temp_db_path: str) -> None:
    # Direct self-cycle: self_loop -> self_loop
    save_script(
        temp_db_path,
        "self_loop",
        [{"type": "run_script", "script_name": "self_loop", "delay_ms": 0}],
    )
    # Indirect cycle: script_a -> script_b -> script_a
    save_script(
        temp_db_path,
        "script_a",
        [{"type": "run_script", "script_name": "script_b", "delay_ms": 0}],
    )
    save_script(
        temp_db_path,
        "script_b",
        [{"type": "run_script", "script_name": "script_a", "delay_ms": 0}],
    )

    with TestClient(app_module.app) as client:
        res_self = client.post(
            "/api/scripts/self_loop/run",
            json={"target_device_ids": ["dev1"]},
        )
        assert res_self.status_code == 400
        assert "Circular script reference detected" in res_self.json()["detail"]

        res_indirect = client.post(
            "/api/scripts/script_a/run",
            json={"target_device_ids": ["dev1"]},
        )
        assert res_indirect.status_code == 400
        assert "Circular script reference detected" in res_indirect.json()["detail"]


def test_api_run_script_max_depth_exceeded_returns_400(temp_db_path: str) -> None:
    # Build chain s0 -> s1 -> ... -> s11 (depth 11 > max 10)
    save_script(temp_db_path, "s11", [{"type": "clearkeydetail", "delay_ms": 0}])
    for idx in range(10, -1, -1):
        save_script(
            temp_db_path,
            f"s{idx}",
            [{"type": "run_script", "script_name": f"s{idx + 1}", "delay_ms": 0}],
        )

    with TestClient(app_module.app) as client:
        res = client.post(
            "/api/scripts/s0/run",
            json={"target_device_ids": ["dev1"]},
        )
        assert res.status_code == 400
        assert "Maximum script nesting depth (10) exceeded" in res.json()["detail"]


def test_api_run_sequence_with_inter_script_delay_and_cancel(temp_db_path: str) -> None:
    save_script(
        temp_db_path,
        "wifi_setting",
        [{"type": "sendircode", "prot": 8, "addr": 1, "cmd": 1, "flags": 0, "delay_ms": 0}],
    )
    save_script(
        temp_db_path,
        "picture_setting",
        [{"type": "sendircode", "prot": 8, "addr": 1, "cmd": 2, "flags": 0, "delay_ms": 0}],
    )

    resolved = resolve_script_sequence(
        db_path=temp_db_path,
        script_names=["wifi_setting", "picture_setting"],
        inter_script_delay_ms=750,
    )
    assert len(resolved) == 3
    assert resolved[1] == {"type": "delay", "delay_ms": 750}

    with TestClient(app_module.app) as client:
        # Empty script_names -> 400
        res_empty = client.post(
            "/api/scripts/run-sequence",
            json={"script_names": [], "target_device_ids": ["dev1"]},
        )
        assert res_empty.status_code == 400

        # Valid sequence run across multiple devices
        res_run = client.post(
            "/api/scripts/run-sequence",
            json={
                "script_names": ["wifi_setting", "picture_setting"],
                "target_device_ids": ["dev1", "dev2"],
                "speed": 1.0,
                "inter_script_delay_ms": 750,
            },
        )
        assert res_run.status_code == 200
        body = res_run.json()
        assert body["status"] == "started"
        assert body["script_names"] == ["wifi_setting", "picture_setting"]
        assert body["step_count"] == 3
        assert body["inter_script_delay_ms"] == 750

        # Cancel active sequence
        res_cancel = client.post("/api/replay/cancel")
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "cancelled"
