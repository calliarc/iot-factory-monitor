from datetime import datetime, timedelta, timezone

import pytest

from ingestor.models import (
    InvalidMessage, Metric, PartCount, SensorReading, StatusSample, build_topic, parse_message, parse_topic,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
TS = "2026-01-01T12:00:00+00:00"


def test_parse_topic_ok():
    info = parse_topic("factory/line-1/cnc-01/temperature")
    assert (info.line, info.machine, info.metric) == ("line-1", "cnc-01", Metric.TEMPERATURE)


@pytest.mark.parametrize("topic", [
    "factory/line-1/cnc-01",                # too short
    "factory/line-1/cnc-01/temperature/x",  # too long
    "plant/line-1/cnc-01/temperature",      # wrong prefix
    "factory/Line 1/cnc-01/temperature",    # invalid identifier
    "factory/line-1/cnc-01/pressure",       # unknown metric
    "factory//cnc-01/temperature",          # empty segment
])
def test_parse_topic_rejects(topic):
    with pytest.raises(InvalidMessage):
        parse_topic(topic)


def test_build_topic_roundtrip():
    t = build_topic("line-2", "cnc-03", Metric.VIBRATION)
    assert t == "factory/line-2/cnc-03/vibration"
    assert parse_topic(t).metric is Metric.VIBRATION


def test_sensor_message():
    r = parse_message("factory/line-1/cnc-01/temperature", f'{{"ts":"{TS}","value":64.5,"unit":"C"}}', now=NOW)
    assert isinstance(r, SensorReading)
    assert r.metric == "temperature" and r.value == 64.5 and r.time == NOW


def test_timestamp_normalised_to_utc():
    r = parse_message("factory/l1/m1/vibration", b'{"ts":"2026-01-01T14:00:00+02:00","value":2.0}', now=NOW)
    assert r.time == NOW and r.time.tzinfo == timezone.utc


def test_status_message_default_interval():
    r = parse_message("factory/l1/m1/status", f'{{"ts":"{TS}","status":"DOWN"}}', now=NOW)
    assert isinstance(r, StatusSample) and r.status == "DOWN" and r.duration_s == 1.0


def test_parts_message():
    r = parse_message("factory/l1/m1/parts", f'{{"ts":"{TS}","good":3,"scrap":1}}', now=NOW)
    assert isinstance(r, PartCount) and (r.good, r.scrap) == (3, 1)


def test_extra_fields_ignored():
    r = parse_message("factory/l1/m1/spindle_speed", f'{{"ts":"{TS}","value":12000,"fw":"1.2"}}', now=NOW)
    assert r.value == 12000


@pytest.mark.parametrize("metric,payload", [
    ("temperature", "not json"),
    ("temperature", "[1,2]"),
    ("temperature", '{"value": 20}'),                                   # missing ts
    ("temperature", '{"ts":"2026-01-01T12:00:00","value":20}'),         # naive ts
    ("temperature", f'{{"ts":"{TS}","value":"hot"}}'),                  # non-numeric
    ("temperature", f'{{"ts":"{TS}","value":20,"unit":"F"}}'),          # wrong unit
    ("temperature", f'{{"ts":"{TS}","value":1000}}'),                   # implausible
    ("vibration", f'{{"ts":"{TS}","value":-1}}'),                       # negative
    ("temperature", f'{{"ts":"{TS}","value":NaN}}'),                    # NaN
    ("status", f'{{"ts":"{TS}","status":"BROKEN"}}'),                   # unknown status
    ("status", f'{{"ts":"{TS}","status":"RUNNING","interval_s":0}}'),   # non-positive interval
    ("parts", f'{{"ts":"{TS}","good":-1,"scrap":0}}'),                  # negative count
    ("parts", f'{{"ts":"{TS}","good":1.5,"scrap":0}}'),                 # fractional count
])
def test_invalid_payloads(metric, payload):
    with pytest.raises(InvalidMessage):
        parse_message(f"factory/l1/m1/{metric}", payload, now=NOW)


def test_future_timestamp_rejected():
    future = (NOW + timedelta(minutes=10)).isoformat()
    with pytest.raises(InvalidMessage, match="future"):
        parse_message("factory/l1/m1/temperature", f'{{"ts":"{future}","value":20}}', now=NOW)


def test_invalid_utf8_rejected():
    with pytest.raises(InvalidMessage):
        parse_message("factory/l1/m1/temperature", b"\xff\xfe", now=NOW)
