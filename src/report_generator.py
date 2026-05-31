"""Report Generator.

Aggregates all per-question ResponseAssessments into a FinalReport using Gemini.

Recommendation scale:
  strong_yes  | overall >= 4.0
  weak_yes    | overall >= 3.0
  weak_no     | overall >= 2.0
  strong_no   | overall <  2.0

Every recommendation must include transcript evidence (enforced by prompt).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import google.generativeai as genai
from sqlalchemy.orm import Session

from src.settings import settings
from src.prompts import REPORT_DRAFTER_SYSTEM, REPORT_DRAFTER_USER
from src.models_db import InterviewSession, Question, Answer, Assessment, FinalReport, Candidate
from src.audit_log import write_audit_entry
from src.observability import log

genai.configure(api_key=settings.gemini_api_key)


def _recommendation_from_score(score: float) -> str:
    if score >= 4.0:
        return "strong_yes"
    if score >= 3.0:
        return "weak_yes"
    if score >= 2.0:
        return "weak_no"
    return "strong_no"


def generate_report(db: Session, session_id: str) -> FinalReport:
    """
    Build and persist a FinalReport for a completed InterviewSession.

    Steps:
      1. Load all Q&A + assessments for the session
      2. Call Gemini REPORT_DRAFTER to aggregate
      3. Persist FinalReport to DB
      4. Write audit log entry
      5. Advance session status to awaiting_hr
    """
    session: InterviewSession | None = (
        db.query(InterviewSession).filter_by(id=session_id).first()
    )
    if session is None:
        raise ValueError(f"InterviewSession {session_id!r} not found.")

    candidate: Candidate = session.candidate

    # Collect all assessments
    assessments_data = []
    for question in session.questions:
        for answer in question.answers:
            if answer.assessment:
                a = answer.assessment
                assessments_data.append(
                    {
                        "question": question.question_text,
                        "skill_targeted": question.skill_targeted,
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
            session_id=session_id,
            overall_score=1.0,
            ai_recommendation="weak_no",
            written_summary="No assessments available — manual HR review required.",
            per_skill_ratings={},
            strengths=[],
            areas_for_development=["No voice answers received; cannot evaluate."],
        )
        db.add(report)
        session.status = "awaiting_hr"
        db.commit()
        db.refresh(report)
        return report

    # Build skills matrix from parsed CV
    skills_matrix = (candidate.parsed_cv_json or {}).get("skills_matrix", {})
    role = session.role or "Unspecified Role"

    prompt = REPORT_DRAFTER_USER.format(
        candidate_id=candidate.id,
        role=role,
        assessments_json=json.dumps(assessments_data, indent=2),
        skills_matrix=json.dumps(skills_matrix, indent=2),
    )

    model = genai.GenerativeModel(
        settings.gemini_model,
        system_instruction=REPORT_DRAFTER_SYSTEM,
    )
    response = model.generate_content(prompt)
    text = re.sub(r"^```[a-z]*\n?", "", response.text.strip())
    text = re.sub(r"\n?```$", "", text)

    try:
        data = json.loads(text)
        overall_score = float(data.get("overall_score", 1.0))
        ai_recommendation = data.get(
            "recommendation", _recommendation_from_score(overall_score)
        )

        report = FinalReport(
            session_id=session_id,
            overall_score=overall_score,
            ai_recommendation=ai_recommendation,
            per_skill_ratings=data.get("per_skill_ratings", {}),
            strengths=data.get("strengths", []),
            areas_for_development=data.get("areas_for_development", []),
            written_summary=data.get("written_summary", ""),
        )
    except Exception as exc:
        log.error("report_gen.parse_failed", session_id=session_id, error=str(exc))
        avg = (
            sum(a["average_score"] for a in assessments_data) / len(assessments_data)
            if assessments_data
            else 1.0
        )
        report = FinalReport(
            session_id=session_id,
            overall_score=round(avg, 2),
            ai_recommendation=_recommendation_from_score(avg),
            written_summary="Report generation encountered an error; scores computed from raw averages.",
            per_skill_ratings={},
            strengths=[],
            areas_for_development=["Automated report failed — manual review required."],
        )

    db.add(report)
    session.status = "awaiting_hr"
    session.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(report)

    write_audit_entry(
        db,
        session_id=session_id,
        candidate_id=candidate.id,
        event_type="report_generated",
        ai_recommendation=report.ai_recommendation,
        metadata={
            "overall_score": report.overall_score,
            "question_count": len(assessments_data),
        },
    )

    log.info(
        "report_gen.success",
        session_id=session_id,
        overall_score=report.overall_score,
        ai_recommendation=report.ai_recommendation,
    )
    return report
