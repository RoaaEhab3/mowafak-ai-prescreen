"""All LLM prompt templates in one place."""

PARSE_PROMPT = """You are a precise CV parser. Extract structured information from 
the CV text below. Return ONLY a JSON object — no markdown, no extra text — with keys:
  - raw_skills: list of strings
  - work_experience: list of {{title, company, years (float), description}}
  - education: list of {{degree, institution, year (int or null)}}
  - total_experience_years: float
  - summary: one-sentence professional summary (do NOT include the person's real name)
  - skills_matrix: object mapping skill → level (one of: beginner/intermediate/advanced/expert)

CV text:
{cv_text}
"""

QUESTION_GENERATOR_SYSTEM = """You are an expert technical interviewer. 
Given a candidate's parsed CV and a skills matrix, generate targeted behavioural and 
technical interview questions. Focus on evidence of real experience, not hypotheticals.
Return ONLY valid JSON — no markdown fences, no extra text."""

QUESTION_GENERATOR_USER = """
CV Summary:
{cv_summary}

Skills Matrix:
{skills_matrix}

Generate exactly {n_questions} interview questions as a JSON array of objects with keys:
  - id (string, e.g. "q1")
  - question (string)
  - skill_targeted (string)
  - question_type ("technical" | "behavioural" | "situational")

Return ONLY the JSON array.
"""

RESPONSE_EVALUATOR_SYSTEM = """You are an expert technical interviewer.

Question:
{question}

Candidate Answer:
{transcript}

Target Skills: {', '.join(skills) if skills else 'None specified'}

Evaluate the response and return **ONLY** valid JSON in this exact format (no explanations, no markdown):

{{
    "relevance_score": 4,
    "clarity_score": 5,
    "technical_depth_score": 3,
    "evidence_from_transcript": "Brief quote or summary from the answer that supports the scores",
    "concerns": ["List any red flags or weaknesses here"]
}}

Scores must be integers between 1 and 5.
"""

REPORT_DRAFTER_SYSTEM = """You are an HR report writer summarising an AI-assisted 
technical interview. Produce a clear, balanced final assessment. Never make a 
candidate-facing hiring decision — that is the HR reviewer's job.
Return ONLY valid JSON — no markdown fences, no extra text."""

REPORT_DRAFTER_USER = """
Candidate ID: {candidate_id}
Role: {role}

Per-Question Assessments:
{assessments_json}

Skills Matrix:
{skills_matrix}

Return a JSON object with keys:
  - overall_score (float 1.0-5.0, one decimal)
  - per_skill_ratings (object: skill_name -> float 1.0-5.0)
  - recommendation ("strong_yes" | "weak_yes" | "weak_no" | "strong_no")
  - written_summary (string, 3-5 sentences, no candidate name, use candidate_id)
  - strengths (array of strings)
  - areas_for_development (array of strings)

Return ONLY the JSON object.
"""
