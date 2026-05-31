"""SQLAlchemy ORM models.

Tables:
  - Candidate
  - InterviewSession
  - Question
  - Answer
  - Assessment
  - FinalReport
  - AuditLog
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# Candidate
# ─────────────────────────────────────────────────────────────────────────────

class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Anonymised storage — NO real name/email stored by default
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cv_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # JSON blob from ParsedCV (no PII fields)
    parsed_cv_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sessions: Mapped[list["InterviewSession"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# InterviewSession
# ─────────────────────────────────────────────────────────────────────────────

class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidates.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    role: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # State machine: pending | in_progress | completed | awaiting_hr | decided
    status: Mapped[str] = mapped_column(String(50), default="pending")

    candidate: Mapped["Candidate"] = relationship(back_populates="sessions")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    final_report: Mapped["FinalReport | None"] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Question
# ─────────────────────────────────────────────────────────────────────────────

class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("interview_sessions.id"), nullable=False
    )
    question_index: Mapped[int] = mapped_column(Integer, default=0)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    skill_targeted: Mapped[str] = mapped_column(String(100), default="general")
    question_type: Mapped[str] = mapped_column(String(50), default="behavioural")

    session: Mapped["InterviewSession"] = relationship(back_populates="questions")
    answers: Mapped[list["Answer"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Answer
# ─────────────────────────────────────────────────────────────────────────────

class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("questions.id"), nullable=False
    )
    audio_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_detected: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    question: Mapped["Question"] = relationship(back_populates="answers")
    assessment: Mapped["Assessment | None"] = relationship(
        back_populates="answer", uselist=False, cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Assessment
# ─────────────────────────────────────────────────────────────────────────────

class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    answer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("answers.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    relevance_score: Mapped[int] = mapped_column(Integer, default=1)
    clarity_score: Mapped[int] = mapped_column(Integer, default=1)
    technical_depth_score: Mapped[int] = mapped_column(Integer, default=1)
    average_score: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_from_transcript: Mapped[str] = mapped_column(Text, default="")
    concerns: Mapped[list] = mapped_column(JSON, default=list)

    answer: Mapped["Answer"] = relationship(back_populates="assessment")


# ─────────────────────────────────────────────────────────────────────────────
# FinalReport
# ─────────────────────────────────────────────────────────────────────────────

class FinalReport(Base):
    __tablename__ = "final_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("interview_sessions.id"), nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    # strong_yes | weak_yes | weak_no | strong_no  (AI recommendation only — HR decides)
    ai_recommendation: Mapped[str] = mapped_column(String(20), default="weak_no")
    per_skill_ratings: Mapped[dict] = mapped_column(JSON, default=dict)
    strengths: Mapped[list] = mapped_column(JSON, default=list)
    areas_for_development: Mapped[list] = mapped_column(JSON, default=list)
    written_summary: Mapped[str] = mapped_column(Text, default="")

    # HR decision fields — populated only after HR acts
    hr_decision: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # approved | rejected | hold
    hr_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    hr_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hr_reviewer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    session: Mapped["InterviewSession"] = relationship(back_populates="final_report")


# ─────────────────────────────────────────────────────────────────────────────
# AuditLog
# ─────────────────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("interview_sessions.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ai_recommendation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hr_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hr_notes_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
