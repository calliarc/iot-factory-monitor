#!/bin/sh
# Creates a read-only role for Grafana. Runs once on first database init
# (sourced by the postgres image entrypoint after init.sql).
set -eu

: "${GRAFANA_DB_USER:=grafana_reader}"
: "${GRAFANA_DB_PASSWORD:?GRAFANA_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v reader="$GRAFANA_DB_USER" -v reader_pwd="$GRAFANA_DB_PASSWORD" <<'EOSQL'
CREATE ROLE :"reader" LOGIN PASSWORD :'reader_pwd';
GRANT CONNECT ON DATABASE :"DBNAME" TO :"reader";
GRANT USAGE ON SCHEMA public TO :"reader";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO :"reader";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"reader";
ALTER ROLE :"reader" SET default_transaction_read_only = on;
ALTER ROLE :"reader" SET statement_timeout = '30s';
EOSQL
