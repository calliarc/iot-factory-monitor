import pytest

from ingestor.thresholds import DEFAULT_THRESHOLDS, Severity, Threshold, classify, is_transition


@pytest.mark.parametrize("metric,value,expected", [
    ("temperature", 60.0, Severity.OK),
    ("temperature", 75.0, Severity.WARNING),
    ("temperature", 84.9, Severity.WARNING),
    ("temperature", 85.0, Severity.CRITICAL),
    ("vibration", 2.0, Severity.OK),
    ("vibration", 4.5, Severity.WARNING),
    ("vibration", 7.1, Severity.CRITICAL),
    ("spindle_speed", 1e9, Severity.OK),  # no threshold configured
])
def test_classify(metric, value, expected):
    assert classify(metric, value) is expected


def test_custom_thresholds():
    t = {"temperature": Threshold("temperature", warning=40, critical=50, unit="C")}
    assert classify("temperature", 45, t) is Severity.WARNING


def test_threshold_ordering_validated():
    with pytest.raises(ValueError):
        Threshold("x", warning=10, critical=5, unit="")


def test_transitions():
    assert not is_transition(None, Severity.OK)          # first sample, healthy: nothing to report
    assert is_transition(None, Severity.WARNING)
    assert is_transition(Severity.OK, Severity.CRITICAL)
    assert is_transition(Severity.CRITICAL, Severity.OK)
    assert not is_transition(Severity.WARNING, Severity.WARNING)


def test_defaults_sane():
    assert set(DEFAULT_THRESHOLDS) == {"temperature", "vibration"}
