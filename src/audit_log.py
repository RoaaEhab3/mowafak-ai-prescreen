"""Append-only audit log.

Writes to responsible_ai/audit_log.jsonl (JSONL format).
Also persists entries to the DB AuditLog table.

Each entry:
  - timestamp (ISO)
  - session_id
  - candidate_id
  - event_type
  - ai_recommendation
  - hr_decision
  - hr_notes_hash (SHA-256 of raw notes — PII never stored raw)
  - metadata (non-PII extra context)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.settings import settings
from src.models_db import AuditLog as AuditLogORM
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
    db: Session,
    *,
    session_id: str,
    candidate_id: str,
    event_type: str,
    ai_recommendation: str | None = None,
    hr_decision: str | None = None,
    hr_notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write an immutable audit entry to both JSONL file and DB."""
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

    # 2. Persist to DB
    orm_entry = AuditLogORM(
        session_id=session_id,
        candidate_id=candidate_id,
        event_type=event_type,
        ai_recommendation=ai_recommendation,
        hr_decision=hr_decision,
        hr_notes_hash=notes_hash,
        metadata_json=metadata or {},
    )
    db.add(orm_entry)
    db.commit()

    log.info(
        "audit.entry_written",
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
    )
