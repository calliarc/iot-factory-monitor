"""Topic parsing and payload validation (pure functions, no I/O).

Topic scheme::

    factory/{line}/{machine}/{metric}

Metrics and payloads (all JSON, ``ts`` is an ISO-8601 timestamp with timezone):

=================  ==========================================================
metric             payload
=================  ==========================================================
``temperature``    ``{"ts": "...", "value": 64.2, "unit": "C"}``
``vibration``      ``{"ts": "...", "value": 2.8, "unit": "mm/s"}``  (RMS)
``spindle_speed``  ``{"ts": "...", "value": 11950, "unit": "rpm"}``
``status``         ``{"ts": "...", "status": "RUNNING", "interval_s": 1.0}``
``parts``          ``{"ts": "...", "good": 1, "scrap": 0}``  (increments)
=================  ==========================================================
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

TOPIC_PREFIX = "factory"
SUBSCRIPTION = f"{TOPIC_PREFIX}/+/+/+"

# Line and machine identifiers: lowercase letters, digits, '-' and '_'.
_IDENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

# Reject timestamps too far in the future (clock skew guard).
MAX_FUTURE_SKEW = timedelta(minutes=5)


class Metric(str, Enum):
    TEMPERATURE = "temperature"
    VIBRATION = "vibration"
    SPINDLE_SPEED = "spindle_speed"
    STATUS = "status"
    PARTS = "parts"


SENSOR_METRICS = {Metric.TEMPERATURE, Metric.VIBRATION, Metric.SPINDLE_SPEED}

EXPECTED_UNITS = {
    Metric.TEMPERATURE: "C",
    Metric.VIBRATION: "mm/s",
    Metric.SPINDLE_SPEED: "rpm",
}

# Physically plausible bounds; values outside are rejected as sensor garbage.
SENSOR_BOUNDS = {
    Metric.TEMPERATURE: (-50.0, 400.0),
    Metric.VIBRATION: (0.0, 200.0),
    Metric.SPINDLE_SPEED: (0.0, 60000.0),
}


class MachineStatus(str, Enum):
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    DOWN = "DOWN"


class InvalidMessage(ValueError):
    """Raised when a topic or payload does not match the schema."""


@dataclass(frozen=True)
class TopicInfo:
    line: str
    machine: str
    metric: Metric


def parse_topic(topic: str) -> TopicInfo:
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != TOPIC_PREFIX:
        raise InvalidMessage(f"topic does not match {TOPIC_PREFIX}/{{line}}/{{machine}}/{{metric}}: {topic!r}")
    _, line, machine, metric = parts
    for name, value in (("line", line), ("machine", machine)):
        if not _IDENT_RE.match(value):
            raise InvalidMessage(f"invalid {name} identifier {value!r}")
    try:
        metric_enum = Metric(metric)
    except ValueError as exc:
        raise InvalidMessage(f"unknown metric {metric!r}") from exc
    return TopicInfo(line=line, machine=machine, metric=metric_enum)


def build_topic(line: str, machine: str, metric: Metric | str) -> str:
    metric_value = metric.value if isinstance(metric, Metric) else metric
    return f"{TOPIC_PREFIX}/{line}/{machine}/{metric_value}"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    ts: datetime

    @field_validator("ts")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ts must include a timezone offset (e.g. 'Z' or '+00:00')")
        return v.astimezone(timezone.utc)


class SensorPayload(_Payload):
    value: float = Field(allow_inf_nan=False)
    unit: str | None = None


class StatusPayload(_Payload):
    status: MachineStatus
    interval_s: float = Field(default=1.0, gt=0, le=3600)


class PartsPayload(_Payload):
    good: int = Field(ge=0, le=100_000)
    scrap: int = Field(ge=0, le=100_000)


# ----- Normalised records handed to the database layer -----


@dataclass(frozen=True)
class SensorReading:
    time: datetime
    line: str
    machine: str
    metric: str
    value: float
    kind: Literal["sensor"] = "sensor"


@dataclass(frozen=True)
class StatusSample:
    time: datetime
    line: str
    machine: str
    status: str
    duration_s: float
    kind: Literal["status"] = "status"


@dataclass(frozen=True)
class PartCount:
    time: datetime
    line: str
    machine: str
    good: int
    scrap: int
    kind: Literal["parts"] = "parts"


Record = Union[SensorReading, StatusSample, PartCount]


def parse_message(topic: str, payload: bytes | str, now: datetime | None = None) -> Record:
    """Validate one MQTT message and return a normalised record.

    Raises :class:`InvalidMessage` for anything that does not match the schema.
    """
    info = parse_topic(topic)
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidMessage(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidMessage("payload must be a JSON object")

    try:
        if info.metric in SENSOR_METRICS:
            p = SensorPayload.model_validate(data)
            expected = EXPECTED_UNITS[info.metric]
            if p.unit is not None and p.unit != expected:
                raise InvalidMessage(f"unit {p.unit!r} not allowed for {info.metric.value}, expected {expected!r}")
            lo, hi = SENSOR_BOUNDS[info.metric]
            if not lo <= p.value <= hi:
                raise InvalidMessage(f"{info.metric.value} value {p.value} outside plausible range [{lo}, {hi}]")
            record: Record = SensorReading(p.ts, info.line, info.machine, info.metric.value, p.value)
        elif info.metric is Metric.STATUS:
            s = StatusPayload.model_validate(data)
            record = StatusSample(s.ts, info.line, info.machine, s.status.value, s.interval_s)
        else:
            c = PartsPayload.model_validate(data)
            record = PartCount(c.ts, info.line, info.machine, c.good, c.scrap)
    except ValidationError as exc:
        raise InvalidMessage(f"payload failed validation: {exc.errors(include_url=False)}") from exc

    now = now or datetime.now(timezone.utc)
    if record.time - now > MAX_FUTURE_SKEW:
        raise InvalidMessage(f"timestamp {record.time.isoformat()} is too far in the future")
    return record
