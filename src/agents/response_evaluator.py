import os
import json
import re
from typing import Any
from langchain_google_genai import ChatGoogleGenerativeAI

from pydantic import BaseModel
from typing import List

from ..prompts import RESPONSE_EVALUATOR_SYSTEM

import os
from dotenv import load_dotenv

load_dotenv()

class ResponseAssessment(BaseModel):
    relevance_score: int
    clarity_score: int
    technical_depth_score: int
    evidence_from_transcript: str
    concerns: List[str]

class ResponseEvaluator:

    def __init__(self):

        self.llm = ChatGoogleGenerativeAI(
    model=os.getenv("GOOGLE_API_MODEL"),
    google_api_key=os.getenv("GOOGLE_API_KEY"),
    temperature=0
)

    def _clean_json_output(self, content: str) -> str:
        """Remove markdown fences and extra text."""
        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
        if "{" in content and "}" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            content = content[start:end]
        return content.strip()    
    

    def evaluate(
        self,
        question: str,
        transcript: str,
        skills: list
    ) -> ResponseAssessment:
        
        if not transcript or not transcript.strip():
            # Early return for empty answers
            return ResponseAssessment(
                relevance_score=1,
                clarity_score=1,
                technical_depth_score=1,
                evidence_from_transcript="No transcript provided.",
                concerns=["Candidate provided no audible response."],
            )

        prompt = RESPONSE_EVALUATOR_SYSTEM.format(
            question=question,
            transcript=transcript,
            skills=skills
        )

        try:
            response = self.llm.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            cleaned = self._clean_json_output(content)
            data: dict[str, Any] = json.loads(cleaned)
            required_keys = {
                "relevance_score",
                "clarity_score",
                "technical_depth_score",
                "evidence_from_transcript",
                "concerns",
            }
            if not required_keys.issubset(data.keys()):
                raise ValueError("Missing required fields in LLM response")
            return ResponseAssessment(**data)

        except (json.JSONDecodeError, ValueError, Exception) as e:
            # Fallback if anything goes wrong
            print(f"Evaluation failed for question: {question[:80]}... Error: {e}")
            return ResponseAssessment(
                relevance_score=2,
                clarity_score=2,
                technical_depth_score=2,
                evidence_from_transcript="Evaluation failed due to parsing error.",
                concerns=[f"Failed to parse AI evaluation: {str(e)[:200]}"],
            )