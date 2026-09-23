"""OEE (Overall Equipment Effectiveness) as pure functions.

This module is the reference implementation of the formulas used by the
``machine_oee_hourly`` view and the ``oee_window()`` function in
``db/init.sql``. Keep the two in sync (tests cover the Python side).

Definitions (for one machine over one time window):

* **Planned production time** = RUNNING + IDLE + DOWN seconds.
  The simulator models a 24/7 schedule, so every observed second is planned.
  IDLE (starved/blocked, no operator) therefore counts as an availability loss.
* **Run time** = RUNNING seconds.
* **Availability** = run time / planned production time.
* **Performance** = (ideal cycle time x total parts) / run time, capped at 1.0.
* **Quality** = good parts / total parts, where total = good + scrap.
* **OEE** = availability x performance x quality.

A component is ``None`` when it is undefined (no planned time, no run time,
or no parts). OEE is ``None`` if any component is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OEEResult:
    availability: float | None
    performance: float | None
    quality: float | None
    oee: float | None


def availability(run_s: float, idle_s: float, down_s: float) -> float | None:
    for v in (run_s, idle_s, down_s):
        if v < 0:
            raise ValueError("durations must be non-negative")
    planned = run_s + idle_s + down_s
    if planned <= 0:
        return None
    return run_s / planned


def performance(total_parts: int, ideal_cycle_time_s: float, run_s: float) -> float | None:
    if total_parts < 0 or run_s < 0:
        raise ValueError("counts and durations must be non-negative")
    if ideal_cycle_time_s <= 0:
        raise ValueError("ideal_cycle_time_s must be positive")
    if run_s <= 0:
        return None
    return min(1.0, (ideal_cycle_time_s * total_parts) / run_s)


def quality(good: int, scrap: int) -> float | None:
    if good < 0 or scrap < 0:
        raise ValueError("counts must be non-negative")
    total = good + scrap
    if total == 0:
        return None
    return good / total


def compute_oee(
    run_s: float,
    idle_s: float,
    down_s: float,
    good: int,
    scrap: int,
    ideal_cycle_time_s: float,
) -> OEEResult:
    a = availability(run_s, idle_s, down_s)
    p = performance(good + scrap, ideal_cycle_time_s, run_s)
    q = quality(good, scrap)
    oee = a * p * q if None not in (a, p, q) else None  # type: ignore[operator]
    return OEEResult(availability=a, performance=p, quality=q, oee=oee)
