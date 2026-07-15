import sys
import asyncio
import os
import json
import csv
from datetime import datetime
from typing import List

import chainlit as cl
from pydantic import BaseModel, Field

# Python 3.14+ Windows Event Loop Fix
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except AttributeError:
        pass


# ==========================================
# Pydantic Schemas
# ==========================================

class ResponseAssessment(BaseModel):
    relevance_score: int = Field(..., ge=1, le=5)
    clarity_score: int = Field(..., ge=1, le=5)
    technical_depth_score: int = Field(..., ge=1, le=5)
    evidence_from_transcript: str = Field(...)
    concerns: List[str] = Field(default_factory=list)


class CandidateReport(BaseModel):
    candidate_id: str
    name: str
    email: str
    applied_role: str
    overall_score: float
    summary: str
    skills_ratings: dict
    assessments: List[ResponseAssessment]
    ai_recommendation: str
    bias_variance_score: float = 0.0


# ==========================================
# Mock Data (Replace with real DB/agent later)
# ==========================================

def get_mock_screening_queue() -> List[CandidateReport]:
    """Return sample candidates for demo."""
    return [
        CandidateReport(
            candidate_id="cand_8f29d1a",
            name="Youssef Mansour",
            email="youssef.mansour@eng.asu.edu.eg",
            applied_role="Junior Data Engineer (Fintech pipeline)",
            overall_score=4.3,
            summary="Strong candidate demonstrating reliable comprehension of batch processes...",
            skills_ratings={"Python": 5, "SQL": 4, "Apache Spark": 4, "API Design": 3},
            bias_variance_score=0.15,
            ai_recommendation="strong_yes",
            assessments=[...],  # Keep your existing assessments
        ),
        # ... (second candidate remains the same)
    ]


# ==========================================
# Chainlit Handlers
# ==========================================

@cl.on_chat_start
async def initialize_hr_dashboard():
    """Initialize the HR dashboard when Mariam opens the app."""
    queue: List[CandidateReport] = get_mock_screening_queue()
    cl.user_session.set("candidate_queue", queue)

    welcome_message = (
        "## ⚖️ Mowafak — AI Interview Pre-Screening\n"
        "*Responsible Human-in-the-Loop System*\n"
        "---\n"
        "Welcome back, **Mariam**. There are currently **2 applicants** awaiting your review.\n\n"
        "All automated decisions are **disabled**. You have final authority."
    )

    await cl.Message(content=welcome_message).send()

    # Create action buttons
    actions = [
        cl.Action(
            name="view_candidate",
            value=cand.candidate_id,
            label=f"🔍 Review: {cand.name} (Score: {cand.overall_score}/5)",
            payload={"candidate_id": cand.candidate_id}
        )
        for cand in queue
    ]

    actions.append(
        cl.Action(
            name="download_compliance_csv",
            value="export",
            label="📊 Export Audit Logs (CSV)",
            payload={"action": "export"}
        )
    )

    await cl.Message(content="### 📋 Pending Pre-Screen Queue:", actions=actions).send()


@cl.action_callback("view_candidate")
async def handle_view_candidate(action: cl.Action):
    """Display detailed candidate report."""
    candidate_id = action.payload.get("candidate_id")
    queue = cl.user_session.get("candidate_queue")
    candidate: CandidateReport | None = next(
        (c for c in queue if c.candidate_id == candidate_id), None
    )

    if not candidate:
        await cl.Message(content="❌ Candidate not found.").send()
        return

    cl.user_session.set("active_review_candidate", candidate)

    # Recommendation badge
    rec_badges = {
        "strong_yes": "🟢 STRONG YES",
        "weak_yes": "🟡 WEAK YES",
        "weak_no": "🟠 WEAK NO",
        "strong_no": "🔴 STRONG NO",
    }
    badge = rec_badges.get(candidate.ai_recommendation, "⚠️ Unrated")

    stars = " ".join([f"**{skill}:** `{rating}/5`⭐" for skill, rating in candidate.skills_ratings.items()])

    report_md = f"""## 📊 AI Pre-Screening Report
**Name:** {candidate.name}  
**Email:** `{candidate.email}`  
**Role:** *{candidate.applied_role}*

### ⚡ Overview
- **Overall Score:** `{candidate.overall_score}/5`
- **AI Recommendation:** {badge}
- **Bias Variance:** `{candidate.bias_variance_score*100:.1f}%`

> **Summary:** {candidate.summary}

#### 🛠️ Skills
{stars}

---
### 🔍 Question Assessments
"""

    for i, assess in enumerate(candidate.assessments, 1):
        report_md += f"""#### Question {i}
**Relevance:** `{assess.relevance_score}/5` | **Clarity:** `{assess.clarity_score}/5` | **Depth:** `{assess.technical_depth_score}/5`

**Evidence:** *"{assess.evidence_from_transcript}"*

"""
        if assess.concerns:
            report_md += f"⚠️ Concerns: {', '.join(assess.concerns)}\n\n"

    # Decision buttons
    decision_actions = [
        cl.Action(name="approve_action", value="approve", label="✅ Approve to Next Round", payload={"candidate_id": candidate_id}),
        cl.Action(name="reject_action", value="reject", label="❌ Reject", payload={"candidate_id": candidate_id}),
        cl.Action(name="hold_action", value="hold", label="⏳ Hold for Review", payload={"candidate_id": candidate_id}),
    ]

    await cl.Message(content=report_md, actions=decision_actions).send()


# Decision Handlers
async def process_gate_decision(action: cl.Action, decision_type: str):
    candidate_id = action.payload.get("candidate_id")
    queue = cl.user_session.get("candidate_queue")
    candidate = next((c for c in queue if c.candidate_id == candidate_id), None)

    if not candidate:
        await cl.Message(content="Session expired.").send()
        return

    os.makedirs("responsible_ai", exist_ok=True)
    log_path = "responsible_ai/audit_log.jsonl"

    audit_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "candidate_id": candidate.candidate_id,
        "candidate_name": candidate.name,
        "ai_recommendation": candidate.ai_recommendation,
        "hr_decision": decision_type,
        "hr_user_id": "hr_mgr_mariam_cairo",
    }

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(audit_entry) + "\n")

    await cl.Message(content=f"✅ Decision **{decision_type}** recorded for {candidate.name}").send()


@cl.action_callback("approve_action")
async def handle_approve(action: cl.Action):
    await process_gate_decision(action, "APPROVED_TO_NEXT_ROUND")


@cl.action_callback("reject_action")
async def handle_reject(action: cl.Action):
    await process_gate_decision(action, "REJECTED")


@cl.action_callback("hold_action")
async def handle_hold(action: cl.Action):
    await process_gate_decision(action, "HELD_FOR_REVIEW")


# CSV Export
@cl.action_callback("download_compliance_csv")
async def handle_export_audit_csv(_: cl.Action):
    """Export audit log as CSV."""
    jsonl_path = "responsible_ai/audit_log.jsonl"
    csv_path = "responsible_ai/audit_log_export.csv"

    # ... (keep your existing export logic, it's quite good)
    # I can refine it further if you want