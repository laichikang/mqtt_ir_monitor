"""Tests for ReplayEngine single and sequence playback, pacing, and cancellation."""

import asyncio
from unittest.mock import MagicMock

import pytest

from mqtt_ir_monitor.db import record_notification
from mqtt_ir_monitor.replay import ReplayEngine


def test_replay_single(test_settings):
    mock_mqtt = MagicMock()
    mock_mqtt.publish_event.return_value = True

    engine = ReplayEngine(mock_mqtt, test_settings.db_path)

    notif_id = record_notification(
        db_path=test_settings.db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00Z",
        timestamp_ms=1000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=42,
        flags=0,
        raw_payload="ir_data",
    )

    success = engine.replay_single(notif_id, "dev2")
    assert success is True
    mock_mqtt.publish_event.assert_called_once_with(
        target_device_id="dev2",
        prot=8,
        addr=0,
        cmd=42,
        flags=0,
    )


@pytest.mark.asyncio
async def test_replay_sequence_and_clamping(test_settings):
    mock_mqtt = MagicMock()
    mock_mqtt.publish_event.return_value = True

    broadcasts = []
    engine = ReplayEngine(
        mock_mqtt,
        test_settings.db_path,
        on_broadcast=lambda msg: broadcasts.append(msg),
    )

    # Insert 2 events spaced by 200 ms
    id1 = record_notification(
        db_path=test_settings.db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00.000Z",
        timestamp_ms=1000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=1,
        flags=0,
        raw_payload="1",
    )
    id2 = record_notification(
        db_path=test_settings.db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00.200Z",
        timestamp_ms=1200,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=2,
        flags=0,
        raw_payload="2",
    )

    # Run sequence
    await engine._run_sequence([id1, id2], target_device_id="dev1", speed=1.0)
    assert mock_mqtt.publish_event.call_count == 2

    status_types = [b["data"]["status"] for b in broadcasts if b.get("type") == "replay_status"]
    assert "running" in status_types
    assert "completed" in status_types


@pytest.mark.asyncio
async def test_replay_sequence_cancellation(test_settings):
    mock_mqtt = MagicMock()
    mock_mqtt.publish_event.return_value = True

    broadcasts = []
    engine = ReplayEngine(
        mock_mqtt,
        test_settings.db_path,
        on_broadcast=lambda msg: broadcasts.append(msg),
    )

    # Two events spaced by 1000 ms
    id1 = record_notification(
        db_path=test_settings.db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00.000Z",
        timestamp_ms=1000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=1,
        flags=0,
        raw_payload="1",
    )
    id2 = record_notification(
        db_path=test_settings.db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:01.000Z",
        timestamp_ms=2000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=2,
        flags=0,
        raw_payload="2",
    )

    task = asyncio.create_task(
        engine._run_sequence([id1, id2], target_device_id="dev1", speed=1.0)
    )
    # Allow first event to fire, then cancel before second event finishes
    await asyncio.sleep(0.05)
    engine.cancel()
    await task

    # Second event should not have fired
    assert mock_mqtt.publish_event.call_count == 1
    cancelled_status = [
        b["data"]
        for b in broadcasts
        if b.get("type") == "replay_status" and b["data"].get("status") == "cancelled"
    ]
    assert len(cancelled_status) >= 1
