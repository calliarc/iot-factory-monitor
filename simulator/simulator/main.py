"""Simulator entry point: publish machine telemetry to MQTT (paho-mqtt v2 API)."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from .machine import MachineProfile, build_fleet

log = logging.getLogger("simulator")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("MQTT_HOST", "localhost")
    port = _env_int("MQTT_PORT", 1883)
    lines = _env_int("SIM_LINES", 2)
    per_line = _env_int("SIM_MACHINES_PER_LINE", 3)
    interval = _env_float("PUBLISH_INTERVAL_S", 1.0)
    seed_env = os.environ.get("SIM_SEED")
    fault_multiplier = _env_float("SIM_FAULT_RATE", 1.0)
    if interval <= 0 or lines <= 0 or per_line <= 0 or fault_multiplier <= 0:
        raise SystemExit("PUBLISH_INTERVAL_S, SIM_LINES, SIM_MACHINES_PER_LINE, SIM_FAULT_RATE must be > 0")

    base = MachineProfile()
    profile = MachineProfile(
        **{**base.__dict__,
           "ideal_cycle_time_s": _env_float("IDEAL_CYCLE_TIME_S", base.ideal_cycle_time_s),
           "mean_time_between_faults_s": base.mean_time_between_faults_s / fault_multiplier}
    )
    fleet = build_fleet(lines, per_line, seed=int(seed_env) if seed_env else None, profile=profile)

    stop = threading.Event()
    connected = threading.Event()

    def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code.is_failure:
            log.error("MQTT connect refused: %s", reason_code)
            return
        connected.set()
        log.info("connected to MQTT %s:%s; simulating %d machines every %.2fs", host, port, len(fleet), interval)

    def on_disconnect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        connected.clear()
        if not stop.is_set():
            log.warning("MQTT disconnected (%s); reconnecting", reason_code)

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id=os.environ.get("MQTT_CLIENT_ID", "factory-simulator"))
    user = os.environ.get("MQTT_USERNAME")
    if user:
        client.username_pw_set(user, os.environ.get("MQTT_PASSWORD"))
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.max_queued_messages_set(20000)
    client.connect_async(host, port, keepalive=30)  # loop thread retries until the broker is up
    client.loop_start()

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    last_states: dict[str, str] = {}
    next_tick = time.monotonic()
    while not stop.is_set():
        if not connected.wait(timeout=1.0):
            continue
        now = datetime.now(timezone.utc)
        for m in fleet:
            for t, payload in m.step(interval, now):
                client.publish(t, json.dumps(payload, separators=(",", ":")), qos=1)
            key = f"{m.line}/{m.machine}"
            if last_states.get(key) != m.status:
                log.info("%s -> %s%s", key, m.status, f" (fault: {m.fault})" if m.fault else "")
                last_states[key] = m.status
        next_tick += interval
        sleep = next_tick - time.monotonic()
        if sleep > 0:
            stop.wait(sleep)
        else:
            next_tick = time.monotonic()  # fell behind; don't try to catch up

    client.disconnect()
    client.loop_stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
