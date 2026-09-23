"""Every message the simulator produces must pass ingestor validation."""
import json
from datetime import datetime, timedelta, timezone

from ingestor.models import PartCount, SensorReading, StatusSample, parse_message
from simulator.machine import MachineProfile, build_fleet


def test_simulator_output_validates():
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    fleet = build_fleet(2, 3, seed=11, profile=MachineProfile(mean_time_between_faults_s=300))
    kinds = set()
    for i in range(1800):
        for m in fleet:
            for topic, payload in m.step(1.0, t0 + timedelta(seconds=i)):
                rec = parse_message(topic, json.dumps(payload))
                kinds.add(type(rec))
    assert kinds == {SensorReading, StatusSample, PartCount}
