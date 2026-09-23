-- =============================================================================
-- IoT Factory Monitor - TimescaleDB schema (v0.1.0)
--
-- Runs once, on first start of an empty database volume, via
-- /docker-entrypoint-initdb.d. Requires TimescaleDB >= 2.13 (by_range API);
-- the stack pins timescale/timescaledb:2.17.2-pg16.
--
-- Contents
--   1. Reference table        machines
--   2. Hypertables            sensor_readings, machine_status, part_counts
--   3. Compression            raw chunks compressed after 1-2 days
--   4. Continuous aggregates  *_1m (1-minute) and *_1h (1-hour), real-time
--   5. Retention              raw 7/30 days, 1-minute 90 days, 1-hour 2 years
--   6. OEE                    oee_* functions, machine_oee_hourly view,
--                             oee_window(from, to) function
--   7. Helpers                machine_current_status view,
--                             downtime_events(from, to) function
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- -----------------------------------------------------------------------------
-- 1. Machines (reference data)
--    Rows are auto-created by the ingestor the first time a machine reports.
--    Set ideal_cycle_time_s to the real design cycle time of each machine,
--    it is the basis of the OEE performance factor.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS machines (
    line                TEXT             NOT NULL,
    machine             TEXT             NOT NULL,
    ideal_cycle_time_s  DOUBLE PRECISION NOT NULL DEFAULT 2.0 CHECK (ideal_cycle_time_s > 0),
    description         TEXT,
    created_at          TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (line, machine)
);

COMMENT ON COLUMN machines.ideal_cycle_time_s IS
    'Ideal (design) time to produce one part, in seconds. Used by OEE performance.';

-- -----------------------------------------------------------------------------
-- 2. Hypertables (raw data)
-- -----------------------------------------------------------------------------

