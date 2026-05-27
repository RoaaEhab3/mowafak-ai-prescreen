import os
import json


from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from models.assessment import ResponseAssessment

load_dotenv()


class ResponseEvaluator:

    def __init__(self):

        self.llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key="AIzaSyBbp1uDRoWa3c1pXpfebee3Uu0r3Jeiq84",
    temperature=0
)
    
        

    def evaluate(
        self,
        question: str,
        transcript: str,
        skills: list
    ) -> ResponseAssessment:

        prompt = f"""
You are an AI interview evaluator.

Question:
{question}

Candidate Answer:
{transcript}

Skills:
{skills}

Return ONLY valid JSON in this exact format:

{{
    "relevance_score": 1,
    "clarity_score": 1,
    "technical_depth_score": 1,
    "evidence_from_transcript": "",
    "concerns": []
}}

Do not add explanations.
"""

        response = self.llm.invoke(prompt)

        content = response.content.strip()

        if content.startswith("```json"):
            content = content.replace("```json", "")
            content = content.replace("```", "")
            content = content.strip()

        data = json.loads(content)

        

        return ResponseAssessment(**data)