import random
from datetime import datetime, timedelta, timezone

import pytest

from ingestor.batching import BatchBuffer, backoff_delays, should_flush
from ingestor.models import PartCount, SensorReading, StatusSample

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sensor(i):
    return SensorReading(T0 + timedelta(seconds=i), "l1", "m1", "temperature", float(i))


def test_backoff_exponential_and_capped():
    g = backoff_delays(base=1, cap=10)
    assert [next(g) for _ in range(6)] == [1, 2, 4, 8, 10, 10]


def test_backoff_jitter_bounds():
    g = backoff_delays(base=4, cap=4, jitter=0.5, rng=random.Random(1))
    for _ in range(50):
        assert 2.0 <= next(g) <= 4.0


def test_backoff_invalid():
    with pytest.raises(ValueError):
        next(backoff_delays(base=0))


def test_take_splits_by_kind():
    buf = BatchBuffer(100)
    buf.add(sensor(0))
    buf.add(StatusSample(T0, "l1", "m1", "RUNNING", 1.0))
    buf.add(PartCount(T0, "l2", "m9", 1, 0))
    b = buf.take(10)
    assert (len(b.sensors), len(b.statuses), len(b.parts)) == (1, 1, 1)
    assert len(b) == 3 and len(buf) == 0
    assert b.machines() == {("l1", "m1"), ("l2", "m9")}


def test_bounded_drops_oldest():
    buf = BatchBuffer(3)
    buf.extend([sensor(i) for i in range(5)])
    assert len(buf) == 3 and buf.dropped == 2
    assert [r.value for r in buf.take(3).sensors] == [2.0, 3.0, 4.0]


def test_requeue_front_preserves_order():
    buf = BatchBuffer(10)
    buf.extend([sensor(i) for i in range(4)])
    first = buf.take(2)
    buf.requeue_front(first)
    assert [r.value for r in buf.take(10).sensors] == [0.0, 1.0, 2.0, 3.0]


def test_requeue_respects_bound():
    buf = BatchBuffer(3)
    buf.extend([sensor(i) for i in range(3)])
    b = buf.take(2)
    buf.extend([sensor(10), sensor(11)])
    buf.requeue_front(b)
    assert len(buf) == 3 and buf.dropped == 2  # buffer already full: requeued records dropped


@pytest.mark.parametrize("buffered,since,expected", [
    (0, 100, False), (500, 0, True), (10, 0.5, False), (10, 1.0, True),
])
def test_should_flush(buffered, since, expected):
    assert should_flush(buffered, batch_size=500, seconds_since_flush=since, flush_interval_s=1.0) is expected
