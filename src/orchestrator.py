"""Orchestration Pipeline.

Connects all AI modules in sequence:

  CV Parser → Question Generator → Whisper STT → Response Evaluator → Report Generator

Each public method is a reusable service callable from FastAPI or directly.
Implements retries, structured logging, and validation at every boundary.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, TypeVar

from sqlalchemy.orm import Session

from src.cv_parser import parse_cv, ParsedCV
from src.agents.question_generator import generate_questions, InterviewQuestion
from src.agents.response_evaluator import ResponseEvaluator
from src.whisper_stt import WhisperSTT
from src.report_generator import generate_report
from src.models_db import (
    Candidate,
    InterviewSession,
    Question,
    Answer,
    Assessment,
)
from src.audit_log import write_audit_entry
from src.observability import log, new_trace
from src.settings import settings

T = TypeVar("T")

_evaluator: ResponseEvaluator | None = None
_stt: WhisperSTT | None = None


def _get_evaluator() -> ResponseEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = ResponseEvaluator()
    return _evaluator


def _get_stt() -> WhisperSTT:
    global _stt
    if _stt is None:
        _stt = WhisperSTT(settings.whisper_model)
    return _stt


def _retry(fn: Callable, retries: int = 3, delay: float = 1.0):
    """Simple synchronous retry with exponential backoff."""
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == retries:
                raise
            log.warning("orchestrator.retry", attempt=attempt, error=str(exc))
            time.sleep(delay * attempt)


# ─────────────────────────────────────────────────────────────────────────────
# Service Methods
# ─────────────────────────────────────────────────────────────────────────────


def ingest_cv(
    db: Session,
    *,
    candidate_id: str,
    cv_file_path: str,
    consent_given: bool,
) -> tuple[Candidate, ParsedCV]:
    """
    Parse a CV and persist the candidate record.

    Returns (Candidate ORM, ParsedCV Pydantic).
    Raises ValueError if consent is not given.
    """
    if not consent_given:
        raise ValueError("Candidate consent is required before processing CV.")

    trace = new_trace(candidate_id=candidate_id)
    t0 = time.perf_counter()
    log.info("orchestrator.ingest_cv.start", candidate_id=candidate_id)

    parsed: ParsedCV = _retry(
        lambda: parse_cv(Path(cv_file_path), candidate_id), retries=3
    )

    # Upsert candidate
    candidate = db.query(Candidate).filter_by(id=candidate_id).first()
    if candidate is None:
        candidate = Candidate(id=candidate_id)
        db.add(candidate)

    candidate.cv_file_path = cv_file_path
    candidate.parsed_cv_json = parsed.model_dump()
    candidate.consent_given = True
    candidate.consent_timestamp = datetime.now(timezone.utc)
    db.commit()
    db.refresh(candidate)

    elapsed = time.perf_counter() - t0
    log.info(
        "orchestrator.ingest_cv.done",
        candidate_id=candidate_id,
        elapsed_s=round(elapsed, 2),
        skills=len(parsed.raw_skills),
    )
    write_audit_entry(
        db,
        session_id="N/A",
        candidate_id=candidate_id,
        event_type="cv_ingested",
        metadata={"skills_count": len(parsed.raw_skills), "trace_id": trace},
    )
    return candidate, parsed


def start_interview(
    db: Session,
    *,
    candidate_id: str,
    role: str | None = None,
    n_questions: int | None = None,
) -> tuple[InterviewSession, list[InterviewQuestion]]:
    """
    Create an InterviewSession, generate questions, persist to DB.

    Returns (InterviewSession ORM, list of InterviewQuestion Pydantic).
    """
    trace = new_trace(candidate_id=candidate_id)
    log.info("orchestrator.start_interview", candidate_id=candidate_id)

    candidate = db.query(Candidate).filter_by(id=candidate_id).first()
    if candidate is None:
        raise ValueError(f"Candidate {candidate_id!r} not found.")
    if not candidate.consent_given:
        raise ValueError("Candidate has not given consent.")
    if candidate.parsed_cv_json is None:
        raise ValueError("CV has not been parsed yet. Call /upload_cv first.")

    parsed_cv = ParsedCV(candidate_id=candidate_id, **candidate.parsed_cv_json)

    questions: list[InterviewQuestion] = _retry(
        lambda: generate_questions(parsed_cv, n_questions=n_questions), retries=3
    )

    session = InterviewSession(
        id=str(uuid.uuid4()),
        candidate_id=candidate_id,
        role=role,
        status="in_progress",
    )
    db.add(session)
    db.flush()  # get session.id before creating questions

    for idx, q in enumerate(questions):
        orm_q = Question(
            id=str(uuid.uuid4()),
            session_id=session.id,
            question_index=idx,
            question_text=q.question,
            skill_targeted=q.skill_targeted,
            question_type=q.question_type,
        )
        db.add(orm_q)

    db.commit()
    db.refresh(session)

    write_audit_entry(
        db,
        session_id=session.id,
        candidate_id=candidate_id,
        event_type="interview_started",
        metadata={"question_count": len(questions), "trace_id": trace},
    )

    log.info(
        "orchestrator.start_interview.done",
        session_id=session.id,
        questions=len(questions),
    )
    return session, questions


def process_answer(
    db: Session,
    *,
    session_id: str,
    question_id: str,
    audio_file_path: str,
) -> tuple[Answer, Assessment]:
    """
    Transcribe audio via Whisper, evaluate response, persist both.

    Returns (Answer ORM, Assessment ORM).
    """
    trace = new_trace()
    log.info(
        "orchestrator.process_answer.start",
        session_id=session_id,
        question_id=question_id,
    )

    question = db.query(Question).filter_by(id=question_id).first()
    if question is None:
        raise ValueError(f"Question {question_id!r} not found.")
    if question.session_id != session_id:
        raise ValueError("Question does not belong to the given session.")

    # 1. Transcribe
    t0 = time.perf_counter()
    stt_result = _retry(
        lambda: _get_stt().transcribe_audio(audio_file_path), retries=2
    )
    transcript = stt_result["transcript"]
    language = stt_result["language"]
    log.info(
        "orchestrator.transcribe_done",
        chars=len(transcript),
        language=language,
        elapsed_s=round(time.perf_counter() - t0, 2),
    )

    # 2. Evaluate
    session_orm = db.query(InterviewSession).filter_by(id=session_id).first()
    candidate = session_orm.candidate
    skills_matrix = (candidate.parsed_cv_json or {}).get("skills_matrix", {})

    assessment_result = _retry(
        lambda: _get_evaluator().evaluate(
            question=question.question_text,
            transcript=transcript,
            skill_targeted=question.skill_targeted,
            skills_matrix=skills_matrix,
        ),
        retries=2,
    )

    # 3. Persist Answer
    answer = Answer(
        id=str(uuid.uuid4()),
        question_id=question_id,
        audio_file_path=audio_file_path,
        transcript=transcript,
        language_detected=language,
    )
    db.add(answer)
    db.flush()

    # 4. Persist Assessment
    orm_assessment = Assessment(
        id=str(uuid.uuid4()),
        answer_id=answer.id,
        relevance_score=assessment_result.relevance_score,
        clarity_score=assessment_result.clarity_score,
        technical_depth_score=assessment_result.technical_depth_score,
        average_score=assessment_result.average_score,
        evidence_from_transcript=assessment_result.evidence_from_transcript,
        concerns=assessment_result.concerns,
    )
    db.add(orm_assessment)
    db.commit()
    db.refresh(answer)
    db.refresh(orm_assessment)

    log.info(
        "orchestrator.process_answer.done",
        session_id=session_id,
        avg_score=orm_assessment.average_score,
        concerns=len(orm_assessment.concerns),
    )
    return answer, orm_assessment


def finalize_session(db: Session, *, session_id: str):
    """
    Generate the FinalReport once all answers are submitted.
    Advances session status to awaiting_hr.
    """
    log.info("orchestrator.finalize_session", session_id=session_id)
    return generate_report(db, session_id)
