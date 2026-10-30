#!/bin/sh
set -eu

: "${POSTGRES_USER:?Missing POSTGRES_USER}"
: "${POSTGRES_DB:?Missing POSTGRES_DB}"
: "${APP_DB_NAME:?Missing APP_DB_NAME}"
: "${APP_DB_USER:?Missing APP_DB_USER}"
: "${APP_DB_PASSWORD:?Missing APP_DB_PASSWORD}"
: "${N8N_DB_NAME:?Missing N8N_DB_NAME}"
: "${N8N_DB_USER:?Missing N8N_DB_USER}"
: "${N8N_DB_PASSWORD:?Missing N8N_DB_PASSWORD}"

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v app_user="$APP_DB_USER" \
  -v app_password="$APP_DB_PASSWORD" \
  -v app_db="$APP_DB_NAME" \
  -v n8n_user="$N8N_DB_USER" \
  -v n8n_password="$N8N_DB_PASSWORD" \
  -v n8n_db="$N8N_DB_NAME" <<'SQL'

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'app_user',
    :'app_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'app_user'
)
\gexec

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'n8n_user',
    :'n8n_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'n8n_user'
)
\gexec

SELECT format(
    'CREATE DATABASE %I OWNER %I',
    :'app_db',
    :'app_user'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = :'app_db'
)
\gexec

SELECT format(
    'CREATE DATABASE %I OWNER %I',
    :'n8n_db',
    :'n8n_user'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = :'n8n_db'
)
\gexec

SQL

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$APP_DB_USER" \
  --dbname "$APP_DB_NAME" \
  --file /schema.sql

echo "FlowPilot database initialization completed."