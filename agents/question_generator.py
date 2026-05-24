"""Question Generator Agent.

Takes a ParsedCV and produces a list of InterviewQuestion objects via Gemini.
"""
from __future__ import annotations
import sys
import os

project_root = os.path.abspath("D:/Data Science/Sprints/Grad_proj/colab/src")

if project_root not in sys.path:
    sys.path.append(project_root)


import json
import re

import google.generativeai as genai
from pydantic import BaseModel

from settings import settings
from prompts import QUESTION_GENERATOR_SYSTEM, QUESTION_GENERATOR_USER
from cv_parser import ParsedCV
from observability import log

genai.configure(api_key=settings.gemini_api_key)


class InterviewQuestion(BaseModel):
    id: str
    question: str
    skill_targeted: str
    question_type: str  # technical | behavioural | situational


def generate_questions(parsed_cv: ParsedCV, n_questions: int | None = None) -> list[InterviewQuestion]:
    """Generate interview questions from a ParsedCV. Returns list of InterviewQuestion."""
    n = n_questions or settings.questions_per_interview
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

    model = genai.GenerativeModel(
        settings.gemini_model,
        system_instruction=QUESTION_GENERATOR_SYSTEM,
    )
    response = model.generate_content(prompt)
    text = re.sub(r"^```[a-z]*\n?", "", response.text.strip())
    text = re.sub(r"\n?```$", "", text)

    try:
        raw_list = json.loads(text)
        questions = [InterviewQuestion(**q) for q in raw_list]
        log.info("question_gen.success", candidate_id=parsed_cv.candidate_id,
                 count=len(questions))
        return questions
    except Exception as exc:
        log.error("question_gen.failed", candidate_id=parsed_cv.candidate_id, error=str(exc))
        # Fallback: one generic question
        return [InterviewQuestion(
            id="q1",
            question="Tell me about a technically challenging project you led.",
            skill_targeted="general",
            question_type="behavioural",
        )]


