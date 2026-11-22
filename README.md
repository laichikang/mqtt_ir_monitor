# MQTT IR Device Monitor & Replay Web App

A web dashboard and backend service to monitor MQTT brokers for registered `esp8266_ir_remote_simulator` devices, record timestamped notifications in SQLite, and replay single or multi-event timed IR sequences to any online device.

## Features

- **MQTT Protocol Integration**:
  - Subscribes to `<prefix>/+/status` for retained device status (LWT: `{"online": false}`).
  - Subscribes to `<prefix>/+/notification` for JSON IR events (`ir_rx`, `ir_tx`) and command replies.
  - Publishes `sendircode?prot=<p>&addr=<a>&cmd=<c>&flags=<f>` to `<prefix>/<target_device_id>/events`.
  - Enforces minimum 120 ms pacing interval between consecutive publications to prevent microcontroller queue overflows (ADR-007).
- **SQLite Persistence & Auto-Pruning**:
  - Automatically records all events and maintains device registry and notification counts.
  - Automatically prunes database to keep the most recent `MAX_NOTIFICATIONS` rows (default 10,000).
- **REST & Real-Time WebSocket API**:
  - Live WebSocket (`/ws`) broadcasting broker connection updates, device status changes, new notifications, and replay progress.
  - REST endpoints for querying filtered notifications, clearing history, single replay, sequence replay, and runtime MQTT settings re-configuration.
- **Vanilla HTML/CSS/JS Web Dashboard**:
  - Top Bar with live broker connection indicator, address, and runtime settings modal.
  - Registered Devices panel with online/offline badges, last seen timestamps, and message counts (clicking filters the notification feed).
  - Notifications panel with multi-field filtering (Device, Event type, Hide protocol-2 noise), selectable rows, and individual Replay buttons.
  - Replay Toolbar supporting target device selection, speed control (`0.1x` to `5.0x`), batch replay of selected events with preserved timing intervals, and live progress display with cancellation support.

## Installation

Ensure Python 3.11+ and `uv` are installed.

```bash
cd ~/projects/mqtt_ir_monitor
uv sync
```

## Running the Application

Start the web server using `uv run uvicorn`:

```bash
uv run uvicorn mqtt_ir_monitor.app:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser at `http://localhost:8000`.

## Configuration

Environment variables can configure the application:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MQTT_HOST` | `192.168.20.38` | MQTT broker hostname or IP |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | (None) | MQTT username |
| `MQTT_PASSWORD` | (None) | MQTT password |
| `MQTT_TOPIC_PREFIX` | `/irdevs` | Root topic prefix |
| `DB_PATH` | `data/mqtt_ir_monitor.db` | SQLite database file location |
| `MAX_NOTIFICATIONS`| `10000` | Maximum rows retained in notifications table |

You can also update the MQTT broker host, port, and credentials dynamically at runtime via the web UI Settings dialog (`⚙ Settings`) or the `/api/settings/mqtt` API without restarting the application.

## Running Tests and Local Checks

Run the automated test suite:

```bash
uv run pytest
```

Run code formatting and linting:

```bash
uv run ruff check
```

Run type checking:

```bash
uv run mypy src
```

## REST API Summary

- `GET /` — Serve web dashboard.
- `GET /api/devices` — List registered devices and broker status.
- `GET /api/notifications` — List notifications with filters (`device_id`, `event_type`, `hide_noise`, `limit`, `offset`).
- `DELETE /api/notifications` — Clear history (optional `device_id`).
- `POST /api/replay/single` — Replay single IR notification (`{"notification_id": int, "target_device_id": str}`).
- `POST /api/replay/sequence` — Replay sequence preserving timestamp deltas (`{"notification_ids": [int], "target_device_id": str, "speed": float, "max_delay_ms": int}`).
- `POST /api/replay/cancel` — Cancel active sequence replay.
- `GET /api/settings/mqtt` — Get current broker settings.
- `POST /api/settings/mqtt` — Update broker settings and reconnect live.
- `GET /api/scripts` — List all saved Action Scripts.
- `GET /api/scripts/{name}` — Fetch a saved Action Script by name.
- `POST /api/scripts` — Create or overwrite a named Action Script (returns HTTP 409 on name conflict unless `overwrite: true`).
- `POST /api/scripts/from-notifications` — Compile selected IR notifications into a named Action Script with timestamp deltas.
- `DELETE /api/scripts/{name}` — Delete a saved Action Script.
- `POST /api/scripts/{name}/run` — Execute a saved Action Script concurrently across multiple target devices with per-device 120 ms minimum pacing.
- `WS /ws` — Real-time event push stream (`broker_status`, `device_update`, `notification`, `replay_status`, `script_status`).

## Action Scripts

Action Scripts let you save, edit, export/import, and replay ordered device action sequences across one or more devices concurrently.

Supported step types:
- `sendircode`: `{"type": "sendircode", "prot": 8, "addr": 0, "cmd": 12, "flags": 0, "delay_ms": 0}`
- `digital`: `{"type": "digital", "pin": 5, "value": 1, "delay_ms": 0}`
- `clearkeydetail`: `{"type": "clearkeydetail", "delay_ms": 0}`
- `replaylast`: `{"type": "replaylast", "delay_ms": 0}`
- `delay`: `{"type": "delay", "delay_ms": 500}`

In the web dashboard:
- Select notifications and click **Save as Script** to convert them into a named `sendircode` script.
- Use the **Action Scripts** panel to create, visually edit, export/import JSON, delete, or run scripts concurrently across checked target devices.
