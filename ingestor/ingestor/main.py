"""Ingestor entry point: MQTT subscribe -> validate -> batch -> TimescaleDB."""

from __future__ import annotations

import logging
import queue
import signal
import sys
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt
import psycopg

from .batching import BatchBuffer, backoff_delays, should_flush
from .config import Settings
from .db import Database
from .health import HealthState, start_health_server
from .models import InvalidMessage, Record, SensorReading, parse_message
from .thresholds import Severity, classify, is_transition

log = logging.getLogger("ingestor")


class Ingestor:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.state = HealthState()
        self.inbox: "queue.Queue[Record]" = queue.Queue()
        self.buffer = BatchBuffer(settings.max_buffer)
        self.db = Database(settings.database_url)
        self.stop = threading.Event()
        self._severity: dict[tuple[str, str, str], Severity] = {}
        self._invalid_logged = 0

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            clean_session=False,  # broker queues QoS 1 messages while we restart
        )
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # ----- MQTT callbacks (run on paho's network thread) -----

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code.is_failure:
            log.error("MQTT connect refused: %s", reason_code)
            self.state.update(mqtt_connected=False, last_error=f"mqtt: {reason_code}")
            return
        client.subscribe(self.s.mqtt_topic, qos=1)
        self.state.update(mqtt_connected=True)
        log.info("connected to MQTT %s:%s, subscribed to %s", self.s.mqtt_host, self.s.mqtt_port, self.s.mqtt_topic)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        self.state.update(mqtt_connected=False)
        if not self.stop.is_set():
            log.warning("MQTT disconnected (%s); paho will reconnect with backoff", reason_code)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        self.state.incr("received")
        try:
            self.inbox.put(parse_message(msg.topic, msg.payload))
        except InvalidMessage as exc:
            self.state.incr("invalid")
            # Rate-limit log noise from a misbehaving publisher.
            self._invalid_logged += 1
            if self._invalid_logged <= 20 or self._invalid_logged % 1000 == 0:
                log.warning("dropping invalid message on %s: %s", msg.topic, exc)

    # ----- main loop -----

    def _check_threshold(self, r: SensorReading) -> None:
        key = (r.line, r.machine, r.metric)
        sev = classify(r.metric, r.value)
        prev = self._severity.get(key)
        if is_transition(prev, sev):
            if sev is Severity.OK:
                log.info("threshold cleared: %s/%s %s=%.2f", r.line, r.machine, r.metric, r.value)
            else:
                self.state.incr("threshold_breaches")
                log.warning("threshold %s: %s/%s %s=%.2f", sev.value, r.line, r.machine, r.metric, r.value)
        self._severity[key] = sev

    def _connect_mqtt(self) -> None:
        for delay in backoff_delays(base=1, cap=30, jitter=0.2):
            if self.stop.is_set():
                return
            try:
                self.client.connect(self.s.mqtt_host, self.s.mqtt_port, keepalive=30)
                return
            except OSError as exc:
                log.warning("MQTT broker unreachable (%s); retrying in %.1fs", exc, delay)
                self.state.update(last_error=f"mqtt: {exc}")
                self.stop.wait(delay)

    def _drain_inbox(self, timeout: float) -> None:
        try:
            first = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return
        records = [first]
        while len(records) < self.s.batch_size * 4:
            try:
                records.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        for r in records:
            if isinstance(r, SensorReading):
                self._check_threshold(r)
        before = self.buffer.dropped
        self.buffer.extend(records)
        if self.buffer.dropped != before:
            log.error("buffer full, dropped %d oldest records", self.buffer.dropped - before)

    def run(self) -> None:
        start_health_server(self.state, self.s.health_port)
        self._connect_mqtt()
        self.client.loop_start()

        delays = backoff_delays(base=1, cap=60, jitter=0.2)
        next_db_attempt = 0.0
        last_flush = time.monotonic()
        while not self.stop.is_set():
            self._drain_inbox(timeout=0.2)
            now = time.monotonic()
            self.state.update(buffered=len(self.buffer), dropped=self.buffer.dropped)
            if now < next_db_attempt:
                continue
            if not should_flush(len(self.buffer), self.s.batch_size, now - last_flush, self.s.flush_interval_s):
                continue
            batch = self.buffer.take(self.s.batch_size)
            try:
                self.db.write(batch)
            except psycopg.OperationalError as exc:
                self.buffer.requeue_front(batch)
                delay = next(delays)
                next_db_attempt = now + delay
                self.state.update(db_connected=False, last_error=f"db: {exc}".strip()[:300])
                log.warning("database unavailable (%s); retrying in %.1fs", str(exc).strip(), delay)
                continue
            except psycopg.Error as exc:
                # Non-retryable (bad data / schema): drop this batch, keep running.
                self.state.update(last_error=f"db: {exc}".strip()[:300])
                log.error("dropping batch of %d records after database error: %s", len(batch), exc)
                self.state.incr("dropped", len(batch))
                continue
            delays = backoff_delays(base=1, cap=60, jitter=0.2)
            last_flush = time.monotonic()
            self.state.incr("written", len(batch))
            self.state.update(db_connected=True, last_flush_at=time.time())

        self._shutdown()

    def _shutdown(self) -> None:
        log.info("shutting down, flushing %d buffered records", len(self.buffer) + self.inbox.qsize())
        self.client.disconnect()
        self.client.loop_stop()
        while True:
            try:
                self.buffer.add(self.inbox.get_nowait())
            except queue.Empty:
                break
        while len(self.buffer):
            batch = self.buffer.take(self.s.batch_size)
            try:
                self.db.write(batch)
            except psycopg.Error as exc:
                log.error("final flush failed, %d records lost: %s", len(batch) + len(self.buffer), exc)
                break
        self.db.close()


def main() -> int:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ingestor = Ingestor(settings)

    def _stop(signum: int, _frame: Any) -> None:
        log.info("received signal %s", signum)
        ingestor.stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    ingestor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
