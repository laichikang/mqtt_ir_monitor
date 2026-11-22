"""Configuration management for MQTT IR Monitor."""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    mqtt_host: str = field(
        default_factory=lambda: os.getenv("MQTT_HOST", "192.168.20.38")
    )
    mqtt_port: int = field(
        default_factory=lambda: int(os.getenv("MQTT_PORT", "1883"))
    )
    mqtt_username: str | None = field(
        default_factory=lambda: os.getenv("MQTT_USERNAME") or None
    )
    mqtt_password: str | None = field(
        default_factory=lambda: os.getenv("MQTT_PASSWORD") or None
    )
    mqtt_topic_prefix: str = field(
        default_factory=lambda: os.getenv("MQTT_TOPIC_PREFIX", "/irdevs")
    )
    db_path: str = field(
        default_factory=lambda: os.getenv("DB_PATH", "data/mqtt_ir_monitor.db")
    )
    max_notifications: int = field(
        default_factory=lambda: int(os.getenv("MAX_NOTIFICATIONS", "10000"))
    )
    min_publish_interval_ms: int = 120


settings = Settings()
