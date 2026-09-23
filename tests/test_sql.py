"""Parse db/init.sql with the real PostgreSQL parser (pglast / libpg_query).

This checks syntax only; TimescaleDB semantics are exercised by the CI
docker compose smoke test.
"""
from pathlib import Path

import pytest

pglast = pytest.importorskip("pglast")

SQL = (Path(__file__).resolve().parents[1] / "db" / "init.sql").read_text()


def test_init_sql_parses():
    stmts = pglast.parse_sql(SQL)
    assert len(stmts) > 30


def test_function_bodies_parse():
    # Bodies of LANGUAGE sql functions are strings to the outer parser; parse them too.
    from pglast import ast
    bodies = []
    for raw in pglast.parse_sql(SQL):
        stmt = raw.stmt
        if isinstance(stmt, ast.CreateFunctionStmt):
            for opt in stmt.options:
                if opt.defname == "as":
                    bodies.append(opt.arg[0].sval)
    assert len(bodies) == 5
    for body in bodies:
        pglast.parse_sql(body)


def test_expected_objects_present():
    for name in ["sensor_readings", "machine_status", "part_counts", "machines",
                 "sensor_readings_1m", "sensor_readings_1h", "machine_status_1m", "machine_status_1h",
                 "part_counts_1m", "part_counts_1h", "machine_oee_hourly", "oee_window",
                 "add_retention_policy", "create_hypertable", "add_continuous_aggregate_policy"]:
        assert name in SQL
