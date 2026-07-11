"""Append-only audit log.

Writes to responsible_ai/audit_log.jsonl (JSONL format) AND persists entries
to the Supabase `audit_logs` table.

Each entry:
  - timestamp (ISO)
  - session_id (nullable — some events fire before a session exists)
  - candidate_id
  - event_type
  - ai_recommendation
  - hr_decision
  - hr_notes_hash (SHA-256 of raw notes — PII never stored raw)
  - metadata (non-PII extra context)

Immutability: this module only ever appends (JSONL) or inserts (DB). It never
updates or deletes an entry. Harden further with an append-only Postgres
policy/trigger on the audit_logs table.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client

from src.settings import settings
from src.models_db import AuditLog, TABLE_AUDIT_LOGS
from src.observability import log


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _append_jsonl(entry: dict) -> None:
    """Append a single JSON line to the audit log file."""
    audit_path = Path(settings.audit_log_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def write_audit_entry(
    db: Client,
    *,
    session_id: str | None,
    candidate_id: str,
    event_type: str,
    ai_recommendation: str | None = None,
    hr_decision: str | None = None,
    hr_notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write an immutable audit entry to both the JSONL file and the DB."""
    notes_hash = _sha256(hr_notes) if hr_notes else None
    ts = datetime.now(timezone.utc).isoformat()

    entry: dict[str, Any] = {
        "timestamp": ts,
        "session_id": session_id,
        "candidate_id": candidate_id,
        "event_type": event_type,
        "ai_recommendation": ai_recommendation,
        "hr_decision": hr_decision,
        "hr_notes_hash": notes_hash,
        "metadata": metadata or {},
    }

    # 1. Append to JSONL (survives DB loss)
    _append_jsonl(entry)

    # 2. Persist to DB (insert only — never update/delete)
    record = AuditLog(
        id=str(uuid.uuid4()),
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
        ai_recommendation=ai_recommendation,
        hr_decision=hr_decision,
        hr_notes_hash=notes_hash,
        metadata_json=metadata or {},
    )
    db.table(TABLE_AUDIT_LOGS).insert(record.to_row()).execute()

    log.info(
        "audit.entry_written",
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
    )
