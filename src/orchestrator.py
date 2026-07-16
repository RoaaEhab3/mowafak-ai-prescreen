"""Orchestration Pipeline.

Connects all AI modules in sequence:

  CV Parser → Question Generator → Whisper STT → Response Evaluator → Report Generator

Each public method is a reusable service callable from FastAPI or directly.
Implements retries, structured logging, and validation at every boundary.

Persistence uses supabase-py (PostgREST HTTP), not SQLAlchemy: every DB access
goes through `db.table(...).select()/.insert()/.update()/.upsert().execute()`
and reads `.data` (a list of dict rows). Row shapes come from src.models_db
dataclasses (`.to_row()` / `.from_row()`).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from supabase import Client

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
    TABLE_CANDIDATES,
    TABLE_SESSIONS,
    TABLE_QUESTIONS,
    TABLE_ANSWERS,
    TABLE_ASSESSMENTS,
)
from src.audit_log import write_audit_entry
from src.observability import log, new_trace
from src.settings import settings

T = TypeVar("T")

_evaluator: ResponseEvaluator | None = None
_stt: WhisperSTT | None = None


def _uuid() -> str:
    return str(uuid.uuid4())


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
    """Simple synchronous retry with exponential backoff.

    Retry warnings include the traceback (exc_info): this wrapper is the only
    thing standing between a failure inside whisper / google-generativeai and
    the caller, so if it logs just str(exc) the origin of the error is lost.
    """
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == retries:
                raise
            log.warning(
                "orchestrator.retry",
                attempt=attempt,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=exc,
            )
            time.sleep(delay * attempt)


def _parsed_cv_from_json(candidate_id: str, parsed_cv_json: dict) -> ParsedCV:
    """Rebuild a ParsedCV from stored JSON.

    parsed_cv_json is `ParsedCV.model_dump()`, which already contains a
    candidate_id key — pop it so we don't pass the argument twice.
    """
    data = dict(parsed_cv_json or {})
    data.pop("candidate_id", None)
    return ParsedCV(candidate_id=candidate_id, **data)


# ─────────────────────────────────────────────────────────────────────────────
# Service Methods
# ─────────────────────────────────────────────────────────────────────────────


def ingest_cv(
    db: Client,
    *,
    candidate_id: str,
    cv_file_path: str,
    consent_given: bool,
) -> tuple[Candidate, ParsedCV]:
    """
    Parse a CV and persist the candidate record.

    Returns (Candidate row-shape, ParsedCV Pydantic).
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

    candidate = Candidate(
        id=candidate_id,
        consent_given=True,
        consent_timestamp=datetime.now(timezone.utc),
        cv_file_path=cv_file_path,
        parsed_cv_json=parsed.model_dump(),
        raw_skills=parsed.raw_skills,
        total_experience_years=parsed.total_experience_years,
        summary=parsed.summary,
    )
    # Upsert on primary key so re-uploading a CV for the same candidate_id
    # overwrites rather than erroring on a duplicate key.
    db.table(TABLE_CANDIDATES).upsert(candidate.to_row()).execute()

    elapsed = time.perf_counter() - t0
    log.info(
        "orchestrator.ingest_cv.done",
        candidate_id=candidate_id,
        elapsed_s=round(elapsed, 2),
        skills=len(parsed.raw_skills),
    )
    write_audit_entry(
        db,
        session_id=None,
        candidate_id=candidate_id,
        event_type="cv_ingested",
        metadata={"skills_count": len(parsed.raw_skills), "trace_id": trace},
    )
    return candidate, parsed


