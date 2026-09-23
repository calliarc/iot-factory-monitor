"""Alert thresholds (pure functions).

These are the same limits that Grafana alerting evaluates
(``grafana/provisioning/alerting/alert-rules.yml``); a test asserts the two
stay in sync. The ingestor uses them to log and count breaches so that
threshold events are visible in the service logs and ``/health`` output even
without Grafana.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Threshold:
    metric: str
    warning: float
    critical: float
    unit: str

    def __post_init__(self) -> None:
        if self.critical < self.warning:
            raise ValueError("critical threshold must be >= warning threshold")


# Upper limits. Vibration limits follow ISO 10816-3 zone boundaries for
# medium-size machines on rigid foundations (4.5 mm/s zone B/C, 7.1 mm/s C/D).
DEFAULT_THRESHOLDS: dict[str, Threshold] = {
    "temperature": Threshold("temperature", warning=75.0, critical=85.0, unit="C"),
    "vibration": Threshold("vibration", warning=4.5, critical=7.1, unit="mm/s"),
}

# A machine that has been DOWN for longer than this raises an alert.
DOWNTIME_ALERT_MINUTES = 5


def classify(metric: str, value: float, thresholds: dict[str, Threshold] | None = None) -> Severity:
    """Classify a single reading. Metrics without a threshold are always OK."""
    t = (thresholds or DEFAULT_THRESHOLDS).get(metric)
    if t is None:
        return Severity.OK
    if value >= t.critical:
        return Severity.CRITICAL
    if value >= t.warning:
        return Severity.WARNING
    return Severity.OK


def is_transition(previous: Severity | None, current: Severity) -> bool:
    """True when severity changed; used to log breaches once, not every sample."""
    return previous is not current and not (previous is None and current is Severity.OK)
