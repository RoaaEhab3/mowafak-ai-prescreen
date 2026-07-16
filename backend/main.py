"""Mowafak AI PreScreen — FastAPI Backend.

Endpoints:
  POST /upload_cv
  POST /start_interview
  POST /upload_answer/{session_id}/{question_id}
  POST /finalize/{session_id}
  GET  /get_report/{session_id}
  POST /hr_decision

CHANGELOG (review fixes applied — see accompanying review notes):
  - Routes with blocking I/O (file writes, sync SQLAlchemy calls) are now
    plain `def`, not `async def`, so FastAPI runs them in its threadpool
    instead of blocking the event loop.
  - Internal exception text is no longer returned to the client; it is
    logged server-side with a trace id, and the client gets a generic
    message + that id for support/debugging correlation.
  - Upload directories are resolved relative to this file, not the
    process's current working directory.
  - CORS origins are now read from an environment variable instead of "*".
  - Basic file-size limits and content sniffing added to uploads.
  - /health performs a real DB check.
  - A single trace id is created per request and threaded through all log
    calls for that request.

  Integration-completion pass (this changeset):
  - hr_decision is now a Literal["approved", "rejected", "hold"] instead of
    a bare `str`, so FastAPI/pydantic rejects invalid values at the API
    boundary (a 422) instead of forwarding an arbitrary string into
    hil_gate.require_hr_decision().
  - Added a session-state guard: /upload_answer and /finalize reject (409)
    once a session is closed — either "awaiting_hr" (set by
    report_generator._persist_report) or "decided" (set by
    hil_gate.require_hr_decision). Blocking "decided" matters most: it stops
    a new answer or a regenerated report from landing underneath an HR
    decision the audit log has already recorded. It is not a full state
    machine — see "Still NOT implemented" below.
  - Removed the unused `shutil` import.

  Deliberately NOT done: deleting uploaded CV/audio after processing. It was
  proposed (PII minimisation) but rejected for now on two grounds: (1) the
  brief's Requirement 2 explicitly says "Store the audio file + transcript in
  SQLite linked to the candidate session"; and (2) Candidate.cv_file_path and
  Answer.audio_file_path persist those paths, so deleting the files would
  leave dangling references to non-existent files. If we do want retention
  limits, it should be an explicit, documented policy (null the path columns
  + a retention rule in responsible_ai/RAI_Config.yaml), not a silent unlink.

  Still NOT implemented here (needs decisions from other files / infra,
  flagged with TODO):
  - Authentication/authorization. There is currently no verification that
    the caller of /hr_decision is actually the HR reviewer named in the
    request body. This MUST be added before production use.
  - Full session state-machine guards beyond the single "already
    finalized" check added above (e.g. rejecting /upload_answer before a
    session has questions, or after HR has already decided). The complete
    state vocabulary is owned by orchestrator.py / hil_gate.py, which are
    not available to this changeset — revisit once those files are
    reviewed.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import Client

# Bootstrap path so imports resolve from project root
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import get_db, create_all_tables
from src.models_db import (
    TABLE_SESSIONS,
    TABLE_QUESTIONS,
    TABLE_FINAL_REPORTS,
    InterviewSession,
    Question,
    FinalReport,
)
from src.orchestrator import ingest_cv, start_interview, process_answer, finalize_session
from src.hil_gate import require_hr_decision, HILViolationError
from src.observability import log, new_trace

# MIGRATION STATUS:
# The full DB layer now runs on supabase-py. orchestrator.py, hil_gate.py,
# audit_log.py, and report_generator.py have all been ported off SQLAlchemy to
# `db.table(...).select()/.insert()/.update()/.upsert()` calls, and the
# canonical row shapes live in src/models_db.py (dataclasses with
# from_row/to_row). The Supabase schema is in backend/schema.sql — apply it via
# the Supabase SQL editor or `supabase db push` before first run.
#
# Still open (separate work, not part of the DB migration):
#   - orchestrator.py imports the AI modules src.agents.question_generator,
#     src.agents.response_evaluator, and src.whisper_stt, which are not yet on
#     main — the server won't boot end-to-end until those land.
#   - No auth on /hr_decision: hr_reviewer_id is still self-reported (see the
#     TODO on that endpoint).

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Mowafak AI PreScreen",
    description="Responsible AI hiring pre-screening platform with mandatory human-in-the-loop.",
    version="1.0.0",
)

# CORS: read allowed origins from env instead of hardcoding "*". Falls back to
# "*" only if nothing is configured (dev convenience), but logs a warning so
# it isn't shipped to prod silently.
_cors_origins_env = os.environ.get("MOWAFAK_CORS_ORIGINS", "")
if _cors_origins_env.strip():
    ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
else:
    ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Resolve upload dirs relative to this file, not the process cwd.
BASE_DIR = Path(__file__).resolve().parent
UPLOADS_CV = BASE_DIR / "uploads" / "cvs"
UPLOADS_AUDIO = BASE_DIR / "uploads" / "audio"
UPLOADS_CV.mkdir(parents=True, exist_ok=True)
UPLOADS_AUDIO.mkdir(parents=True, exist_ok=True)

MAX_CV_BYTES = 10 * 1024 * 1024        # 10 MB
MAX_AUDIO_BYTES = 50 * 1024 * 1024     # 50 MB
ALLOWED_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".webm"}

# Session statuses that mean "this session is closed to further processing".
#   awaiting_hr : report_generator._persist_report() set it — the report exists
#                 and is queued for HR review.
#   decided     : hil_gate.require_hr_decision() set it — HR has already made
#                 the call. Mutating a decided session would let a new answer
#                 or a regenerated report land underneath a recorded HR
#                 decision, which the audit log would not reflect.
# Not a full state machine (it doesn't police the earlier pending ->
# in_progress transitions), but it closes both terminal states.
SESSION_STATUSES_CLOSED = frozenset({"awaiting_hr", "decided"})


@app.on_event("startup")
async def startup() -> None:
    create_all_tables()
    log.info("mowafak.startup", message="Tables ready")


# ── Request / Response schemas ────────────────────────────────────────────────

class UploadCVResponse(BaseModel):
    candidate_id: str
    skills_count: int
    experience_years: float
    summary: str
    message: str


class StartInterviewRequest(BaseModel):
    candidate_id: str
    role: str | None = None
    n_questions: int | None = None
    consent_confirmed: bool = False


class InterviewQuestionOut(BaseModel):
    id: str          # DB Question.id
    question_text: str
    skill_targeted: str
    question_type: str


class StartInterviewResponse(BaseModel):
    session_id: str
    questions: list[InterviewQuestionOut]


class AnswerUploadResponse(BaseModel):
    answer_id: str
    transcript: str
    language: str
    average_score: float
    concerns: list[str]


class ReportResponse(BaseModel):
    session_id: str
    candidate_id: str
    overall_score: float
    ai_recommendation: str
    written_summary: str
    per_skill_ratings: dict
    strengths: list[str]
    areas_for_development: list[str]
    hr_decision: str | None
    hr_decided_at: str | None
    status: str


class HRDecisionRequest(BaseModel):
    session_id: str
    # Tightened from `str` to a closed set of allowed values: FastAPI/pydantic
    # now rejects anything else with a 422 at the API boundary, instead of
    # letting an arbitrary string reach hil_gate.require_hr_decision().
    hr_decision: Literal["approved", "rejected", "hold"]
    hr_reviewer_id: str
    hr_notes: str


class HRDecisionResponse(BaseModel):
    session_id: str
    hr_decision: str
    hr_decided_at: str
    message: str


class ErrorResponse(BaseModel):
    detail: str
    trace_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fail(trace_id: str, event: str, exc: Exception, client_message: str, status_code: int = 500):
    """Log full exception server-side, raise a generic HTTPException for the client.

    Never puts raw exception text in the client-facing response — avoids
    leaking stack traces, file paths, or upstream (Gemini/Whisper) error
    payloads that may contain sensitive detail.
    """
    # exc_info=exc logs the FULL traceback server-side. Previously this recorded
    # only str(exc), so a TypeError raised deep inside whisper/google-generativeai
    # surfaced as a bare message with no file or line — undebuggable. The client
    # still receives only the generic message + trace id below.
    log.error(
        event,
        trace_id=trace_id,
        error=str(exc),
        error_type=type(exc).__name__,
        exc_info=exc,
    )
    raise HTTPException(
        status_code=status_code,
        detail=f"{client_message} (reference: {trace_id})",
    )


def _save_upload(file: UploadFile, dest: Path, max_bytes: int) -> int:
    """Stream an upload to disk with a hard size cap. Returns bytes written."""
    written = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds max allowed size of {max_bytes} bytes.",
                    )
                fh.write(chunk)
    finally:
        file.file.close()
    return written


def _session_status(db: Client, session_id: str) -> str | None:
    """Fetch just the `status` column for a session, or None if not found.

    Small helper used by the partial state-machine guard below. Kept
    intentionally minimal (one column, no full row hydration) since it's
    only used for a pre-flight check, not to construct an InterviewSession.
    """
    result = (
        db.table(TABLE_SESSIONS).select("status").eq("id", session_id).limit(1).execute()
    )
    if not result.data:
        return None
    return result.data[0].get("status")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/upload_cv", response_model=UploadCVResponse, status_code=status.HTTP_201_CREATED)
def upload_cv(
    file: UploadFile = File(...),
    consent: bool = Form(default=False),
    db: Client = Depends(get_db),
):
    """
    Upload a PDF CV.

    - Saves file to uploads/cvs/
    - Parses with Gemini
    - Stores parsed data in DB
    - Requires consent=true

    NOTE: this is a sync `def`, not `async def` — it does blocking file and
    DB I/O, so FastAPI runs it in a threadpool instead of the event loop.
    """
    trace_id = new_trace()

    if not consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Candidate consent is required (consent=true) before processing.",
        )

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only PDF files are accepted.",
        )

    candidate_id = str(uuid.uuid4())
    dest = UPLOADS_CV / f"{candidate_id}.pdf"

    _save_upload(file, dest, MAX_CV_BYTES)

    # Minimal content sniff: real PDFs start with "%PDF-". Extension alone
    # is trivially spoofable.
    with dest.open("rb") as fh:
        header = fh.read(5)
    if header != b"%PDF-":
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File does not appear to be a valid PDF.",
        )

    log.info("api.upload_cv.saved", trace_id=trace_id, candidate_id=candidate_id, path=str(dest))

    try:
        candidate, parsed = ingest_cv(
            db,
            candidate_id=candidate_id,
            cv_file_path=str(dest),
            consent_given=True,
        )
    except Exception as exc:
        _fail(trace_id, "api.upload_cv.failed", exc, "CV parsing failed.")

    return UploadCVResponse(
        candidate_id=candidate_id,
        skills_count=len(parsed.raw_skills),
        experience_years=parsed.total_experience_years,
        summary=parsed.summary,
        message="CV parsed and stored. Proceed to /start_interview.",
    )


@app.post(
    "/start_interview",
    response_model=StartInterviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_interview_endpoint(
    body: StartInterviewRequest,
    db: Client = Depends(get_db),
):
    """
    Generate personalised interview questions and open a session.
    """
    trace_id = new_trace()

    if not body.consent_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="consent_confirmed must be true.",
        )

    try:
        session, questions = start_interview(
            db,
            candidate_id=body.candidate_id,
            role=body.role,
            n_questions=body.n_questions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _fail(trace_id, "api.start_interview.failed", exc, "Question generation failed.")

    # Use the questions returned by the orchestrator directly instead of
    # re-querying the DB — start_interview() already returns the persisted
    # objects, and a redundant query here previously masked any mismatch
    # between what's returned and what's actually stored.
    if not questions:
        result = (
            db.table(TABLE_QUESTIONS)
            .select("*")
            .eq("session_id", session.id)
            .order("question_index")
            .execute()
        )
        questions = [Question.from_row(row) for row in (result.data or [])]

    return StartInterviewResponse(
        session_id=session.id,
        questions=[
            InterviewQuestionOut(
                id=q.id,
                question_text=q.question_text,
                skill_targeted=q.skill_targeted,
                question_type=q.question_type,
            )
            for q in questions
        ],
    )


@app.post(
    "/upload_answer/{session_id}/{question_id}",
    response_model=AnswerUploadResponse,
)
def upload_answer(
    session_id: str,
    question_id: str,
    file: UploadFile = File(...),
    db: Client = Depends(get_db),
):
    """
    Upload an audio answer for a specific question.

    - Saves audio to uploads/audio/
    - Transcribes with Whisper
    - Evaluates with Gemini
    - Returns transcript + scores
    """
    trace_id = new_trace()

    # Session-state guard: refuse new answers once the session is closed —
    # either finalized (report awaiting HR) or already decided by HR. Accepting
    # an answer after a decision would silently change the evidence under a
    # recorded HR decision.
    existing_status = _session_status(db, session_id)
    if existing_status in SESSION_STATUSES_CLOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Session is '{existing_status}'; no further answers can be uploaded."
            ),
        )

    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXT:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported audio format. Accepted: {sorted(ALLOWED_AUDIO_EXT)}",
        )

    audio_id = str(uuid.uuid4())
    dest = UPLOADS_AUDIO / f"{audio_id}{suffix}"

    _save_upload(file, dest, MAX_AUDIO_BYTES)

    try:
        answer, assessment = process_answer(
            db,
            session_id=session_id,
            question_id=question_id,
            audio_file_path=str(dest),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        _fail(trace_id, "api.upload_answer.missing_dependency", exc,
              "Answer processing failed due to a missing resource.")
    except Exception as exc:
        _fail(trace_id, "api.upload_answer.failed", exc, "Answer processing failed.")

    return AnswerUploadResponse(
        answer_id=answer.id,
        transcript=answer.transcript or "",
        language=answer.language_detected or "unknown",
        average_score=assessment.average_score,
        concerns=assessment.concerns,
    )


@app.post("/finalize/{session_id}", status_code=status.HTTP_200_OK)
def finalize_session_endpoint(session_id: str, db: Client = Depends(get_db)):
    """
    Trigger final report generation once all answers are uploaded.
    Advances session to awaiting_hr status.
    """
    trace_id = new_trace()

    # Same guard as /upload_answer: don't regenerate a report for a session
    # that's already finalized, and never overwrite one HR has decided on.
    existing_status = _session_status(db, session_id)
    if existing_status in SESSION_STATUSES_CLOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is '{existing_status}'; it cannot be finalized again.",
        )

    try:
        report = finalize_session(db, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _fail(trace_id, "api.finalize.failed", exc, "Report generation failed.")

    return {
        "session_id": session_id,
        "overall_score": report.overall_score,
        "ai_recommendation": report.ai_recommendation,
        "status": "awaiting_hr",
        "message": "Report generated. HR review required before any decision.",
    }


@app.get("/get_report/{session_id}", response_model=ReportResponse)
def get_report(session_id: str, db: Client = Depends(get_db)):
    """
    Return the final AI-generated report for HR review.
    NOTE: hr_decision will be null until HR acts via /hr_decision.
    """
    report_result = (
        db.table(TABLE_FINAL_REPORTS)
        .select("*")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    if not report_result.data:
        raise HTTPException(
            status_code=404,
            detail=f"No report found for session {session_id!r}. Call /finalize first.",
        )
    report = FinalReport.from_row(report_result.data[0])

    session_result = (
        db.table(TABLE_SESSIONS)
        .select("*")
        .eq("id", session_id)
        .limit(1)
        .execute()
    )
    session = (
        InterviewSession.from_row(session_result.data[0])
        if session_result.data
        else None
    )

    return ReportResponse(
        session_id=session_id,
        candidate_id=session.candidate_id if session else "unknown",
        overall_score=report.overall_score,
        ai_recommendation=report.ai_recommendation,
        written_summary=report.written_summary,
        per_skill_ratings=report.per_skill_ratings or {},
        strengths=report.strengths or [],
        areas_for_development=report.areas_for_development or [],
        hr_decision=report.hr_decision,
        hr_decided_at=(
            report.hr_decided_at.isoformat() if report.hr_decided_at else None
        ),
        status=session.status if session else "unknown",
    )


@app.post("/hr_decision", response_model=HRDecisionResponse)
def hr_decision_endpoint(
    body: HRDecisionRequest,
    db: Client = Depends(get_db),
    # TODO: add an auth dependency here, e.g.:
    #   current_user: AuthedUser = Depends(get_current_hr_user)
    # and verify current_user.id == body.hr_reviewer_id (or drop
    # hr_reviewer_id from the request body entirely and derive it from the
    # authenticated session). Without this, hr_reviewer_id is just a
    # self-reported string and the "mandatory human-in-the-loop" guarantee
    # is not actually enforced.
):
    """
    Record a mandatory HR decision.

    REQUIRED fields:
      - hr_decision: approved | rejected | hold
      - hr_reviewer_id: identity of the HR reviewer
      - hr_notes: documented reasoning (hashed in audit log)

    This is the ONLY way a decision reaches a candidate.
    Auto-rejection is architecturally impossible.
    """
    trace_id = new_trace()

    try:
        report = require_hr_decision(
            db,
            session_id=body.session_id,
            hr_decision=body.hr_decision,
            hr_reviewer_id=body.hr_reviewer_id,
            hr_notes=body.hr_notes,
        )
    except (ValueError, HILViolationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _fail(trace_id, "api.hr_decision.failed", exc, "Decision recording failed.")

    return HRDecisionResponse(
        session_id=body.session_id,
        hr_decision=report.hr_decision,
        hr_decided_at=report.hr_decided_at.isoformat(),
        message=(
            f"HR decision '{report.hr_decision}' recorded and audit-logged. "
            "This decision is now immutable."
        ),
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health(db: Client = Depends(get_db)):
    db_ok = True
    try:
        db.table(TABLE_SESSIONS).select("id").limit(1).execute()
    except Exception as exc:
        db_ok = False
        log.error("api.health.db_check_failed", error=str(exc))

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "unreachable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
