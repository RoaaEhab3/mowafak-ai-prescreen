"""Question Generator Agent.

Takes a ParsedCV and produces a list of InterviewQuestion objects via Gemini.
"""
from __future__ import annotations
import sys
import os

import json
import re

from google import genai
from google.genai.types import GenerateContentResponse
from pydantic import BaseModel


from ..cv_parser import ParsedCV
from ..prompts import QUESTION_GENERATOR_SYSTEM, QUESTION_GENERATOR_USER
from ..observability import log


from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


class InterviewQuestion(BaseModel):
    id: str
    question: str
    skill_targeted: str
    question_type: str  # technical | behavioural | situational


def generate_questions(parsed_cv: ParsedCV, n_questions: int | None = None) -> list[InterviewQuestion]:
    """Generate interview questions from a ParsedCV. Returns list of InterviewQuestion."""
    n = n_questions or int(os.getenv("QUESTIONS_PER_INTERVIEW"))
    log.info("question_gen.start", candidate_id=parsed_cv.candidate_id, n=n)

    skills_matrix_str = json.dumps(parsed_cv.skills_matrix, indent=2)
    cv_summary = (
        f"Summary: {parsed_cv.summary}\n"
        f"Total experience: {parsed_cv.total_experience_years} years\n"
        f"Skills: {', '.join(parsed_cv.raw_skills[:20])}\n"
        f"Roles: {', '.join(w.title for w in parsed_cv.work_experience[:5])}"
    )

    prompt = QUESTION_GENERATOR_USER.format(
        cv_summary=cv_summary,
        skills_matrix=skills_matrix_str,
        n_questions=n,
    )

    try:
        response: GenerateContentResponse = client.models.generate_content(
            model=os.getenv("GOOGLE_API_MODEL", "gemini-1.5-flash"),
            contents=prompt,
            config={
                "system_instruction": QUESTION_GENERATOR_SYSTEM,
                "temperature": 0.7,
            }
        )

        text = response.text.strip() if response.text else ""

        # Clean markdown code blocks
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text, flags=re.IGNORECASE)

        raw_list: list[dict[str, Any]] = json.loads(text)
        questions = [InterviewQuestion.model_validate(q) for q in raw_list]

        log.info("question_gen.success", 
                candidate_id=parsed_cv.candidate_id, 
                count=len(questions))
        return questions

    except Exception as exc:
        log.error("question_gen.failed", 
                 candidate_id=parsed_cv.candidate_id, 
                 error=str(exc))
        # Fallback
        return [InterviewQuestion(
            id="q1",
            question="Tell me about a technically challenging project you led.",
            skill_targeted="general",
            question_type="behavioural",
        )]