-- Analog sensor samples from factory/{line}/{machine}/{temperature|vibration|spindle_speed}
CREATE TABLE IF NOT EXISTS sensor_readings (
    time     TIMESTAMPTZ      NOT NULL,
    line     TEXT             NOT NULL,
    machine  TEXT             NOT NULL,
    metric   TEXT             NOT NULL CHECK (metric IN ('temperature', 'vibration', 'spindle_speed')),
    value    DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('sensor_readings', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS sensor_readings_machine_metric_time_idx
    ON sensor_readings (line, machine, metric, time DESC);

COMMENT ON COLUMN sensor_readings.value IS
    'temperature in C, vibration as RMS velocity in mm/s, spindle_speed in rpm';

-- Status heartbeats from factory/{line}/{machine}/status.
-- Each row means "the machine was in <status> for the <duration_s> seconds ending at <time>",
-- which makes time-in-state a simple SUM(duration_s) (time-weighted, cagg friendly).
CREATE TABLE IF NOT EXISTS machine_status (
    time        TIMESTAMPTZ      NOT NULL,
    line        TEXT             NOT NULL,
    machine     TEXT             NOT NULL,
    status      TEXT             NOT NULL CHECK (status IN ('RUNNING', 'IDLE', 'DOWN')),
    duration_s  DOUBLE PRECISION NOT NULL CHECK (duration_s > 0)
);
SELECT create_hypertable('machine_status', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS machine_status_machine_time_idx
    ON machine_status (line, machine, time DESC);

-- Part counter increments from factory/{line}/{machine}/parts.
CREATE TABLE IF NOT EXISTS part_counts (
    time     TIMESTAMPTZ NOT NULL,
    line     TEXT        NOT NULL,
    machine  TEXT        NOT NULL,
    good     INTEGER     NOT NULL CHECK (good >= 0),
    scrap    INTEGER     NOT NULL CHECK (scrap >= 0)
);
SELECT create_hypertable('part_counts', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS part_counts_machine_time_idx
    ON part_counts (line, machine, time DESC);

-- -----------------------------------------------------------------------------
-- 3. Compression (native columnar compression of older chunks)
-- -----------------------------------------------------------------------------
ALTER TABLE sensor_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'line, machine, metric',
    timescaledb.compress_orderby   = 'time DESC'
);
SELECT add_compression_policy('sensor_readings', INTERVAL '1 day', if_not_exists => TRUE);

ALTER TABLE machine_status SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'line, machine',
    timescaledb.compress_orderby   = 'time DESC'
);
SELECT add_compression_policy('machine_status', INTERVAL '2 days', if_not_exists => TRUE);

ALTER TABLE part_counts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'line, machine',
    timescaledb.compress_orderby   = 'time DESC'
);
SELECT add_compression_policy('part_counts', INTERVAL '2 days', if_not_exists => TRUE);

-- -----------------------------------------------------------------------------
-- 4. Continuous aggregates
--    materialized_only = false enables real-time aggregation: the most recent,
--    not-yet-materialized buckets are computed on the fly from raw data, so
--    dashboards are live while the policies keep older buckets materialized.
-- -----------------------------------------------------------------------------

-- Sensors, 1 minute
CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', time) AS bucket,
       line, machine, metric,
       avg(value)  AS avg_value,
       min(value)  AS min_value,
       max(value)  AS max_value,
       count(*)    AS samples
FROM sensor_readings
GROUP BY bucket, line, machine, metric
WITH NO DATA;

SELECT add_continuous_aggregate_policy('sensor_readings_1m',
    start_offset => INTERVAL '2 hours',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE);

-- Sensors, 1 hour
CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
       line, machine, metric,
       avg(value)  AS avg_value,
       min(value)  AS min_value,
       max(value)  AS max_value,
       count(*)    AS samples
FROM sensor_readings
GROUP BY bucket, line, machine, metric
WITH NO DATA;

SELECT add_continuous_aggregate_policy('sensor_readings_1h',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);

-- Machine status (time in state), 1 minute
CREATE MATERIALIZED VIEW IF NOT EXISTS machine_status_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', time) AS bucket,
       line, machine,
       COALESCE(sum(duration_s) FILTER (WHERE status = 'RUNNING'), 0) AS run_s,
       COALESCE(sum(duration_s) FILTER (WHERE status = 'IDLE'),    0) AS idle_s,
       COALESCE(sum(duration_s) FILTER (WHERE status = 'DOWN'),    0) AS down_s,
       last(status, time) AS last_status
FROM machine_status
GROUP BY bucket, line, machine
WITH NO DATA;

SELECT add_continuous_aggregate_policy('machine_status_1m',
    start_offset => INTERVAL '2 hours',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE);

-- Machine status, 1 hour
CREATE MATERIALIZED VIEW IF NOT EXISTS machine_status_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
       line, machine,
       COALESCE(sum(duration_s) FILTER (WHERE status = 'RUNNING'), 0) AS run_s,
       COALESCE(sum(duration_s) FILTER (WHERE status = 'IDLE'),    0) AS idle_s,
       COALESCE(sum(duration_s) FILTER (WHERE status = 'DOWN'),    0) AS down_s,
       last(status, time) AS last_status
FROM machine_status
GROUP BY bucket, line, machine
WITH NO DATA;

SELECT add_continuous_aggregate_policy('machine_status_1h',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);

-- Part counts, 1 minute
CREATE MATERIALIZED VIEW IF NOT EXISTS part_counts_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', time) AS bucket,
       line, machine,
       sum(good)  AS good,
       sum(scrap) AS scrap
FROM part_counts
GROUP BY bucket, line, machine
WITH NO DATA;

SELECT add_continuous_aggregate_policy('part_counts_1m',
    start_offset => INTERVAL '2 hours',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists => TRUE);

-- Part counts, 1 hour
CREATE MATERIALIZED VIEW IF NOT EXISTS part_counts_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
       line, machine,
       sum(good)  AS good,
       sum(scrap) AS scrap
FROM part_counts
GROUP BY bucket, line, machine
WITH NO DATA;

SELECT add_continuous_aggregate_policy('part_counts_1h',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE);

-- -----------------------------------------------------------------------------
-- 5. Retention
--    Raw data is short-lived; aggregates keep the long-term history.
--    Keep raw retention longer than every cagg start_offset (max 1 day).
-- -----------------------------------------------------------------------------
SELECT add_retention_policy('sensor_readings',    INTERVAL '7 days',   if_not_exists => TRUE);
SELECT add_retention_policy('machine_status',     INTERVAL '30 days',  if_not_exists => TRUE);
SELECT add_retention_policy('part_counts',        INTERVAL '30 days',  if_not_exists => TRUE);
SELECT add_retention_policy('sensor_readings_1m', INTERVAL '90 days',  if_not_exists => TRUE);
SELECT add_retention_policy('machine_status_1m',  INTERVAL '90 days',  if_not_exists => TRUE);
SELECT add_retention_policy('part_counts_1m',     INTERVAL '90 days',  if_not_exists => TRUE);
SELECT add_retention_policy('sensor_readings_1h', INTERVAL '730 days', if_not_exists => TRUE);
SELECT add_retention_policy('machine_status_1h',  INTERVAL '730 days', if_not_exists => TRUE);
SELECT add_retention_policy('part_counts_1h',     INTERVAL '730 days', if_not_exists => TRUE);

-- -----------------------------------------------------------------------------
-- 6. OEE - Overall Equipment Effectiveness
--
--   OEE = Availability x Performance x Quality
--
--   Availability = Run time / Planned production time
--                = RUNNING s / (RUNNING s + IDLE s + DOWN s)
--                  (24/7 schedule: every observed second is planned time,
--                   so IDLE and DOWN are both availability losses)
--   Performance  = (Ideal cycle time x Total parts) / Run time, capped at 1
--                  (speed losses: running slower than the design rate)
--   Quality      = Good parts / Total parts,  Total parts = good + scrap
--
--   Each factor is NULL when undefined (no planned time / no run time /
--   no parts); OEE is then NULL too. The Python reference implementation is
--   ingestor/ingestor/oee.py and is covered by unit tests.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION oee_availability(run_s DOUBLE PRECISION, idle_s DOUBLE PRECISION, down_s DOUBLE PRECISION)
RETURNS DOUBLE PRECISION LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT run_s / NULLIF(run_s + idle_s + down_s, 0)
$$;

CREATE OR REPLACE FUNCTION oee_performance(total_parts BIGINT, ideal_cycle_time_s DOUBLE PRECISION, run_s DOUBLE PRECISION)
RETURNS DOUBLE PRECISION LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT LEAST(1.0, (ideal_cycle_time_s * total_parts) / NULLIF(run_s, 0))
$$;

CREATE OR REPLACE FUNCTION oee_quality(good BIGINT, scrap BIGINT)
RETURNS DOUBLE PRECISION LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT good::DOUBLE PRECISION / NULLIF(good + scrap, 0)
$$;

-- Hourly OEE per machine (built on the 1-hour continuous aggregates).
CREATE OR REPLACE VIEW machine_oee_hourly AS
SELECT s.bucket,
       s.line,
       s.machine,
       s.run_s,
       s.idle_s,
       s.down_s,
       COALESCE(p.good,  0)::BIGINT AS good,
       COALESCE(p.scrap, 0)::BIGINT AS scrap,
       COALESCE(m.ideal_cycle_time_s, 2.0) AS ideal_cycle_time_s,
       f.availability,
       f.performance,
       f.quality,
       f.availability * f.performance * f.quality AS oee
FROM machine_status_1h s
LEFT JOIN part_counts_1h p
       ON p.bucket = s.bucket AND p.line = s.line AND p.machine = s.machine
LEFT JOIN machines m
       ON m.line = s.line AND m.machine = s.machine
CROSS JOIN LATERAL (
    SELECT oee_availability(s.run_s, s.idle_s, s.down_s) AS availability,
           oee_performance((COALESCE(p.good, 0) + COALESCE(p.scrap, 0))::BIGINT,
                           COALESCE(m.ideal_cycle_time_s, 2.0), s.run_s) AS performance,
           oee_quality(COALESCE(p.good, 0)::BIGINT, COALESCE(p.scrap, 0)::BIGINT) AS quality
) f;

COMMENT ON VIEW machine_oee_hourly IS
    'OEE = availability x performance x quality per machine and hour. See db/init.sql section 6.';

-- OEE per machine for an arbitrary window (used by Grafana with $__timeFrom()/$__timeTo()).
-- Uses the 1-minute aggregates, so the window is resolved to whole minutes.
CREATE OR REPLACE FUNCTION oee_window(p_from TIMESTAMPTZ, p_to TIMESTAMPTZ)
RETURNS TABLE (
    line               TEXT,
    machine            TEXT,
    run_s              DOUBLE PRECISION,
    idle_s             DOUBLE PRECISION,
    down_s             DOUBLE PRECISION,
    good               BIGINT,
    scrap              BIGINT,
    availability       DOUBLE PRECISION,
    performance        DOUBLE PRECISION,
    quality            DOUBLE PRECISION,
    oee                DOUBLE PRECISION
)
LANGUAGE sql STABLE AS $$
    WITH st AS (
        SELECT s.line, s.machine,
               sum(s.run_s) AS run_s, sum(s.idle_s) AS idle_s, sum(s.down_s) AS down_s
        FROM machine_status_1m s
        WHERE s.bucket >= p_from AND s.bucket < p_to
        GROUP BY s.line, s.machine
    ),
    pc AS (
        SELECT c.line, c.machine,
               sum(c.good)::BIGINT AS good, sum(c.scrap)::BIGINT AS scrap
        FROM part_counts_1m c
        WHERE c.bucket >= p_from AND c.bucket < p_to
        GROUP BY c.line, c.machine
    ),
    f AS (
        SELECT st.line, st.machine, st.run_s, st.idle_s, st.down_s,
               COALESCE(pc.good, 0)  AS good,
               COALESCE(pc.scrap, 0) AS scrap,
               oee_availability(st.run_s, st.idle_s, st.down_s) AS availability,
               oee_performance(COALESCE(pc.good, 0) + COALESCE(pc.scrap, 0),
                               COALESCE(m.ideal_cycle_time_s, 2.0), st.run_s) AS performance,
               oee_quality(COALESCE(pc.good, 0), COALESCE(pc.scrap, 0)) AS quality
        FROM st
        LEFT JOIN pc ON pc.line = st.line AND pc.machine = st.machine
        LEFT JOIN machines m ON m.line = st.line AND m.machine = st.machine
    )
    SELECT f.line, f.machine, f.run_s, f.idle_s, f.down_s, f.good, f.scrap,
           f.availability, f.performance, f.quality,
           f.availability * f.performance * f.quality
    FROM f
    ORDER BY f.line, f.machine
$$;

-- -----------------------------------------------------------------------------
-- 7. Helpers for dashboards
-- -----------------------------------------------------------------------------

-- Latest status per machine (machines silent for 2 minutes are reported as NULL status).
CREATE OR REPLACE VIEW machine_current_status AS
SELECT m.line,
       m.machine,
       CASE WHEN ls.time > now() - INTERVAL '2 minutes' THEN ls.status END AS status,
       ls.time AS last_seen
FROM machines m
LEFT JOIN LATERAL (
    SELECT ms.status, ms.time
    FROM machine_status ms
    WHERE ms.line = m.line AND ms.machine = m.machine
      AND ms.time > now() - INTERVAL '1 day'
    ORDER BY ms.time DESC
    LIMIT 1
) ls ON TRUE;

-- Contiguous DOWN periods ("gaps and islands" over the raw status heartbeats).
CREATE OR REPLACE FUNCTION downtime_events(p_from TIMESTAMPTZ, p_to TIMESTAMPTZ)
RETURNS TABLE (
    line        TEXT,
    machine     TEXT,
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ,
    duration_s  DOUBLE PRECISION
)
LANGUAGE sql STABLE AS $$
    WITH s AS (
        SELECT ms.line, ms.machine, ms.time, ms.status, ms.duration_s,
               CASE WHEN ms.status IS DISTINCT FROM
                         lag(ms.status) OVER (PARTITION BY ms.line, ms.machine ORDER BY ms.time)
                    THEN 1 ELSE 0 END AS changed
        FROM machine_status ms
        WHERE ms.time >= p_from AND ms.time < p_to
    ),
    g AS (
        SELECT s.*, sum(s.changed) OVER (PARTITION BY s.line, s.machine ORDER BY s.time) AS grp
        FROM s
    )
    SELECT g.line, g.machine,
           min(g.time) - make_interval(secs => min(g.duration_s)) AS started_at,
           max(g.time) AS ended_at,
           sum(g.duration_s) AS duration_s
    FROM g
    WHERE g.status = 'DOWN'
    GROUP BY g.line, g.machine, g.grp
    ORDER BY started_at DESC
$$;
