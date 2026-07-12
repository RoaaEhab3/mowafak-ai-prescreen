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

from src.settings import settings
from src.observability import log
from src.prompts import PARSE_PROMPT

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


def _clean_gemini_json(text: str) -> str:
    """Remove markdown fences and extract the JSON object safely.

    Strips ```json fences, then falls back to slicing from the first "{" to
    the last "}" so stray prose before/after the object doesn't break
    json.loads.
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    # Fallback: extract the first complete JSON object.
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]

    return text.strip()


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
    cleaned = _clean_gemini_json(response.text)

    try:
        data = json.loads(cleaned)
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
    cleaned = _clean_gemini_json(response.text)

    try:
        data = json.loads(cleaned)
        return ParsedCV(candidate_id=candidate_id, **data)
    except Exception as exc:
        log.error("cv_parse_text.failed", candidate_id=candidate_id, error=str(exc))
        return ParsedCV(candidate_id=candidate_id)
