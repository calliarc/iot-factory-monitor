"""Batch buffer and retry backoff (pure logic, no I/O)."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Iterator

from .models import PartCount, Record, SensorReading, StatusSample


def backoff_delays(base: float = 1.0, cap: float = 60.0, factor: float = 2.0, jitter: float = 0.0,
                   rng: random.Random | None = None) -> Iterator[float]:
    """Infinite exponential backoff: base, base*factor, ... capped at ``cap``.

    ``jitter`` in [0, 1] randomly reduces each delay by up to that fraction
    ("equal jitter") to avoid thundering-herd reconnects.
    """
    if base <= 0 or cap <= 0 or factor < 1 or not 0 <= jitter <= 1:
        raise ValueError("invalid backoff parameters")
    rng = rng or random.Random()
    delay = base
    while True:
        d = min(delay, cap)
        if jitter:
            d -= d * jitter * rng.random()
        yield d
        delay = min(delay * factor, cap)


@dataclass
class Batch:
    sensors: list[SensorReading] = field(default_factory=list)
    statuses: list[StatusSample] = field(default_factory=list)
    parts: list[PartCount] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.sensors) + len(self.statuses) + len(self.parts)

    def machines(self) -> set[tuple[str, str]]:
        return {(r.line, r.machine) for r in (*self.sensors, *self.statuses, *self.parts)}


class BatchBuffer:
    """Bounded FIFO of records. When full, the oldest records are dropped so a
    long database outage cannot exhaust memory."""

    def __init__(self, max_records: int) -> None:
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self._q: deque[Record] = deque()
        self.max_records = max_records
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._q)

    def add(self, record: Record) -> None:
        if len(self._q) >= self.max_records:
            self._q.popleft()
            self.dropped += 1
        self._q.append(record)

    def extend(self, records: list[Record]) -> None:
        for r in records:
            self.add(r)

    def requeue_front(self, batch: Batch) -> None:
        """Put a failed batch back at the head (respecting the bound)."""
        items = [*batch.sensors, *batch.statuses, *batch.parts]
        items.sort(key=lambda r: r.time)
        for r in reversed(items):
            if len(self._q) >= self.max_records:
                self.dropped += 1
                continue
            self._q.appendleft(r)

    def take(self, n: int) -> Batch:
        batch = Batch()
        for _ in range(min(n, len(self._q))):
            r = self._q.popleft()
            if isinstance(r, SensorReading):
                batch.sensors.append(r)
            elif isinstance(r, StatusSample):
                batch.statuses.append(r)
            else:
                batch.parts.append(r)
        return batch


def should_flush(buffered: int, batch_size: int, seconds_since_flush: float, flush_interval_s: float) -> bool:
    return buffered > 0 and (buffered >= batch_size or seconds_since_flush >= flush_interval_s)
