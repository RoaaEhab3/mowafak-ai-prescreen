"""Supabase client setup (canonical DB access layer).

Replaces the previous SQLAlchemy engine/Session setup. supabase-py talks to
Supabase over its PostgREST HTTP API, not a raw DB connection — so there is
no connection pool to manage here, no SQLAlchemy Session, and (see
models_db.py) no ORM-declared models. Table rows are plain dicts in/out.

All backend modules import the DB accessor from here (`from src.database
import get_db`). The earlier `backend/database.py` copy has been removed to
keep a single source of truth.

Required environment variables:
  SUPABASE_URL           e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY   the service_role key (NOT the anon/public key)

SECURITY:
  The service_role key bypasses Row Level Security entirely. It must only
  ever live in this backend process (env var / secrets manager) and must
  never be shipped to a browser or mobile client.
"""
from __future__ import annotations

import os
from functools import lru_cache

from supabase import create_client, Client

from src.observability import log

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")


@lru_cache(maxsize=1)
def _get_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment "
            "before the app can start."
        )
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_db() -> Client:
    """FastAPI dependency. Returns the shared Supabase client.

    Unlike the old SQLAlchemy get_db(), there is no per-request session to
    open and close in a try/finally — the client is stateless HTTP under
    the hood, so a single cached instance is safe to reuse across requests
    and across threads.
    """
    return _get_client()


def create_all_tables() -> None:
    """NOT a schema migration — supabase-py has no DDL capability.

    Table creation must happen via Supabase's SQL editor, a migration file,
    or `supabase db push` (Supabase CLI), using the schema in
    `backend/schema.sql`. This function only verifies at startup that the
    expected tables are reachable, so a missing-schema problem fails loudly
    at boot instead of on the first request.
    """
    try:
        client = _get_client()
        client.table("interview_sessions").select("id").limit(1).execute()
        log.info("db.startup_check.ok")
    except Exception as exc:
        log.error("db.startup_check.failed", error=str(exc))
        raise