def start_interview(
    db: Client,
    *,
    candidate_id: str,
    role: str | None = None,
    n_questions: int | None = None,
) -> tuple[InterviewSession, list[Question]]:
    """
    Create an InterviewSession, generate questions, persist to DB.

    Returns (InterviewSession row-shape, list of persisted Question row-shapes).
    """
    trace = new_trace(candidate_id=candidate_id)
    log.info("orchestrator.start_interview", candidate_id=candidate_id)

    res = (
        db.table(TABLE_CANDIDATES)
        .select("*")
        .eq("id", candidate_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise ValueError(f"Candidate {candidate_id!r} not found.")
    candidate = Candidate.from_row(res.data[0])

    if not candidate.consent_given:
        raise ValueError("Candidate has not given consent.")
    if not candidate.parsed_cv_json:
        raise ValueError("CV has not been parsed yet. Call /upload_cv first.")

    parsed_cv = _parsed_cv_from_json(candidate_id, candidate.parsed_cv_json)

    gen_questions: list[InterviewQuestion] = _retry(
        lambda: generate_questions(parsed_cv, n_questions=n_questions), retries=3
    )

    session = InterviewSession(
        id=_uuid(),
        candidate_id=candidate_id,
        role=role,
        status="in_progress",
    )
    db.table(TABLE_SESSIONS).insert(session.to_row()).execute()

    db_questions: list[Question] = [
        Question(
            id=_uuid(),
            session_id=session.id,
            question_index=idx,
            question_text=q.question,
            skill_targeted=q.skill_targeted,
            question_type=q.question_type,
        )
        for idx, q in enumerate(gen_questions)
    ]
    if db_questions:
        db.table(TABLE_QUESTIONS).insert(
            [q.to_row() for q in db_questions]
        ).execute()

    write_audit_entry(
        db,
        session_id=session.id,
        candidate_id=candidate_id,
        event_type="interview_started",
        metadata={"question_count": len(db_questions), "trace_id": trace},
    )

    log.info(
        "orchestrator.start_interview.done",
        session_id=session.id,
        questions=len(db_questions),
    )
    return session, db_questions


def process_answer(
    db: Client,
    *,
    session_id: str,
    question_id: str,
    audio_file_path: str,
) -> tuple[Answer, Assessment]:
    """
    Transcribe audio via Whisper, evaluate response, persist both.

    Returns (Answer row-shape, Assessment row-shape).
    """
    trace = new_trace()
    log.info(
        "orchestrator.process_answer.start",
        session_id=session_id,
        question_id=question_id,
    )

    qres = (
        db.table(TABLE_QUESTIONS)
        .select("*")
        .eq("id", question_id)
        .limit(1)
        .execute()
    )
    if not qres.data:
        raise ValueError(f"Question {question_id!r} not found.")
    question = Question.from_row(qres.data[0])
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

    # 2. Evaluate — pull the skills matrix from the candidate's parsed CV
    sres = (
        db.table(TABLE_SESSIONS)
        .select("*")
        .eq("id", session_id)
        .limit(1)
        .execute()
    )
    if not sres.data:
        raise ValueError(f"InterviewSession {session_id!r} not found.")
    session = InterviewSession.from_row(sres.data[0])

    cres = (
        db.table(TABLE_CANDIDATES)
        .select("*")
        .eq("id", session.candidate_id)
        .limit(1)
        .execute()
    )
    parsed_cv_json = Candidate.from_row(cres.data[0]).parsed_cv_json if cres.data else {}
    skills_matrix = (parsed_cv_json or {}).get("skills_matrix", {})

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
        id=_uuid(),
        session_id=session_id,
        question_id=question_id,
        audio_file_path=audio_file_path,
        transcript=transcript,
        language_detected=language,
    )
    db.table(TABLE_ANSWERS).insert(answer.to_row()).execute()

    # 4. Persist Assessment
    assessment = Assessment(
        id=_uuid(),
        answer_id=answer.id,
        relevance_score=assessment_result.relevance_score,
        clarity_score=assessment_result.clarity_score,
        technical_depth_score=assessment_result.technical_depth_score,
        average_score=assessment_result.average_score,
        evidence_from_transcript=assessment_result.evidence_from_transcript,
        concerns=assessment_result.concerns,
    )
    db.table(TABLE_ASSESSMENTS).insert(assessment.to_row()).execute()

    log.info(
        "orchestrator.process_answer.done",
        session_id=session_id,
        avg_score=assessment.average_score,
        concerns=len(assessment.concerns),
    )
    return answer, assessment


def finalize_session(db: Client, *, session_id: str):
    """
    Generate the FinalReport once all answers are submitted.
    Advances session status to awaiting_hr.
    """
    log.info("orchestrator.finalize_session", session_id=session_id)
    return generate_report(db, session_id)
