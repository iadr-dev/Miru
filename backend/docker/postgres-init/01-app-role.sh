#!/bin/sh
# Runs once, when the Postgres data directory is first created. Creates the ordinary role the app
# connects as when row-level security is on (see db/rls.py): Postgres exempts superusers - and the
# POSTGRES_USER above is one - from every policy, so RLS only protects connections made as a role
# like this one. It owns the tables it creates; FORCE ROW LEVEL SECURITY then applies to it.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE miru_app LOGIN PASSWORD '${POSTGRES_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO miru_app;
GRANT ALL ON SCHEMA public TO miru_app;
SQL
