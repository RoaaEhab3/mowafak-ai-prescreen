"""Mowafak AI PreScreen — FastAPI Backend.

Endpoints:
  POST /upload_cv
  POST /start_interview
  POST /upload_answer/{session_id}/{question_id}
  POST /finalize/{session_id}
  GET  /get_report/{session_id}
  POST /hr_decision
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Bootstrap path so imports resolve from project root
import sys, os
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import get_db, create_all_tables
from src.models_db import Candidate, InterviewSession, Question, FinalReport
from src.orchestrator import ingest_cv, start_interview, process_answer, finalize_session
from src.hil_gate import require_hr_decision, HILViolationError
from src.observability import log, new_trace

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Mowafak AI PreScreen",
    description="Responsible AI hiring pre-screening platform with mandatory human-in-the-loop.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_CV = Path("uploads/cvs")
UPLOADS_AUDIO = Path("uploads/audio")
UPLOADS_CV.mkdir(parents=True, exist_ok=True)
UPLOADS_AUDIO.mkdir(parents=True, exist_ok=True)


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
    hr_decision: str          # approved | rejected | hold
    hr_reviewer_id: str
    hr_notes: str


class HRDecisionResponse(BaseModel):
    session_id: str
    hr_decision: str
    hr_decided_at: str
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/upload_cv", response_model=UploadCVResponse, status_code=status.HTTP_201_CREATED)
async def upload_cv(
    file: UploadFile = File(...),
    consent: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    """
    Upload a PDF CV.

    - Saves file to uploads/cvs/
    - Parses with Gemini
    - Stores parsed data in DB
    - Requires consent=true
    """
    trace = new_trace()

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

    try:
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
    finally:
        file.file.close()

    log.info("api.upload_cv.saved", candidate_id=candidate_id, path=str(dest))

    try:
        candidate, parsed = ingest_cv(
            db,
            candidate_id=candidate_id,
            cv_file_path=str(dest),
            consent_given=True,
        )
    except Exception as exc:
        log.error("api.upload_cv.failed", candidate_id=candidate_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"CV parsing failed: {exc}")

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
async def start_interview_endpoint(
    body: StartInterviewRequest,
    db: Session = Depends(get_db),
):
    """
    Generate personalised interview questions and open a session.
    """
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
        log.error("api.start_interview.failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Question generation failed: {exc}")

    # Map DB question IDs back to the generated questions (by order)
    db_questions = (
        db.query(Question)
        .filter_by(session_id=session.id)
        .order_by(Question.question_index)
        .all()
    )

    return StartInterviewResponse(
        session_id=session.id,
        questions=[
            InterviewQuestionOut(
                id=q.id,
                question_text=q.question_text,
                skill_targeted=q.skill_targeted,
                question_type=q.question_type,
            )
            for q in db_questions
        ],
    )


@app.post(
    "/upload_answer/{session_id}/{question_id}",
    response_model=AnswerUploadResponse,
)
async def upload_answer(
    session_id: str,
    question_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload a WAV answer for a specific question.

    - Saves audio to uploads/audio/
    - Transcribes with Whisper
    - Evaluates with Gemini
    - Returns transcript + scores
    """
    allowed_ext = {".wav", ".mp3", ".m4a", ".ogg", ".webm"}
    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix not in allowed_ext:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported audio format. Accepted: {sorted(allowed_ext)}",
        )

    audio_id = str(uuid.uuid4())
    dest = UPLOADS_AUDIO / f"{audio_id}{suffix}"

    try:
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
    finally:
        file.file.close()

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
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        log.error("api.upload_answer.failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Answer processing failed: {exc}")

    return AnswerUploadResponse(
        answer_id=answer.id,
        transcript=answer.transcript or "",
        language=answer.language_detected or "unknown",
        average_score=assessment.average_score,
        concerns=assessment.concerns,
    )


@app.post("/finalize/{session_id}", status_code=status.HTTP_200_OK)
async def finalize_session_endpoint(session_id: str, db: Session = Depends(get_db)):
    """
    Trigger final report generation once all answers are uploaded.
    Advances session to awaiting_hr status.
    """
    try:
        report = finalize_session(db, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.error("api.finalize.failed", session_id=session_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Report generation failed: {exc}")

    return {
        "session_id": session_id,
        "overall_score": report.overall_score,
        "ai_recommendation": report.ai_recommendation,
        "status": "awaiting_hr",
        "message": "Report generated. HR review required before any decision.",
    }


@app.get("/get_report/{session_id}", response_model=ReportResponse)
async def get_report(session_id: str, db: Session = Depends(get_db)):
    """
    Return the final AI-generated report for HR review.
    NOTE: hr_decision will be null until HR acts via /hr_decision.
    """
    report: FinalReport | None = (
        db.query(FinalReport).filter_by(session_id=session_id).first()
    )
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"No report found for session {session_id!r}. Call /finalize first.",
        )

    session = db.query(InterviewSession).filter_by(id=session_id).first()

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
async def hr_decision_endpoint(
    body: HRDecisionRequest,
    db: Session = Depends(get_db),
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
        log.error("api.hr_decision.failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Decision recording failed: {exc}")

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
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
