import json
import urllib.error
import urllib.request

from ingestor.health import HealthState, start_health_server


def test_snapshot_status_codes():
    s = HealthState()
    assert s.snapshot()[0] == 503
    s.update(mqtt_connected=True, db_connected=True)
    s.incr("written", 5)
    code, body = s.snapshot()
    assert code == 200 and body["status"] == "ok" and body["records_written"] == 5


def test_http_endpoint():
    s = HealthState()
    server = start_health_server(s, port=0, host="127.0.0.1")
    port = server.server_address[1]
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            raise AssertionError("expected 503")
        except urllib.error.HTTPError as e:
            assert e.code == 503
        s.update(mqtt_connected=True, db_connected=True)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            assert r.status == 200 and json.load(r)["status"] == "ok"
    finally:
        server.shutdown()
