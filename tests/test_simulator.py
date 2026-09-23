import random
from datetime import datetime, timedelta, timezone

import pytest

from simulator.machine import DOWN, IDLE, RUNNING, Machine, MachineProfile, build_fleet

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run(machine, seconds, dt=1.0):
    out = []
    for i in range(int(seconds / dt)):
        out.append(machine.step(dt, T0 + timedelta(seconds=i * dt)))
    return out


def test_fleet_names_and_determinism():
    a = build_fleet(2, 3, seed=42)
    b = build_fleet(2, 3, seed=42)
    assert [(m.line, m.machine) for m in a] == [
        ("line-1", "cnc-01"), ("line-1", "cnc-02"), ("line-1", "cnc-03"),
        ("line-2", "cnc-01"), ("line-2", "cnc-02"), ("line-2", "cnc-03")]
    assert run(a[0], 300) == run(b[0], 300)


def test_step_emits_all_metrics():
    m = build_fleet(1, 1, seed=1)[0]
    msgs = m.step(1.0, T0)
    assert [t.rsplit("/", 1)[1] for t, _ in msgs] == ["temperature", "vibration", "spindle_speed", "status", "parts"]
    assert all(t.startswith("factory/line-1/cnc-01/") for t, _ in msgs)
    assert all(p["ts"] == T0.isoformat() for _, p in msgs)


def test_long_run_realistic_ranges_and_all_states():
    m = build_fleet(1, 1, seed=7, profile=MachineProfile(mean_time_between_faults_s=600))[0]
    seen, good, scrap = set(), 0, 0
    for msgs in run(m, 6 * 3600):
        p = {t.rsplit("/", 1)[1]: v for t, v in msgs}
        seen.add(p["status"]["status"])
        assert 10 < p["temperature"]["value"] < 120
        assert 0 <= p["vibration"]["value"] < 20
        assert 0 <= p["spindle_speed"]["value"] < 14000
        good += p["parts"]["good"]
        scrap += p["parts"]["scrap"]
    assert seen == {RUNNING, IDLE, DOWN}
    assert good > 0 and 0 < scrap < good * 0.2


def test_stopped_machine_produces_nothing_and_spins_down():
    m = Machine("l", "m", rng=random.Random(0))
    m.status, m.remaining_s = DOWN, 1e9
    for msgs in run(m, 60):
        p = {t.rsplit("/", 1)[1]: v for t, v in msgs}
        assert p["parts"] == {"ts": p["parts"]["ts"], "good": 0, "scrap": 0}
    assert p["spindle_speed"]["value"] == 0.0


def test_bearing_fault_raises_vibration_until_trip():
    m = Machine("l", "m", rng=random.Random(3), profile=MachineProfile(mean_time_between_idle_s=1e12))
    m.fault = "bearing"
    peak = 0.0
    for _ in range(3600):
        m.step(1.0, T0)
        peak = max(peak, m.vibration)
        if m.status == DOWN:
            break
    assert m.status == DOWN and peak >= 7.1


def test_wear_drifts_upward_while_running():
    m = Machine("l", "m", rng=random.Random(5),
                profile=MachineProfile(mean_time_between_faults_s=1e12, mean_time_between_idle_s=1e12))
    w0 = m.wear
    run(m, 3600)
    assert m.wear > w0


def test_production_rate_close_to_ideal():
    m = Machine("l", "m", rng=random.Random(9),
                profile=MachineProfile(mean_time_between_faults_s=1e12, mean_time_between_idle_s=1e12))
    total = sum(p["good"] + p["scrap"] for msgs in run(m, 3600) for t, p in msgs if t.endswith("/parts"))
    assert 1400 < total < 1800  # ideal would be 1800 parts/h at 2 s cycle time


def test_invalid_dt():
    with pytest.raises(ValueError):
        build_fleet(1, 1, seed=0)[0].step(0, T0)
