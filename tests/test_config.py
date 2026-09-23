import pytest

from ingestor.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.mqtt_host == "localhost" and s.batch_size == 500
    assert s.database_url == "postgresql://factory:factory@localhost:5432/factory"


def test_from_env_builds_url():
    s = Settings.from_env({"POSTGRES_HOST": "db", "POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p",
                           "POSTGRES_DB": "d", "MQTT_USERNAME": "ingestor", "BATCH_SIZE": "10", "log_level": "x"})
    assert s.database_url == "postgresql://u:p@db:5432/d"
    assert s.mqtt_username == "ingestor" and s.batch_size == 10


def test_database_url_wins():
    assert Settings.from_env({"DATABASE_URL": "postgresql://x/y"}).database_url == "postgresql://x/y"


def test_invalid():
    with pytest.raises(ValueError):
        Settings.from_env({"BATCH_SIZE": "1000", "MAX_BUFFER": "10"})
