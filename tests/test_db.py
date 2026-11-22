"""Tests for SQLite persistence, pruning, and filtering."""

from mqtt_ir_monitor.db import (
    clear_notifications,
    get_devices,
    get_notification_by_id,
    get_notifications,
    get_notifications_by_ids,
    record_notification,
    upsert_device_status,
)


def test_device_upsert(temp_db_path):
    # Initial status
    dev = upsert_device_status(temp_db_path, "dev1", True, "2026-11-22T10:00:00Z")
    assert dev["device_id"] == "dev1"
    assert dev["online"] == 1
    assert dev["notification_count"] == 0

    # Offline update
    dev_updated = upsert_device_status(temp_db_path, "dev1", False, "2026-11-22T10:05:00Z")
    assert dev_updated["online"] == 0
    assert dev_updated["last_seen"] == "2026-11-22T10:05:00Z"

    devices = get_devices(temp_db_path)
    assert len(devices) == 1
    assert devices[0]["device_id"] == "dev1"


def test_record_notification_and_counts(temp_db_path):
    notif_id = record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00Z",
        timestamp_ms=1700000000000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=18,
        flags=0,
        raw_payload='{"event":"ir_rx","prot":8,"addr":0,"cmd":18,"flags":0}',
    )
    assert notif_id > 0

    devices = get_devices(temp_db_path)
    assert len(devices) == 1
    assert devices[0]["device_id"] == "dev1"
    assert devices[0]["notification_count"] == 1

    item = get_notification_by_id(temp_db_path, notif_id)
    assert item is not None
    assert item["cmd"] == 18
    assert item["prot"] == 8


def test_protocol_2_noise_filtering(temp_db_path):
    # Insert noise event (prot=2)
    record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00Z",
        timestamp_ms=1000,
        event_type="ir_rx",
        prot=2,
        addr=0,
        cmd=0,
        flags=0,
        raw_payload="noise",
    )
    # Insert valid NEC event (prot=8)
    record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:01Z",
        timestamp_ms=2000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=1,
        flags=0,
        raw_payload="nec",
    )

    # With hide_noise=True (default)
    filtered_rows, total = get_notifications(temp_db_path, hide_noise=True)
    assert total == 1
    assert len(filtered_rows) == 1
    assert filtered_rows[0]["prot"] == 8

    # With hide_noise=False
    all_rows, total_all = get_notifications(temp_db_path, hide_noise=False)
    assert total_all == 2
    assert len(all_rows) == 2


def test_auto_pruning(temp_db_path):
    # Max limit 5 rows
    for i in range(10):
        record_notification(
            db_path=temp_db_path,
            device_id="dev1",
            timestamp=f"2026-11-22T10:00:{i:02d}Z",
            timestamp_ms=1000 + i,
            event_type="ir_rx",
            prot=8,
            addr=0,
            cmd=i,
            flags=0,
            raw_payload=f"msg_{i}",
            max_rows=5,
        )

    rows, total = get_notifications(temp_db_path, hide_noise=False, limit=100)
    assert total == 5
    assert len(rows) == 5
    # The newest rows (cmd 9 down to 5) should remain
    cmds = [r["cmd"] for r in rows]
    assert cmds == [9, 8, 7, 6, 5]


def test_clear_notifications(temp_db_path):
    record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00Z",
        timestamp_ms=1000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=1,
        flags=0,
        raw_payload="nec",
    )
    record_notification(
        db_path=temp_db_path,
        device_id="dev2",
        timestamp="2026-11-22T10:00:01Z",
        timestamp_ms=2000,
        event_type="ir_tx",
        prot=8,
        addr=0,
        cmd=2,
        flags=0,
        raw_payload="nec2",
    )

    # Clear only dev1
    deleted = clear_notifications(temp_db_path, device_id="dev1")
    assert deleted == 1

    rows, total = get_notifications(temp_db_path, hide_noise=False)
    assert total == 1
    assert rows[0]["device_id"] == "dev2"

    # Clear all
    deleted_all = clear_notifications(temp_db_path)
    assert deleted_all == 1
    _, total_empty = get_notifications(temp_db_path, hide_noise=False)
    assert total_empty == 0


def test_get_notifications_by_ids(temp_db_path):
    id1 = record_notification(
        db_path=temp_db_path,
        device_id="dev1",
        timestamp="2026-11-22T10:00:00Z",
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
        timestamp="2026-11-22T10:00:01Z",
        timestamp_ms=2000,
        event_type="ir_rx",
        prot=8,
        addr=0,
        cmd=20,
        flags=0,
        raw_payload="2",
    )

    items = get_notifications_by_ids(temp_db_path, [id2, id1])
    # Chronological sort check
    assert len(items) == 2
    assert items[0]["id"] == id1
    assert items[1]["id"] == id2
