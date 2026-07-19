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
  - previous_hash (hash-chain link to the prior entry, or GENESIS_HASH)
  - current_hash (SHA-256 over this entry's contents + previous_hash)

Immutability: this module only ever appends (JSONL) or inserts (DB). It never
updates or deletes an entry. Harden further with an append-only Postgres
policy/trigger on the audit_logs table.

Hash-chain integrity: every entry embeds the hash of the entry written
immediately before it (`previous_hash`) and a hash of its own contents
(`current_hash`). This turns the JSONL ledger into a simple blockchain-style
chain: rewriting, deleting, or reordering any past entry changes the hash
that later entries point back to, which `verify_chain()` will detect.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client

from src.settings import settings
from src.models_db import AuditLog, TABLE_AUDIT_LOGS
from src.observability import log

# Sentinel previous_hash used by the very first entry in the chain, since
# there is no prior entry to link back to.
GENESIS_HASH = "GENESIS"

# Serializes the read-last-hash -> compute -> append critical section of
# write_audit_entry(). FastAPI runs the sync endpoints in a threadpool, so two
# overlapping requests could otherwise both read the same previous_hash and
# append two entries pointing at it — forking the chain and making a legitimate
# ledger fail verify_chain() with reason="broken_link". This lock guarantees a
# single linear writer WITHIN this process.
#
# NOTE: this does NOT cover multiple processes (e.g. `uvicorn --workers 2`). If
# the API is ever run multi-process, replace this with a cross-process file lock
# (portalocker / fcntl / msvcrt) or serialize audit writes through the DB.
_write_lock = threading.Lock()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compute_hash(entry: dict) -> str:
    """
    Compute a deterministic SHA-256 hash for an audit entry.

    The entry is serialized to canonical JSON (keys sorted, no incidental
    whitespace) before hashing, so logically identical content always
    produces the same hash regardless of key insertion order. Because the
    caller includes `previous_hash` inside `entry` before calling this
    function, the resulting hash binds each entry to its predecessor:
    changing any field in this entry, or the previous entry's hash, produces
    a completely different `current_hash`.
    """
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _append_jsonl(entry: dict) -> None:
    """Append a single JSON line to the audit log file."""
    audit_path = Path(settings.audit_log_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _get_last_hash() -> str:
    """
    Return the `current_hash` of the most recently appended JSONL entry, or
    GENESIS_HASH if the ledger is empty (this write would start the chain).

    The JSONL file is treated as the source of truth for chain linkage, so
    only its last line needs to be read.
    """
    audit_path = Path(settings.audit_log_path)
    if not audit_path.exists():
        return GENESIS_HASH

    last_line: str | None = None
    with audit_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                last_line = line

    if last_line is None:
        return GENESIS_HASH

    last_entry = json.loads(last_line)
    return last_entry.get("current_hash", GENESIS_HASH)


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

    # --- Hash-chain linkage (atomic) -----------------------------------
    # Reading the last hash, stamping this entry with it, and appending must be
    # a single critical section — otherwise two concurrent writers link to the
    # same previous_hash and fork the chain. Hold the lock across read+compute+
    # append only (not the DB call below), so the JSONL ledger stays strictly
    # linear.
    with _write_lock:
        # previous_hash ties this entry to whatever was written immediately
        # before it (or to GENESIS_HASH if this is the first entry ever).
        previous_hash = _get_last_hash()
        entry["previous_hash"] = previous_hash
        # current_hash is a SHA-256 digest over this entry's own contents plus
        # previous_hash. Tampering with this entry, or with any earlier entry
        # (which would change the previous_hash values downstream), is
        # detectable via verify_chain().
        entry["current_hash"] = compute_hash(entry)

        # Append to JSONL (survives DB loss). Append-only: existing lines are
        # never rewritten or removed.
        _append_jsonl(entry)

    # 2. Persist to DB (insert only — never update/delete). The hash-chain
    # fields ride along inside metadata_json so no DB schema change is
    # required to preserve chain integrity information in Supabase too.
    db_metadata = dict(metadata or {})
    db_metadata["previous_hash"] = previous_hash
    db_metadata["current_hash"] = entry["current_hash"]
    record = AuditLog(
        id=str(uuid.uuid4()),
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
        ai_recommendation=ai_recommendation,
        hr_decision=hr_decision,
        hr_notes_hash=notes_hash,
        metadata_json=db_metadata,
    )
    db.table(TABLE_AUDIT_LOGS).insert(record.to_row()).execute()
    log.info(
        "audit.entry_written",
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
        previous_hash=previous_hash,
        current_hash=entry["current_hash"],
    )


def verify_chain() -> bool:
    """
    Verify the integrity of the entire append-only audit chain stored in the
    JSONL ledger.

    Walking the file in order, for every entry this checks:
      1. Linkage: entry["previous_hash"] equals the current_hash of the
         entry immediately before it (or GENESIS_HASH for the first entry).
      2. Content: entry["current_hash"] equals a freshly recomputed hash of
         the entry's own contents (i.e. everything the entry stores except
         its own current_hash field) plus its previous_hash.

    If any past entry was modified, deleted, or reordered, either check (1)
    or (2) will fail for that entry — or for a later entry whose
    previous_hash no longer lines up — so this function returns False as
    soon as a mismatch is found. Returns True only if every entry in the
    file passes both checks.
    """
    audit_path = Path(settings.audit_log_path)
    if not audit_path.exists():
        # Nothing has been written yet, so there is no chain to violate.
        log.info("audit.chain_verified", entry_count=0)
        return True

    expected_previous_hash = GENESIS_HASH
    entry_count = 0

    with audit_path.open("r", encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            entry_count += 1

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                log.warning(
                    "audit.chain_verification_failed",
                    reason="malformed_json",
                    line_number=line_number,
                )
                return False

            stored_previous_hash = entry.get("previous_hash")
            stored_current_hash = entry.get("current_hash")

            # 1. Linkage check — this entry must point at the previous
            # entry's hash (or GENESIS_HASH if it's the first entry).
            if stored_previous_hash != expected_previous_hash:
                log.warning(
                    "audit.chain_verification_failed",
                    reason="broken_link",
                    line_number=line_number,
                    expected_previous_hash=expected_previous_hash,
                    found_previous_hash=stored_previous_hash,
                )
                return False

            # 2. Content check — recompute the hash over everything except
            # the stored current_hash, and confirm it still matches.
            entry_without_hash = {
                k: v for k, v in entry.items() if k != "current_hash"
            }
            recomputed_hash = compute_hash(entry_without_hash)
            if recomputed_hash != stored_current_hash:
                log.warning(
                    "audit.chain_verification_failed",
                    reason="content_mismatch",
                    line_number=line_number,
                )
                return False

            expected_previous_hash = stored_current_hash

    log.info("audit.chain_verified", entry_count=entry_count)
    return True
