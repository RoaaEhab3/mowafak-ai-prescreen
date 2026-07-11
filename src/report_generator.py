"""Report Generator.
 
Aggregates all per-question ResponseAssessments into a FinalReport using Gemini.
 
Recommendation scale:
  strong_yes  | overall >= 4.0
  weak_yes    | overall >= 3.0
  weak_no     | overall >= 2.0
  strong_no   | overall <  2.0
 
Every recommendation must include transcript evidence (enforced by prompt).
 
Persistence uses supabase-py (PostgREST). The SQLAlchemy relationship
traversal (session.questions / question.answers / answer.assessment) is
replaced with explicit table queries joined in Python by id.
"""
from __future__ import annotations
 
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
 
import google.generativeai as genai
from pydantic import BaseModel, ValidationError
from supabase import Client
 
from src.settings import settings
from src.prompts import REPORT_DRAFTER_SYSTEM, REPORT_DRAFTER_USER
from src.models_db import (
    Candidate,
    InterviewSession,
    Question,
    Answer,
    Assessment,
    FinalReport,
    TABLE_CANDIDATES,
    TABLE_SESSIONS,
    TABLE_QUESTIONS,
    TABLE_ANSWERS,
    TABLE_ASSESSMENTS,
    TABLE_FINAL_REPORTS,
)
from src.audit_log import write_audit_entry
from src.observability import log
 
genai.configure(api_key=settings.gemini_api_key)
 
VALID_RECOMMENDATIONS = {"strong_yes", "weak_yes", "weak_no", "strong_no"}
 
PROMPT_VERSION = "v1"
 
GEMINI_MAX_RETRIES = 3
 
 
class ReportOutput(BaseModel):
    """Schema used to validate the Gemini REPORT_DRAFTER JSON response."""
 
    overall_score: float
    recommendation: Optional[str] = None
    per_skill_ratings: dict[str, Any] = {}
    strengths: list[Any] = []
    areas_for_development: list[Any] = []
    written_summary: str = ""
 
 
def _uuid() -> str:
    return str(uuid.uuid4())
 
 
def _recommendation_from_score(score: float) -> str:
    if score >= 4.0:
        return "strong_yes"
    if score >= 3.0:
        return "weak_yes"
    if score >= 2.0:
        return "weak_no"
    return "strong_no"
 
 
