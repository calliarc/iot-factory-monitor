"""Configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    return int(env.get(key, default))


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    return float(env.get(key, default))


@dataclass(frozen=True)
class Settings:
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_client_id: str = "factory-ingestor"
    mqtt_topic: str = "factory/+/+/+"
    database_url: str = "postgresql://factory:factory@localhost:5432/factory"
    batch_size: int = 500
    flush_interval_s: float = 1.0
    max_buffer: int = 100_000
    health_port: int = 8080
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        db_url = env.get("DATABASE_URL")
        if not db_url:
            user = env.get("POSTGRES_USER", "factory")
            pwd = env.get("POSTGRES_PASSWORD", "factory")
            host = env.get("POSTGRES_HOST", "localhost")
            port = env.get("POSTGRES_PORT", "5432")
            db = env.get("POSTGRES_DB", "factory")
            db_url = f"postgresql://{user}:{pwd}@{host}:{port}/{db}"
        s = cls(
            mqtt_host=env.get("MQTT_HOST", cls.mqtt_host),
            mqtt_port=_int(env, "MQTT_PORT", cls.mqtt_port),
            mqtt_username=env.get("MQTT_USERNAME") or None,
            mqtt_password=env.get("MQTT_PASSWORD") or None,
            mqtt_client_id=env.get("MQTT_CLIENT_ID", cls.mqtt_client_id),
            mqtt_topic=env.get("MQTT_TOPIC", cls.mqtt_topic),
            database_url=db_url,
            batch_size=_int(env, "BATCH_SIZE", cls.batch_size),
            flush_interval_s=_float(env, "FLUSH_INTERVAL_S", cls.flush_interval_s),
            max_buffer=_int(env, "MAX_BUFFER", cls.max_buffer),
            health_port=_int(env, "HEALTH_PORT", cls.health_port),
            log_level=env.get("LOG_LEVEL", cls.log_level).upper(),
        )
        if s.batch_size <= 0 or s.flush_interval_s <= 0 or s.max_buffer < s.batch_size:
            raise ValueError("require BATCH_SIZE > 0, FLUSH_INTERVAL_S > 0 and MAX_BUFFER >= BATCH_SIZE")
        return s
