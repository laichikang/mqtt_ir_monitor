"""Pytest configuration and fixtures."""

import os
import tempfile

import pytest

from mqtt_ir_monitor.config import Settings
from mqtt_ir_monitor.db import init_db


@pytest.fixture
def temp_db_path():
    """Create a temporary SQLite database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def test_settings(temp_db_path):
    """Provide settings configured with temporary database."""
    return Settings(
        mqtt_host="127.0.0.1",
        mqtt_port=1883,
        mqtt_topic_prefix="/irdevs",
        db_path=temp_db_path,
        max_notifications=10,
        min_publish_interval_ms=120,
    )
