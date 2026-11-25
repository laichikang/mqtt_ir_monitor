"""SQLite database operations and persistence for MQTT IR Monitor."""

import json
import os
import sqlite3
from datetime import UTC, datetime
from typing import Any


class ScriptConflictError(Exception):
    """Raised when saving a script with a name that already exists without overwrite."""


def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Create database directory and connect to SQLite with WAL mode."""
    dir_name = os.path.dirname(db_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str) -> None:
    """Initialize database tables per SPEC.md schema."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                online INTEGER NOT NULL DEFAULT 0,
                last_seen TEXT NOT NULL,
                notification_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                prot INTEGER,
                addr INTEGER,
                cmd INTEGER,
                flags INTEGER,
                raw_payload TEXT NOT NULL
            );
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_device ON notifications(device_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_event ON notifications(event_type);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_prot ON notifications(prot);"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                steps_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def upsert_device_status(
    db_path: str, device_id: str, online: bool, timestamp: str | None = None
) -> dict[str, Any]:
    """Insert or update device online status and last seen timestamp."""
    now_iso = timestamp or datetime.now(UTC).isoformat()
    online_int = 1 if online else 0
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO devices (device_id, online, last_seen, notification_count)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(device_id) DO UPDATE SET
                online = excluded.online,
                last_seen = excluded.last_seen;
            """,
            (device_id, online_int, now_iso),
        )
        conn.commit()
        cursor.execute(
            """
            SELECT device_id, online, last_seen, notification_count
            FROM devices WHERE device_id = ?
            """,
            (device_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else {}


def record_notification(
    db_path: str,
    device_id: str,
    timestamp: str,
    timestamp_ms: int,
    event_type: str,
    prot: int | None,
    addr: int | None,
    cmd: int | None,
    flags: int | None,
    raw_payload: str,
    max_rows: int = 10000,
) -> int:
    """Insert notification, update device notification count, and auto-prune table."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        # 1. Insert notification
        cursor.execute(
            """
            INSERT INTO notifications (
                device_id, timestamp, timestamp_ms, event_type,
                prot, addr, cmd, flags, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                device_id,
                timestamp,
                timestamp_ms,
                event_type,
                prot,
                addr,
                cmd,
                flags,
                raw_payload,
            ),
        )
        notification_id = cursor.lastrowid or 0

        # 2. Upsert device count and last_seen
        cursor.execute(
            """
            INSERT INTO devices (device_id, online, last_seen, notification_count)
            VALUES (?, 1, ?, 1)
            ON CONFLICT(device_id) DO UPDATE SET
                notification_count = notification_count + 1,
                last_seen = excluded.last_seen;
            """,
            (device_id, timestamp),
        )

        # 3. Auto-prune old notifications if exceeding max_rows
        if max_rows > 0:
            cursor.execute("SELECT count(*) FROM notifications;")
            total_count = cursor.fetchone()[0]
            if total_count > max_rows:
                cursor.execute(
                    """
                    DELETE FROM notifications
                    WHERE id NOT IN (
                        SELECT id FROM notifications
                        ORDER BY id DESC
                        LIMIT ?
                    );
                    """,
                    (max_rows,),
                )

        conn.commit()
        return notification_id


def get_devices(db_path: str) -> list[dict[str, Any]]:
    """Return all devices ordered by device_id."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT device_id, online, last_seen, notification_count
            FROM devices
            ORDER BY device_id ASC;
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def get_notifications(
    db_path: str,
    device_id: str | None = None,
    event_type: str | None = None,
    hide_noise: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Query stored notifications with filters, pagination, and total match count."""
    conditions: list[str] = []
    params: list[Any] = []

    if device_id:
        conditions.append("device_id = ?")
        params.append(device_id)

    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)

    if hide_noise:
        conditions.append("(prot IS NULL OR prot != 2)")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Count total matching rows
        count_sql = f"SELECT count(*) FROM notifications {where_clause};"
        cursor.execute(count_sql, params)
        total = cursor.fetchone()[0]

        # Fetch records
        query_sql = f"""
            SELECT id, device_id, timestamp, timestamp_ms, event_type,
                   prot, addr, cmd, flags, raw_payload
            FROM notifications
            {where_clause}
            ORDER BY id DESC
            LIMIT ? OFFSET ?;
        """
        cursor.execute(query_sql, params + [limit, offset])
        rows = [dict(row) for row in cursor.fetchall()]

        return rows, total


def get_notification_by_id(db_path: str, notification_id: int) -> dict[str, Any] | None:
    """Fetch single notification by its ID."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, device_id, timestamp, timestamp_ms, event_type,
                   prot, addr, cmd, flags, raw_payload
            FROM notifications
            WHERE id = ?;
            """,
            (notification_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_notifications_by_ids(db_path: str, ids: list[int]) -> list[dict[str, Any]]:
    """Fetch multiple notifications by IDs in chronological order."""
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, device_id, timestamp, timestamp_ms, event_type,
                   prot, addr, cmd, flags, raw_payload
            FROM notifications
            WHERE id IN ({placeholders})
            ORDER BY timestamp_ms ASC, id ASC;
            """,
            ids,
        )
        return [dict(row) for row in cursor.fetchall()]


def clear_notifications(db_path: str, device_id: str | None = None) -> int:
    """Clear notifications and reset notification count for device or all devices."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if device_id:
            cursor.execute("DELETE FROM notifications WHERE device_id = ?;", (device_id,))
            deleted = cursor.rowcount
            cursor.execute(
                "UPDATE devices SET notification_count = 0 WHERE device_id = ?;",
                (device_id,),
            )
        else:
            cursor.execute("DELETE FROM notifications;")
            deleted = cursor.rowcount
            cursor.execute("UPDATE devices SET notification_count = 0;")
        conn.commit()
        return deleted


def _row_to_script(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite scripts row to a dictionary with parsed steps."""
    steps = json.loads(row["steps_json"])
    return {
        "id": row["id"],
        "name": row["name"],
        "step_count": len(steps),
        "steps": steps,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_scripts(db_path: str) -> list[dict[str, Any]]:
    """Return all saved action scripts ordered by updated_at descending."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, steps_json, created_at, updated_at
            FROM scripts
            ORDER BY updated_at DESC, name ASC;
            """
        )
        return [_row_to_script(row) for row in cursor.fetchall()]


def get_script_by_name(db_path: str, name: str) -> dict[str, Any] | None:
    """Fetch a single saved script by unique name."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, steps_json, created_at, updated_at
            FROM scripts
            WHERE name = ?;
            """,
            (name,),
        )
        row = cursor.fetchone()
        return _row_to_script(row) if row else None


