"""Row-shape definitions for Supabase tables (canonical).

These are plain dataclasses, NOT an ORM. supabase-py sends/receives plain
JSON dicts over PostgREST, so there is no declarative mapping layer the way
there was with SQLAlchemy. These classes give the rest of the backend
(main.py, orchestrator.py, hil_gate.py, audit_log.py, report_generator.py)
a typed shape to construct and read, and give the whole codebase one place
that defines table and column names.

This module restores full parity with the original SQLAlchemy model that the
first Supabase PR dropped:
  - Candidate keeps `parsed_cv_json`, `cv_file_path`, `consent_timestamp`
    (orchestrator/report_generator depend on parsed_cv_json for the skills
    matrix).
  - The `assessments` and `audit_logs` tables/dataclasses are restored.

Actual schema creation happens in Postgres via Supabase migrations/SQL
editor — see `backend/schema.sql`. This module must be kept in sync with
that SQL by hand.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Table name constants — single source of truth ────────────────────────────

TABLE_CANDIDATES = "candidates"
TABLE_SESSIONS = "interview_sessions"
TABLE_QUESTIONS = "questions"
TABLE_ANSWERS = "answers"
TABLE_ASSESSMENTS = "assessments"
TABLE_FINAL_REPORTS = "final_reports"
TABLE_AUDIT_LOGS = "audit_logs"


# ── Row shapes ────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    id: str
    consent_given: bool = False
    consent_timestamp: Any | None = None          # ISO string or datetime
    cv_file_path: str | None = None
    parsed_cv_json: dict[str, Any] = field(default_factory=dict)
    raw_skills: list[str] = field(default_factory=list)
    total_experience_years: float = 0.0
    summary: str = ""
    created_at: Any | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Candidate":
        return cls(
            id=row["id"],
            consent_given=row.get("consent_given", False),
            consent_timestamp=row.get("consent_timestamp"),
            cv_file_path=row.get("cv_file_path"),
            parsed_cv_json=row.get("parsed_cv_json") or {},
            raw_skills=row.get("raw_skills") or [],
            total_experience_years=row.get("total_experience_years", 0.0),
            summary=row.get("summary") or "",
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        ts = self.consent_timestamp
        if isinstance(ts, datetime):
            ts = ts.isoformat()
        return {
            "id": self.id,
            "consent_given": self.consent_given,
            "consent_timestamp": ts,
            "cv_file_path": self.cv_file_path,
            "parsed_cv_json": self.parsed_cv_json,
            "raw_skills": self.raw_skills,
            "total_experience_years": self.total_experience_years,
            "summary": self.summary,
        }


@dataclass
class InterviewSession:
    id: str
    candidate_id: str
    role: str | None = None
    status: str = "in_progress"
    completed_at: Any | None = None
    created_at: Any | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "InterviewSession":
        return cls(
            id=row["id"],
            candidate_id=row["candidate_id"],
            role=row.get("role"),
            status=row.get("status", "in_progress"),
            completed_at=row.get("completed_at"),
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        completed = self.completed_at
        if isinstance(completed, datetime):
            completed = completed.isoformat()
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "role": self.role,
            "status": self.status,
            "completed_at": completed,
        }


@dataclass
class Question:
    id: str
    session_id: str
    question_index: int
    question_text: str
    skill_targeted: str | None = None
    question_type: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Question":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            question_index=row["question_index"],
            question_text=row["question_text"],
            skill_targeted=row.get("skill_targeted"),
            question_type=row.get("question_type"),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "question_index": self.question_index,
            "question_text": self.question_text,
            "skill_targeted": self.skill_targeted,
            "question_type": self.question_type,
        }


@dataclass
class Answer:
    id: str
    session_id: str
    question_id: str
    transcript: str | None = None
    language_detected: str | None = None
    audio_file_path: str | None = None
    created_at: Any | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Answer":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            question_id=row["question_id"],
            transcript=row.get("transcript"),
            language_detected=row.get("language_detected"),
            audio_file_path=row.get("audio_file_path"),
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "question_id": self.question_id,
            "transcript": self.transcript,
            "language_detected": self.language_detected,
            "audio_file_path": self.audio_file_path,
        }


@dataclass
class Assessment:
    id: str
    answer_id: str
    relevance_score: int = 1
    clarity_score: int = 1
    technical_depth_score: int = 1
    average_score: float = 1.0
    evidence_from_transcript: str = ""
    concerns: list[str] = field(default_factory=list)
    created_at: Any | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Assessment":
        return cls(
            id=row["id"],
            answer_id=row["answer_id"],
            relevance_score=row.get("relevance_score", 1),
            clarity_score=row.get("clarity_score", 1),
            technical_depth_score=row.get("technical_depth_score", 1),
            average_score=row.get("average_score", 1.0),
            evidence_from_transcript=row.get("evidence_from_transcript") or "",
            concerns=row.get("concerns") or [],
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "answer_id": self.answer_id,
            "relevance_score": self.relevance_score,
            "clarity_score": self.clarity_score,
            "technical_depth_score": self.technical_depth_score,
            "average_score": self.average_score,
            "evidence_from_transcript": self.evidence_from_transcript,
            "concerns": self.concerns,
        }


@dataclass
class FinalReport:
    id: str
    session_id: str
    overall_score: float = 0.0
    ai_recommendation: str = ""
    written_summary: str = ""
    per_skill_ratings: dict[str, Any] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    areas_for_development: list[str] = field(default_factory=list)
    hr_decision: str | None = None
    hr_reviewer_id: str | None = None
    hr_notes: str | None = None
    hr_decided_at: Any | None = None
    created_at: Any | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "FinalReport":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            overall_score=row.get("overall_score", 0.0),
            ai_recommendation=row.get("ai_recommendation", ""),
            written_summary=row.get("written_summary", ""),
            per_skill_ratings=row.get("per_skill_ratings") or {},
            strengths=row.get("strengths") or [],
            areas_for_development=row.get("areas_for_development") or [],
            hr_decision=row.get("hr_decision"),
            hr_reviewer_id=row.get("hr_reviewer_id"),
            hr_notes=row.get("hr_notes"),
            hr_decided_at=row.get("hr_decided_at"),
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        decided = self.hr_decided_at
        if isinstance(decided, datetime):
            decided = decided.isoformat()
        return {
            "id": self.id,
            "session_id": self.session_id,
            "overall_score": self.overall_score,
            "ai_recommendation": self.ai_recommendation,
            "written_summary": self.written_summary,
            "per_skill_ratings": self.per_skill_ratings,
            "strengths": self.strengths,
            "areas_for_development": self.areas_for_development,
            "hr_decision": self.hr_decision,
            "hr_reviewer_id": self.hr_reviewer_id,
            "hr_notes": self.hr_notes,
            "hr_decided_at": decided,
        }


@dataclass
class AuditLog:
    id: str
    event_type: str
    candidate_id: str
    session_id: str | None = None
    ai_recommendation: str | None = None
    hr_decision: str | None = None
    hr_notes_hash: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: Any | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AuditLog":
        return cls(
            id=row["id"],
            event_type=row["event_type"],
            candidate_id=row["candidate_id"],
            session_id=row.get("session_id"),
            ai_recommendation=row.get("ai_recommendation"),
            hr_decision=row.get("hr_decision"),
            hr_notes_hash=row.get("hr_notes_hash"),
            metadata_json=row.get("metadata_json") or {},
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "candidate_id": self.candidate_id,
            "session_id": self.session_id,
            "ai_recommendation": self.ai_recommendation,
            "hr_decision": self.hr_decision,
            "hr_notes_hash": self.hr_notes_hash,
            "metadata_json": self.metadata_json,
        }
