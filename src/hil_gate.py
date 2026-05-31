"""Human-in-the-Loop Gate.

Enforces that NO candidate-facing decision is ever made automatically.
The architecture itself makes auto-rejection structurally impossible:

  - FinalReport.hr_decision starts as NULL
  - Candidates cannot receive a decision until HR explicitly acts
  - This module validates HR decisions before persisting them
  - Any attempt to bypass the gate raises HILViolationError
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models_db import FinalReport, InterviewSession
from src.audit_log import write_audit_entry
from src.observability import log


VALID_HR_DECISIONS = frozenset({"approved", "rejected", "hold"})


class HILViolationError(RuntimeError):
    """Raised when an operation would bypass the human-in-the-loop gate."""


def require_hr_decision(
    db: Session,
    *,
    session_id: str,
    hr_decision: str,
    hr_reviewer_id: str,
    hr_notes: str,
) -> FinalReport:
    """
    Record an explicit HR decision on a completed interview session.

    Rules:
      - hr_decision must be one of: approved | rejected | hold
      - A FinalReport must exist (AI must have completed its work first)
      - HR decision cannot be overwritten once set (append-only)
      - Every decision is audit-logged immediately

    Returns the updated FinalReport.
    """
    if hr_decision not in VALID_HR_DECISIONS:
        raise ValueError(
            f"Invalid hr_decision '{hr_decision}'. "
            f"Must be one of: {sorted(VALID_HR_DECISIONS)}"
        )

    if not hr_reviewer_id or not hr_reviewer_id.strip():
        raise HILViolationError("hr_reviewer_id is required — anonymous decisions are not allowed.")

    if not hr_notes or not hr_notes.strip():
        raise HILViolationError("hr_notes are required — undocumented decisions are not allowed.")

    session: InterviewSession | None = (
        db.query(InterviewSession).filter_by(id=session_id).first()
    )
    if session is None:
        raise ValueError(f"InterviewSession {session_id!r} not found.")

    report: FinalReport | None = (
        db.query(FinalReport).filter_by(session_id=session_id).first()
    )
    if report is None:
        raise HILViolationError(
            f"No FinalReport exists for session {session_id!r}. "
            "AI must complete its evaluation before HR can decide."
        )

    if report.hr_decision is not None:
        raise HILViolationError(
            f"HR decision already recorded for session {session_id!r}. "
            "Decisions are immutable."
        )

    # Persist HR decision
    report.hr_decision = hr_decision
    report.hr_notes = hr_notes
    report.hr_reviewer_id = hr_reviewer_id
    report.hr_decided_at = datetime.now(timezone.utc)

    session.status = "decided"
    db.commit()
    db.refresh(report)

    # Audit log
    write_audit_entry(
        db,
        session_id=session_id,
        candidate_id=session.candidate_id,
        event_type="hr_decision",
        ai_recommendation=report.ai_recommendation,
        hr_decision=hr_decision,
        hr_notes=hr_notes,
        metadata={
            "reviewer_id": hr_reviewer_id,
            "ai_score": report.overall_score,
        },
    )

    log.info(
        "hil.decision_recorded",
        session_id=session_id,
        hr_decision=hr_decision,
        reviewer=hr_reviewer_id,
    )
    return report
