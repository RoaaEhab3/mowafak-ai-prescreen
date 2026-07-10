"""Row-shape definitions for Supabase tables.

These are plain dataclasses, NOT an ORM. supabase-py sends/receives plain
JSON dicts over PostgREST, so there is no declarative mapping layer the way
there was with SQLAlchemy. These classes exist to give the rest of the
backend (main.py, orchestrator.py, hil_gate.py) a typed shape to construct
and read, and to give the whole codebase one place that defines table and
column names.

Actual schema creation happens in Postgres via Supabase migrations/SQL
editor — see schema.sql alongside this file. This module must be kept in
sync with that SQL by hand; there is no migration-generation tooling doing
that for you here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ── Table name constants — single source of truth for table names used
#    throughout main.py / orchestrator.py / hil_gate.py ──────────────────────

TABLE_CANDIDATES = "candidates"
TABLE_SESSIONS = "interview_sessions"
TABLE_QUESTIONS = "questions"
TABLE_ANSWERS = "answers"
TABLE_FINAL_REPORTS = "final_reports"


# ── Row shapes ────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    id: str
    consent_given: bool = False
    raw_skills: list[str] = field(default_factory=list)
    total_experience_years: float = 0.0
    summary: str = ""
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Candidate":
        return cls(
            id=row["id"],
            consent_given=row.get("consent_given", False),
            raw_skills=row.get("raw_skills") or [],
            total_experience_years=row.get("total_experience_years", 0.0),
            summary=row.get("summary") or "",
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "consent_given": self.consent_given,
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
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "InterviewSession":
        return cls(
            id=row["id"],
            candidate_id=row["candidate_id"],
            role=row.get("role"),
            status=row.get("status", "in_progress"),
            created_at=row.get("created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "role": self.role,
            "status": self.status,
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
    created_at: datetime | None = None

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
    hr_decided_at: datetime | None = None
    created_at: datetime | None = None

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
            "hr_decided_at": self.hr_decided_at.isoformat() if self.hr_decided_at else None,
        }
