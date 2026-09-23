"""Deterministic-when-seeded machine model (pure, no I/O).

Each simulated CNC machine has:

* a status state machine: RUNNING <-> IDLE (starved/blocked) and
  RUNNING -> DOWN (fault) -> RUNNING (after repair),
* slow **drift**: tool/bearing wear accumulates while running and raises
  vibration and temperature until maintenance (a repair) resets it,
* random **faults**: ``jam`` (immediate stop), ``overheat`` (temperature climbs
  until a protective stop at 92 C) and ``bearing`` (vibration climbs until a
  protective stop at 9 mm/s),
* part production at slightly below the ideal cycle time, with scrap
  probability rising as vibration and temperature rise.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime

TOPIC_PREFIX = "factory"

RUNNING, IDLE, DOWN = "RUNNING", "IDLE", "DOWN"

OVERHEAT_TRIP_C = 92.0
BEARING_TRIP_MM_S = 9.0


def topic(line: str, machine: str, metric: str) -> str:
    return f"{TOPIC_PREFIX}/{line}/{machine}/{metric}"


@dataclass
class MachineProfile:
    ideal_cycle_time_s: float = 2.0
    nominal_rpm: float = 12000.0
    ambient_c: float = 24.0
    running_temp_rise_c: float = 36.0
    base_vibration: float = 1.8
    speed_factor: float = 0.92          # actual speed vs ideal (performance loss)
    base_scrap_rate: float = 0.015
    mean_time_between_faults_s: float = 1500.0
    mean_time_between_idle_s: float = 900.0
    wear_per_running_s: float = 1.0 / (6 * 3600)  # full wear after ~6 h of running


@dataclass
class Machine:
    line: str
    machine: str
    rng: random.Random
    profile: MachineProfile = field(default_factory=MachineProfile)

    status: str = RUNNING
    temperature: float = 0.0
    vibration: float = 0.0
    spindle_rpm: float = 0.0
    wear: float = 0.0
    fault: str | None = None
    fault_offset: float = 0.0
    remaining_s: float = 0.0
    _part_progress: float = 0.0

    def __post_init__(self) -> None:
        p = self.profile
        self.temperature = p.ambient_c + p.running_temp_rise_c * 0.8
        self.spindle_rpm = p.nominal_rpm
        self.vibration = p.base_vibration
        self.wear = self.rng.uniform(0.0, 0.3)
        # Machines differ a little from each other.
        self.profile = MachineProfile(**{**p.__dict__, "speed_factor": p.speed_factor * self.rng.uniform(0.95, 1.05)})

    # ----- state machine -----

    def _chance(self, mean_interval_s: float, dt: float) -> bool:
        """Poisson event with the given mean interval, over a step of dt seconds."""
        return self.rng.random() < 1.0 - math.exp(-dt / mean_interval_s)

    def _go_down(self) -> None:
        self.status = DOWN
        self.remaining_s = self.rng.uniform(60, 480)

    def _advance_status(self, dt: float) -> None:
        p = self.profile
        if self.status == RUNNING:
            if self.fault == "overheat" and self.temperature >= OVERHEAT_TRIP_C:
                self._go_down()
            elif self.fault == "bearing" and self.vibration >= BEARING_TRIP_MM_S:
                self._go_down()
            elif self.fault is None and self._chance(p.mean_time_between_faults_s, dt):
                self.fault = self.rng.choice(["jam", "overheat", "bearing"])
                self.fault_offset = 0.0
                if self.fault == "jam":
                    self._go_down()
            elif self.fault is None and self._chance(p.mean_time_between_idle_s, dt):
                self.status = IDLE
                self.remaining_s = self.rng.uniform(30, 240)
        else:
            self.remaining_s -= dt
            if self.remaining_s <= 0:
                if self.status == DOWN:
                    # Repair / maintenance: clear fault, reset most of the wear.
                    if self.fault in ("bearing", "overheat"):
                        self.wear *= 0.2
                    self.fault = None
                    self.fault_offset = 0.0
                self.status = RUNNING
                self.remaining_s = 0.0

    # ----- physics-ish sensor model -----

    def _advance_sensors(self, dt: float) -> None:
        p, g = self.profile, self.rng.gauss
        running = self.status == RUNNING
        if running:
            self.wear = min(1.0, self.wear + p.wear_per_running_s * dt)
            if self.fault in ("overheat", "bearing"):
                self.fault_offset += dt * (0.05 if self.fault == "overheat" else 0.005)

        # Temperature: first-order lag towards a status-dependent target.
        rise = {RUNNING: p.running_temp_rise_c, IDLE: 8.0, DOWN: 0.0}[self.status]
        target = p.ambient_c + rise + (10.0 * self.wear if running else 0.0)
        if self.fault == "overheat" and running:
            target += self.fault_offset * 4.0
            self.temperature += self.fault_offset * 0.02 * dt
        tau = 90.0
        self.temperature += (target - self.temperature) * min(1.0, dt / tau) + g(0, 0.15)

        # Spindle speed.
        if running:
            self.spindle_rpm = p.nominal_rpm * (1.0 + g(0, 0.004)) * (1.0 - 0.02 * self.wear)
        else:
            self.spindle_rpm = max(0.0, self.spindle_rpm * math.exp(-dt / 2.0) - 5.0)
            if self.spindle_rpm < 1.0:
                self.spindle_rpm = 0.0

        # Vibration RMS velocity (mm/s).
        if running:
            v = p.base_vibration + 3.0 * self.wear ** 2 + abs(g(0, 0.12))
            if self.fault == "bearing":
                v += self.fault_offset * 4.0
            self.vibration = v
        else:
            self.vibration = max(0.02, (0.3 if self.status == IDLE else 0.05) + g(0, 0.02))

    def _produce(self, dt: float) -> tuple[int, int]:
        if self.status != RUNNING:
            return 0, 0
        p = self.profile
        rate = p.speed_factor * (1.0 - 0.1 * self.wear) / p.ideal_cycle_time_s  # parts per second
        self._part_progress += rate * dt
        made = int(self._part_progress)
        self._part_progress -= made
        scrap_p = p.base_scrap_rate + 0.02 * max(0.0, self.vibration - 4.5) + 0.01 * max(0.0, self.temperature - 75.0)
        scrap_p = min(0.5, scrap_p)
        scrap = sum(1 for _ in range(made) if self.rng.random() < scrap_p)
        return made - scrap, scrap

    def step(self, dt: float, now: datetime) -> list[tuple[str, dict]]:
        """Advance the model by ``dt`` seconds and return ``(topic, payload)`` pairs."""
        if dt <= 0:
            raise ValueError("dt must be positive")
        self._advance_status(dt)
        self._advance_sensors(dt)
        good, scrap = self._produce(dt)
        ts = now.isoformat()
        t = lambda m: topic(self.line, self.machine, m)  # noqa: E731
        return [
            (t("temperature"), {"ts": ts, "value": round(self.temperature, 2), "unit": "C"}),
            (t("vibration"), {"ts": ts, "value": round(self.vibration, 3), "unit": "mm/s"}),
            (t("spindle_speed"), {"ts": ts, "value": round(self.spindle_rpm, 1), "unit": "rpm"}),
            (t("status"), {"ts": ts, "status": self.status, "interval_s": dt}),
            (t("parts"), {"ts": ts, "good": good, "scrap": scrap}),
        ]


def build_fleet(lines: int, machines_per_line: int, seed: int | None = None,
                profile: MachineProfile | None = None) -> list[Machine]:
    master = random.Random(seed)
    fleet = []
    for li in range(1, lines + 1):
        for mi in range(1, machines_per_line + 1):
            fleet.append(Machine(
                line=f"line-{li}",
                machine=f"cnc-{mi:02d}",
                rng=random.Random(master.getrandbits(64)),
                profile=profile or MachineProfile(),
            ))
    return fleet
