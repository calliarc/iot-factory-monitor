#!/usr/bin/env bash
# End-to-end smoke test for a running stack (used by CI after `docker compose up`).
# Checks: data flows simulator -> MQTT -> ingestor -> TimescaleDB, schema objects
# work (caggs, OEE), ingestor is healthy, Grafana has datasource/dashboards/alerts.
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose}"
TIMEOUT="${SMOKE_TIMEOUT:-180}"
set -a; . ./.env; set +a
GRAFANA="http://127.0.0.1:${GRAFANA_PORT:-3000}"
AUTH="${GRAFANA_ADMIN_USER:-admin}:${GRAFANA_ADMIN_PASSWORD}"

psql_q() {
  $COMPOSE exec -T timescaledb psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v ON_ERROR_STOP=1 -c "$1"
}

wait_for() {  # wait_for <description> <command...>
  local desc="$1"; shift
  local deadline=$((SECONDS + TIMEOUT))
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS > deadline )); then echo "FAIL: timed out waiting for $desc"; return 1; fi
    sleep 3
  done
  echo "ok: $desc"
}

has_rows() { [ "$(psql_q "SELECT count(*) FROM $1")" -gt 0 ]; }

wait_for "ingestor /health 200" curl -fsS "http://127.0.0.1:${INGESTOR_HEALTH_PORT:-8080}/health"
wait_for "sensor_readings rows" has_rows sensor_readings
wait_for "machine_status rows" has_rows machine_status
wait_for "part_counts rows" has_rows part_counts

machines=$(psql_q "SELECT count(*) FROM machines")
expected=$(( ${SIM_LINES:-2} * ${SIM_MACHINES_PER_LINE:-3} ))
[ "$machines" -eq "$expected" ] || { echo "FAIL: expected $expected machines, got $machines"; exit 1; }
echo "ok: $machines machines registered"

# Schema objects execute (real-time caggs, OEE view/function, helpers).
psql_q "SELECT count(*) FROM sensor_readings_1m" >/dev/null
psql_q "SELECT count(*) FROM sensor_readings_1h" >/dev/null
psql_q "SELECT count(*) FROM machine_oee_hourly" >/dev/null
psql_q "SELECT count(*) FROM downtime_events(now() - interval '1 hour', now())" >/dev/null
oee_rows=$(psql_q "SELECT count(*) FROM oee_window(now() - interval '1 hour', now()) WHERE availability IS NOT NULL")
[ "$oee_rows" -gt 0 ] || { echo "FAIL: oee_window returned no rows"; exit 1; }
echo "ok: continuous aggregates and OEE queries"
jobs=$(psql_q "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name IN ('policy_retention','policy_refresh_continuous_aggregate','policy_compression')")
[ "$jobs" -ge 18 ] || { echo "FAIL: expected >= 18 policies, got $jobs"; exit 1; }
echo "ok: $jobs TimescaleDB policies"

# Read-only Grafana role can read but not write.
$COMPOSE exec -T -e PGPASSWORD="$GRAFANA_DB_PASSWORD" timescaledb \
  psql -h 127.0.0.1 -U "${GRAFANA_DB_USER:-grafana_reader}" -d "$POSTGRES_DB" -tA -c "SELECT count(*) FROM oee_window(now() - interval '1 hour', now())" >/dev/null
if $COMPOSE exec -T -e PGPASSWORD="$GRAFANA_DB_PASSWORD" timescaledb \
  psql -h 127.0.0.1 -U "${GRAFANA_DB_USER:-grafana_reader}" -d "$POSTGRES_DB" -c "DELETE FROM machines" >/dev/null 2>&1; then
  echo "FAIL: grafana reader could write"; exit 1
fi
echo "ok: grafana_reader is read-only"

# MQTT rejects anonymous clients.
if $COMPOSE exec -T mosquitto mosquitto_pub -h 127.0.0.1 -t factory/x/y/temperature -m '{}' >/dev/null 2>&1; then
  echo "FAIL: anonymous MQTT publish was accepted"; exit 1
fi
echo "ok: anonymous MQTT rejected"

# Grafana provisioning.
wait_for "grafana health" curl -fsS "$GRAFANA/api/health"
curl -fsS -u "$AUTH" "$GRAFANA/api/datasources/uid/timescaledb/health" | grep -q '"status":"OK"' \
  || { echo "FAIL: datasource health"; exit 1; }
echo "ok: grafana datasource healthy"
for uid in factory-overview machine-detail; do
  curl -fsS -u "$AUTH" "$GRAFANA/api/dashboards/uid/$uid" >/dev/null || { echo "FAIL: dashboard $uid"; exit 1; }
done
echo "ok: dashboards provisioned"
rules=$(curl -fsS -u "$AUTH" "$GRAFANA/api/v1/provisioning/alert-rules" | grep -o '"uid":"factory-' | wc -l)
[ "$rules" -eq 6 ] || { echo "FAIL: expected 6 alert rules, got $rules"; exit 1; }
echo "ok: $rules alert rules provisioned"

echo "SMOKE TEST PASSED"
