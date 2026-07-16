"""Response Evaluator Agent.

Takes a candidate's transcript answer + the original question + the skills
matrix and produces a structured ResponseAssessment via Gemini.

Ported from the original `agents/response_evaluator.py` with three fixes:
  1. Removed the hardcoded Gemini API key (SECURITY) — the key now comes from
     settings/env. The old committed key MUST be revoked.
  2. Rewritten on `google-generativeai` (matches cv_parser / question_generator /
     report_generator) instead of `langchain_google_genai`.
  3. Signature aligned with the orchestrator: evaluate(question, transcript,
     skill_targeted, skills_matrix), and the assessment now exposes
     `average_score` (the orchestrator persists it).
"""
from __future__ import annotations

import json
import re
from typing import Any

import google.generativeai as genai
from pydantic import BaseModel, Field, computed_field

from src.settings import settings
from src.prompts import RESPONSE_EVALUATOR_SYSTEM, RESPONSE_EVALUATOR_USER
from src.observability import log

genai.configure(api_key=settings.gemini_api_key)


class ResponseAssessment(BaseModel):
    """Structured assessment of a single transcript answer."""

    relevance_score: int = Field(ge=1, le=5)
    clarity_score: int = Field(ge=1, le=5)
    technical_depth_score: int = Field(ge=1, le=5)
    evidence_from_transcript: str = ""
    concerns: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[misc]
    @property
    def average_score(self) -> float:
        return round(
            (self.relevance_score + self.clarity_score + self.technical_depth_score)
            / 3,
            2,
        )


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```[a-z]*\n?", "", text.strip())
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


class ResponseEvaluator:
    """Gemini-backed evaluator. Instantiate once and reuse across answers."""

    def __init__(self, model_name: str | None = None):
        resolved = model_name or settings.gemini_model
        # Validate before handing it to the SDK. GenerativeModel does
        # `if "/" not in model_name`, so a None/empty value dies inside the
        # library as "argument of type 'NoneType' is not iterable" — a TypeError
        # with no hint that the real problem is unset config.
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError(
                f"Gemini model name is invalid ({resolved!r}). Set GEMINI_MODEL "
                "in your .env (e.g. gemini-2.0-flash)."
            )
        if not isinstance(settings.gemini_api_key, str) or not settings.gemini_api_key.strip():
            raise ValueError(
                "GEMINI_API_KEY is missing/empty. Set it in your .env."
            )
        self.model = genai.GenerativeModel(
            resolved,
            system_instruction=RESPONSE_EVALUATOR_SYSTEM,
        )

    def evaluate(
        self,
        *,
        question: str,
        transcript: str,
        skill_targeted: str | None = None,
        skills_matrix: Any = None,
    ) -> ResponseAssessment:
        """Evaluate one answer. Returns a ResponseAssessment.

        On any parse/LLM failure, returns a minimum-score assessment so the
        pipeline can continue and HR still reviews.
        """
        skills_repr = (
            json.dumps(skills_matrix)
            if isinstance(skills_matrix, (dict, list))
            else str(skills_matrix or {})
        )
        prompt = RESPONSE_EVALUATOR_USER.format(
            question=question,
            skill_targeted=skill_targeted or "general",
            skills_matrix=skills_repr,
            transcript=transcript,
        )

        try:
            response = self.model.generate_content(prompt)
            data = json.loads(_strip_fences(response.text))
            assessment = ResponseAssessment(
                relevance_score=int(data["relevance_score"]),
                clarity_score=int(data["clarity_score"]),
                technical_depth_score=int(data["technical_depth_score"]),
                evidence_from_transcript=data.get("evidence_from_transcript", ""),
                concerns=data.get("concerns", []) or [],
            )
            log.info("evaluator.success", avg_score=assessment.average_score)
            return assessment
        except Exception as exc:
            log.error("evaluator.failed", error=str(exc))
            return ResponseAssessment(
                relevance_score=1,
                clarity_score=1,
                technical_depth_score=1,
                evidence_from_transcript="",
                concerns=["Automated evaluation failed — manual review required."],
            )


def evaluate_response(
    question: str,
    transcript: str,
    skill_targeted: str | None = None,
    skills_matrix: Any = None,
) -> ResponseAssessment:
    """Functional convenience wrapper around ResponseEvaluator."""
    return ResponseEvaluator().evaluate(
        question=question,
        transcript=transcript,
        skill_targeted=skill_targeted,
        skills_matrix=skills_matrix,
    )
