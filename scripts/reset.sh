#!/usr/bin/env bash
#
# scripts/reset.sh
#
# Development reset utility.
#
# Puts the project back into a clean dev state by:
#   1. Deleting all ROWS (never schema/migrations) from every application
#      table, detected automatically from src/models_db.py.
#   2. Truncating responsible_ai/audit_log.jsonl back to an empty file.
#
# It never touches: DB schema, migrations, .env, other config files, source
# code, or Git files. Safe to run repeatedly (idempotent).
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve paths relative to this script, so it works no matter which
# directory it's invoked from.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

AUDIT_LOG_PATH="${PROJECT_ROOT}/responsible_ai/audit_log.jsonl"
MODELS_DB_FILE="${PROJECT_ROOT}/src/models_db.py"
ENV_FILE="${PROJECT_ROOT}/.env"

echo "Resetting Supabase data..."

# ---------------------------------------------------------------------------
# Reuse the project's existing Supabase configuration.
# The app already reads its Supabase URL/key from .env via src/settings.py.
# We source that same .env here instead of introducing a second, competing
# source of configuration, so this script always talks to whichever
# database the app itself is pointed at.
# ---------------------------------------------------------------------------
if [ -f "${ENV_FILE}" ]; then
  set -a               # export every variable sourced below
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
else
  echo "Warning: no .env found at ${ENV_FILE}; relying on variables already exported in this shell." >&2
fi

# Fail fast (and clearly) if the required connection details aren't present,
# rather than silently operating on the wrong database or none at all.
: "${SUPABASE_URL:?SUPABASE_URL is not set. Check ${ENV_FILE} or your shell environment.}"
# Prefer SUPABASE_SERVICE_KEY — that's the exact variable the app reads in
# src/database.py — then fall back to the Supabase dashboard's own naming
# (SUPABASE_SERVICE_ROLE_KEY) and the shorter SUPABASE_KEY, so this script
# works with whichever name the .env happens to use.
SUPABASE_SERVICE_KEY="${SUPABASE_SERVICE_KEY:-${SUPABASE_SERVICE_ROLE_KEY:-${SUPABASE_KEY:-}}}"
: "${SUPABASE_SERVICE_KEY:?SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE_KEY / SUPABASE_KEY) is not set. Check ${ENV_FILE}.}"

# ---------------------------------------------------------------------------
# Detect application tables from the codebase instead of hardcoding a list.
#
# src/models_db.py defines one `TABLE_<NAME> = "<table_name>"` constant per
# application table (e.g. TABLE_CANDIDATES, TABLE_SESSIONS, TABLE_QUESTIONS,
# TABLE_ANSWERS, TABLE_ASSESSMENTS, TABLE_FINAL_REPORTS, TABLE_AUDIT_LOGS).
# Grepping these out means a new table is picked up automatically the moment
# a developer adds another TABLE_* constant — this script never needs to be
# edited to stay in sync with the schema.
# ---------------------------------------------------------------------------
if [ ! -f "${MODELS_DB_FILE}" ]; then
  echo "Error: could not find ${MODELS_DB_FILE} to detect application tables." >&2
  exit 1
fi

mapfile -t APP_TABLES < <(
  grep -Eo '^TABLE_[A-Z0-9_]+[[:space:]]*=[[:space:]]*"[^"]+"' "${MODELS_DB_FILE}" \
    | sed -E 's/^TABLE_[A-Z0-9_]+[[:space:]]*=[[:space:]]*"([^"]+)"/\1/'
)

if [ "${#APP_TABLES[@]}" -eq 0 ]; then
  echo "Error: no TABLE_* constants found in ${MODELS_DB_FILE}; nothing to reset." >&2
  exit 1
fi

echo "Detected application tables: ${APP_TABLES[*]}"

# ---------------------------------------------------------------------------
# Delete rows only — never schema, indexes, or migrations.
#
# We reuse the same supabase-py client the application itself uses (via
# PostgREST) and issue a DELETE against every row of each detected table.
# This clears data while leaving table definitions, indexes, and migrations
# completely untouched, and is safe to re-run: deleting from an
# already-empty table is a no-op, which is what makes this step idempotent.
# ---------------------------------------------------------------------------
python3 - "${SUPABASE_URL}" "${SUPABASE_SERVICE_KEY}" "${APP_TABLES[@]}" <<'PY'
import sys

from supabase import create_client

url, key, *tables = sys.argv[1:]
db = create_client(url, key)

# Every application table uses a UUID primary key column named "id"
# (see FinalReport/AuditLog/etc. in src/models_db.py). PostgREST requires an
# explicit filter for DELETE, so we filter on an id value that can never
# exist, which in effect deletes every row without depending on a
# database-specific "always true" clause.
NEVER_MATCHES_ID = "00000000-0000-0000-0000-000000000000"

for table in tables:
    db.table(table).delete().neq("id", NEVER_MATCHES_ID).execute()
    print(f"  cleared table: {table}")
PY

# ---------------------------------------------------------------------------
# Clear the audit log.
# ---------------------------------------------------------------------------
echo "Clearing audit log..."

# Recreate responsible_ai/ if it doesn't exist yet (idempotent: mkdir -p is
# a no-op when the directory is already there).
mkdir -p "$(dirname "${AUDIT_LOG_PATH}")"

# Whether the file already exists, is missing, or is already empty, this
# always leaves behind exactly one empty file — never fails either way.
if [ -f "${AUDIT_LOG_PATH}" ]; then
  : > "${AUDIT_LOG_PATH}"   # truncate in place, preserving the path/perms
else
  touch "${AUDIT_LOG_PATH}" # recreate as an empty file
fi

echo "Reset complete."
