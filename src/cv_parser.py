"""CV Parser — extracts structured data from a PDF or plain-text CV.

Uses pypdf for PDF → text extraction, then Gemini to produce a structured
ParsedCV Pydantic object. No PII is logged — only candidate_id.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import google.generativeai as genai
from pydantic import BaseModel, Field
from pypdf import PdfReader

from settings import settings
from observability import log
from prompts import PARSE_PROMPT

genai.configure(api_key=settings.gemini_api_key)


# ── Pydantic models ────────────────────────────────────────────────────────────

class WorkExperience(BaseModel):
    title: str
    company: str
    years: float = 0.0
    description: str = ""


class Education(BaseModel):
    degree: str
    institution: str
    year: Optional[int] = None


class ParsedCV(BaseModel):
    candidate_id: str
    raw_skills: list[str] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    total_experience_years: float = 0.0
    summary: str = ""
    # Skills matrix: skill -> self-assessed level
    skills_matrix: dict[str, str] = Field(default_factory=dict)


# ── Text extraction ────────────────────────────────────────────────────────────

def extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def extract_text_from_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return extract_text_from_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")




def parse_cv(file_path: Path | str, candidate_id: str) -> ParsedCV:
    """Parse a CV file and return a structured ParsedCV. Logs use candidate_id only."""
    path = Path(file_path)
    log.info("cv_parse.start", candidate_id=candidate_id, file_ext=path.suffix)

    raw_text = extract_text_from_file(path)
    if not raw_text.strip():
        log.warning("cv_parse.empty_text", candidate_id=candidate_id)
        return ParsedCV(candidate_id=candidate_id)

    model = genai.GenerativeModel(settings.gemini_model)
    response = model.generate_content(PARSE_PROMPT.format(cv_text=raw_text[:8000]))
    text = response.text.strip()

    # Strip markdown fences if model ignores instructions
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)

    try:
        data = json.loads(text)
        parsed = ParsedCV(candidate_id=candidate_id, **data)
        log.info("cv_parse.success", candidate_id=candidate_id,
                 skills_count=len(parsed.raw_skills))
        return parsed
    except Exception as exc:
        log.error("cv_parse.failed", candidate_id=candidate_id, error=str(exc))
        # Return minimal object so pipeline can continue
        return ParsedCV(candidate_id=candidate_id, summary="Parse failed — minimal fallback")


def parse_cv_from_text(raw_text: str, candidate_id: str) -> ParsedCV:
    """Parse a CV from a raw text string (e.g. already extracted)."""
    log.info("cv_parse_text.start", candidate_id=candidate_id)

    model = genai.GenerativeModel(settings.gemini_model)
    response = model.generate_content(PARSE_PROMPT.format(cv_text=raw_text[:8000]))
    text = re.sub(r"^```[a-z]*\n?", "", response.text.strip())
    text = re.sub(r"\n?```$", "", text)

    try:
        data = json.loads(text)
        return ParsedCV(candidate_id=candidate_id, **data)
    except Exception as exc:
        log.error("cv_parse_text.failed", candidate_id=candidate_id, error=str(exc))
        return ParsedCV(candidate_id=candidate_id)
