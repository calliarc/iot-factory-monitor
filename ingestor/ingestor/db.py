"""TimescaleDB writer (psycopg 3)."""

from __future__ import annotations

import logging

import psycopg

from .batching import Batch

log = logging.getLogger(__name__)

INSERT_MACHINE = (
    "INSERT INTO machines (line, machine) VALUES (%s, %s) ON CONFLICT (line, machine) DO NOTHING"
)
INSERT_SENSOR = (
    "INSERT INTO sensor_readings (time, line, machine, metric, value) VALUES (%s, %s, %s, %s, %s)"
)
INSERT_STATUS = (
    "INSERT INTO machine_status (time, line, machine, status, duration_s) VALUES (%s, %s, %s, %s, %s)"
)
INSERT_PARTS = (
    "INSERT INTO part_counts (time, line, machine, good, scrap) VALUES (%s, %s, %s, %s, %s)"
)


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self._conn: psycopg.Connection | None = None
        self._known_machines: set[tuple[str, str]] = set()

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    def connect(self) -> None:
        self.close()
        self._conn = psycopg.connect(self.url, autocommit=True, connect_timeout=10, application_name="factory-ingestor")
        log.info("connected to database")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - best effort
                pass
        self._conn = None

    def write(self, batch: Batch) -> None:
        """Write a batch in one transaction. Raises psycopg.Error on failure."""
        if not self.connected:
            self.connect()
        assert self._conn is not None
        new_machines = batch.machines() - self._known_machines
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                if new_machines:
                    cur.executemany(INSERT_MACHINE, sorted(new_machines))
                if batch.sensors:
                    cur.executemany(INSERT_SENSOR, [(r.time, r.line, r.machine, r.metric, r.value) for r in batch.sensors])
                if batch.statuses:
                    cur.executemany(INSERT_STATUS, [(r.time, r.line, r.machine, r.status, r.duration_s) for r in batch.statuses])
                if batch.parts:
                    cur.executemany(INSERT_PARTS, [(r.time, r.line, r.machine, r.good, r.scrap) for r in batch.parts])
        except psycopg.OperationalError:
            self.close()
            raise
        self._known_machines |= new_machines
