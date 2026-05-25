from pydantic import BaseModel
from typing import List


class ResponseAssessment(BaseModel):
    relevance_score: int
    clarity_score: int
    technical_depth_score: int
    evidence_from_transcript: str
    concerns: List[str]