def save_script(
    db_path: str,
    name: str,
    steps: list[dict[str, Any]],
    overwrite: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Create or overwrite a named action script. Returns (script_dict, created)."""
    now_iso = datetime.now(UTC).isoformat()
    steps_json = json.dumps(steps)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM scripts WHERE name = ?;", (name,))
        existing = cursor.fetchone()
        if existing:
            if not overwrite:
                raise ScriptConflictError(f"Script '{name}' already exists.")
            cursor.execute(
                """
                UPDATE scripts
                SET steps_json = ?, updated_at = ?
                WHERE name = ?;
                """,
                (steps_json, now_iso, name),
            )
            created = False
        else:
            cursor.execute(
                """
                INSERT INTO scripts (name, steps_json, created_at, updated_at)
                VALUES (?, ?, ?, ?);
                """,
                (name, steps_json, now_iso, now_iso),
            )
            created = True
        conn.commit()
        cursor.execute(
            """
            SELECT id, name, steps_json, created_at, updated_at
            FROM scripts
            WHERE name = ?;
            """,
            (name,),
        )
        row = cursor.fetchone()
        return _row_to_script(row), created


def delete_script(db_path: str, name: str) -> bool:
    """Delete a saved script by name. Returns True if a row was deleted."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scripts WHERE name = ?;", (name,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