def _persist_report(db: Client, report: FinalReport, session_id: str) -> FinalReport:
    """Insert the report and advance the session to awaiting_hr."""
    db.table(TABLE_FINAL_REPORTS).insert(report.to_row()).execute()
    db.table(TABLE_SESSIONS).update(
        {
            "status": "awaiting_hr",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", session_id).execute()
    return report
 
 
def _generate_content_with_retry(model, prompt: str, session_id: str):
    """Call Gemini's generate_content with retry logic.
 
    Retries up to GEMINI_MAX_RETRIES times, logging each attempt.
    Raises RuntimeError if all retries fail.
    """
    last_exc: Exception | None = None
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            return model.generate_content(prompt)
        except Exception as exc:
            last_exc = exc
            log.warning(
                "report_gen.gemini_retry",
                session_id=session_id,
                attempt=attempt,
                max_retries=GEMINI_MAX_RETRIES,
                error=str(exc),
            )
            if attempt < GEMINI_MAX_RETRIES:
                time.sleep(min(2 ** (attempt - 1), 5))
    raise RuntimeError(
        f"Gemini generate_content failed after {GEMINI_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc
 
 
def generate_report(db: Client, session_id: str) -> FinalReport:
    """
    Build and persist a FinalReport for a completed InterviewSession.
 
    Steps:
      1. Load all Q&A + assessments for the session
      2. Call Gemini REPORT_DRAFTER to aggregate
      3. Persist FinalReport to DB
      4. Write audit log entry
      5. Advance session status to awaiting_hr
    """
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
    candidate = Candidate.from_row(cres.data[0]) if cres.data else None
 
    # Load questions + answers + assessments for the session and join in Python.
    q_rows = (
        db.table(TABLE_QUESTIONS).select("*").eq("session_id", session_id).execute()
    )
    questions = {r["id"]: Question.from_row(r) for r in (q_rows.data or [])}
 
    a_rows = (
        db.table(TABLE_ANSWERS).select("*").eq("session_id", session_id).execute()
    )
    answers = [Answer.from_row(r) for r in (a_rows.data or [])]
 
    answer_ids = [a.id for a in answers]
    assessments_by_answer: dict[str, Assessment] = {}
    if answer_ids:
        asmt_rows = (
            db.table(TABLE_ASSESSMENTS)
            .select("*")
            .in_("answer_id", answer_ids)
            .execute()
        )
        for r in (asmt_rows.data or []):
            a = Assessment.from_row(r)
            assessments_by_answer[a.answer_id] = a
 
    assessments_data = []
    for answer in answers:
        a = assessments_by_answer.get(answer.id)
        if a is None:
            log.warning(
                "report_gen.answer_missing_assessment",
                session_id=session_id,
                answer_id=answer.id,
            )
            continue
        question = questions.get(answer.question_id)
        assessments_data.append(
            {
                "question": question.question_text if question else "",
                "skill_targeted": question.skill_targeted if question else None,
                "transcript_snippet": (answer.transcript or "")[:500],
                "relevance_score": a.relevance_score,
                "clarity_score": a.clarity_score,
                "technical_depth_score": a.technical_depth_score,
                "average_score": a.average_score,
                "evidence_from_transcript": a.evidence_from_transcript,
                "concerns": a.concerns,
            }
        )
 
    if not assessments_data:
        log.warning("report_gen.no_assessments", session_id=session_id)
        # Return a minimal report so HR still needs to decide
        report = FinalReport(
            id=_uuid(),
            session_id=session_id,
            overall_score=1.0,
            ai_recommendation="weak_no",
            written_summary="No assessments available — manual HR review required.",
            per_skill_ratings={},
            strengths=[],
            areas_for_development=["No voice answers received; cannot evaluate."],
        )
        return _persist_report(db, report, session_id)
 
    # Compute the raw average once; reused both for logging context and as the
    # fallback score/recommendation if Gemini output can't be used.
    avg_score = sum(a["average_score"] for a in assessments_data) / len(assessments_data)
 
    # Build skills matrix from parsed CV
    skills_matrix = (
        (candidate.parsed_cv_json if candidate else {}) or {}
    ).get("skills_matrix", {})
    candidate_id = candidate.id if candidate else session.candidate_id
    role = session.role or "Unspecified Role"
 
    prompt = REPORT_DRAFTER_USER.format(
        candidate_id=candidate_id,
        role=role,
        assessments_json=json.dumps(assessments_data, indent=2),
        skills_matrix=json.dumps(skills_matrix, indent=2),
    )
 
    model = genai.GenerativeModel(
        settings.gemini_model,
        system_instruction=REPORT_DRAFTER_SYSTEM,
    )
 
    log.info(
        "report_gen.sending_prompt",
        session_id=session_id,
        assessment_count=len(assessments_data),
    )
    response = _generate_content_with_retry(model, prompt, session_id)
    text = re.sub(r"^```[a-z]*\n?", "", response.text.strip())
    text = re.sub(r"\n?```$", "", text)
 
    try:
        raw_data = json.loads(text)
        parsed = ReportOutput.model_validate(raw_data)
 
        overall_score = float(parsed.overall_score)
        ai_recommendation = parsed.recommendation
        if ai_recommendation not in VALID_RECOMMENDATIONS:
            ai_recommendation = _recommendation_from_score(overall_score)
 
        report = FinalReport(
            id=_uuid(),
            session_id=session_id,
            overall_score=overall_score,
            ai_recommendation=ai_recommendation,
            per_skill_ratings=parsed.per_skill_ratings,
            strengths=parsed.strengths,
            areas_for_development=parsed.areas_for_development,
            written_summary=parsed.written_summary,
        )
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        log.error("report_gen.parse_failed", session_id=session_id, error=str(exc))
        report = FinalReport(
            id=_uuid(),
            session_id=session_id,
            overall_score=round(avg_score, 2),
            ai_recommendation=_recommendation_from_score(avg_score),
            written_summary="Report generation encountered an error; scores computed from raw averages.",
            per_skill_ratings={},
            strengths=[],
            areas_for_development=["Automated report failed — manual review required."],
        )
 
    _persist_report(db, report, session_id)
 
    write_audit_entry(
        db,
        session_id=session_id,
        candidate_id=candidate_id,
        event_type="report_generated",
        ai_recommendation=report.ai_recommendation,
        metadata={
            "overall_score": report.overall_score,
            "question_count": len(assessments_data),
            "prompt_version": PROMPT_VERSION,
        },
    )
 
    log.info(
        "report_gen.success",
        session_id=session_id,
        overall_score=report.overall_score,
        ai_recommendation=report.ai_recommendation,
    )
    return report
