"""Static checks of Grafana provisioning files (no Grafana needed)."""
import json
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from ingestor.thresholds import DEFAULT_THRESHOLDS, DOWNTIME_ALERT_MINUTES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GRAFANA = ROOT / "grafana"
DASHBOARDS = sorted((GRAFANA / "dashboards").glob("*.json"))
INIT_SQL = (ROOT / "db" / "init.sql").read_text()


def _rules():
    doc = yaml.safe_load((GRAFANA / "provisioning" / "alerting" / "alert-rules.yml").read_text())
    assert doc["apiVersion"] == 1
    return {r["uid"]: r for g in doc["groups"] for r in g["rules"]}


def _threshold(rule):
    expr = next(q for q in rule["data"] if q["refId"] == rule["condition"])
    assert expr["datasourceUid"] == "__expr__" and expr["model"]["type"] == "threshold"
    ev = expr["model"]["conditions"][0]["evaluator"]
    assert ev["type"] == "gt"
    return ev["params"][0]


def test_datasource_uid():
    doc = yaml.safe_load((GRAFANA / "provisioning" / "datasources" / "timescaledb.yml").read_text())
    ds = doc["datasources"][0]
    assert ds["uid"] == "timescaledb" and ds["type"] == "grafana-postgresql-datasource"
    assert "password" not in ds  # only via secureJsonData + env var
    assert ds["secureJsonData"]["password"].startswith("${")


@pytest.mark.parametrize("metric", sorted(DEFAULT_THRESHOLDS))
def test_alert_thresholds_match_python(metric):
    rules = _rules()
    t = DEFAULT_THRESHOLDS[metric]
    assert _threshold(rules[f"factory-{metric}-warning"]) == t.warning
    assert _threshold(rules[f"factory-{metric}-critical"]) == t.critical


def test_downtime_alert_matches_python():
    assert _threshold(_rules()["factory-machine-down"]) == DOWNTIME_ALERT_MINUTES


def test_alert_rules_structure():
    rules = _rules()
    assert len(rules) == 6
    for uid, r in rules.items():
        assert len(uid) <= 40
        refs = {q["refId"] for q in r["data"]}
        assert r["condition"] in refs
        for q in r["data"]:
            if q["datasourceUid"] != "__expr__":
                assert q["datasourceUid"] == "timescaledb"
                assert q["model"]["rawSql"].strip()
        for text in r["annotations"].values():
            # "$" must be escaped as "$$" or Grafana treats it as an env variable.
            assert not re.search(r"(?<!\$)\$(?!\$)[a-z]", text), f"unescaped $ in {uid}"


def test_dashboards_valid():
    assert {p.stem for p in DASHBOARDS} == {"factory-overview", "machine-detail"}
    uids = set()
    for path in DASHBOARDS:
        d = json.loads(path.read_text())
        assert d["uid"] not in uids
        uids.add(d["uid"])
        ids = [p["id"] for p in d["panels"]]
        assert len(ids) == len(set(ids))
        for p in d["panels"]:
            for t in p.get("targets", []):
                assert t["datasource"]["uid"] == "timescaledb"


def test_dashboard_sql_references_exist_in_schema():
    objects = ["oee_window", "machine_oee_hourly", "machine_current_status", "downtime_events",
               "machine_status_1m", "part_counts_1m", "sensor_readings_1m"]
    text = "".join(p.read_text() for p in DASHBOARDS)
    for obj in objects:
        if obj in text:
            assert re.search(rf"(VIEW|FUNCTION)\s+(IF NOT EXISTS\s+)?{obj}\b", INIT_SQL), obj


def test_overview_has_oee_downtime_and_trends():
    titles = " ".join(p["title"] for path in DASHBOARDS for p in json.loads(path.read_text())["panels"])
    for word in ("OEE", "Downtime", "Temperature", "Vibration", "Spindle"):
        assert word in titles
