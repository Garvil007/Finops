#!/bin/bash
# Creates the FinOpsAI warehouse alongside the LiteLLM database.
#
# POSTGRES_DB (litellm) is created by the official entrypoint; this script adds
# the second database. It runs only when the data volume is empty -- to re-run
# it, tear the volume down with `docker compose down -v`.
set -euo pipefail

: "${FINOPSAI_DB:=finopsai}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	CREATE DATABASE "${FINOPSAI_DB}";
SQL

echo "created database ${FINOPSAI_DB}"
