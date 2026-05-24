import sys
import asyncio

# Python 3.14+ Event Loop Backward-Compatibility Patch
if sys.platform == 'win32':
    try:
        # Forces Windows to use a selector loop that avoids AnyIO context loss
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except AttributeError:
        pass

# ... rest of your existing app.py imports follow right under this ...
import os
import json
import csv
# (Keep all the rest of the code exactly the same)
from datetime import datetime
import asyncio
import chainlit as cl
from pydantic import BaseModel, Field
from typing import List, Optional


# ==========================================
# 1. PYDANTIC DATA INTERFACE SCHEMAS (Req 3)
# ==========================================

class ResponseAssessment(BaseModel):
    """Structured assessment produced by the Response Evaluator agent."""
    relevance_score: int = Field(..., ge=1, le=5, description="1-5 grading score")
    clarity_score: int = Field(..., ge=1, le=5, description="1-5 grading score")
    technical_depth_score: int = Field(..., ge=1, le=5, description="1-5 grading score")
    evidence_from_transcript: str = Field(..., description="Exact string quote from Whisper transcript")
    concerns: List[str] = Field(default_factory=list, description="List of noted flags or concerns")

class CandidateReport(BaseModel):
    """Aggregated screening report produced by the Report Drafter agent."""
    candidate_id: str
    name: str
    email: str
    applied_role: str
    overall_score: float
    summary: str
    skills_ratings: dict  # e.g., {"SQL": 4, "FastAPI": 5}
    assessments: List[ResponseAssessment]
    ai_recommendation: str  # strong_yes, weak_yes, weak_no, strong_no
    bias_variance_score: float = Field(default=0.0, description="Max variance across audit runs")

# ==========================================
# 2. SEED SAMPLE CANDIDATE METRIC GENERATOR
# ==========================================

def get_mock_screening_queue() -> List[CandidateReport]:
    """Generates structured pipeline reports for Mariam's fintech junior dev role."""
    return [
        CandidateReport(
            candidate_id="cand_8f29d1a",
            name="Youssef Mansour",
            email="youssef.mansour@eng.asu.edu.eg",
            applied_role="Junior Data Engineer (Fintech pipeline)",
            overall_score=4.3,
            summary="Strong candidate demonstrating reliable comprehension of batch processes. Answered Python/SQL questions cleanly with explicit performance metrics. Minor hesitation regarding live streaming architectures.",
            skills_ratings={"Python": 5, "SQL": 4, "Apache Spark": 4, "API Design": 3},
            bias_variance_score=0.15,
            ai_recommendation="strong_yes",
            assessments=[
                ResponseAssessment(
                    relevance_score=5,
                    clarity_score=4,
                    technical_depth_score=5,
                    evidence_from_transcript="I optimization-tested the indexing system which reduced our query latency metrics from 800 milliseconds down to 45 milliseconds under high volume.",
                    concerns=[]
                ),
                ResponseAssessment(
                    relevance_score=4,
                    clarity_score=4,
                    technical_depth_score=3,
                    evidence_from_transcript="We loaded data using standard cursor fetches, but I haven't implemented real-time windowing parameters over Kafka streams directly yet.",
                    concerns=["Lacks direct real-time streaming experience"]
                )
            ]
        ),
        CandidateReport(
            candidate_id="cand_3a91b8c",
            name="Amira El-Sayed",
            email="amira.elsayed@bis.helwan.edu",
            overall_score=3.8,
            applied_role="Junior Data Engineer (Fintech pipeline)",
            summary="Capable developer presenting reliable academic background. Showcased robust understanding of schema normalization patterns, though syntax structure on secondary responses was slightly generic.",
            skills_ratings={"Python": 4, "SQL": 5, "Apache Spark": 3, "API Design": 4},
            bias_variance_score=0.08,
            ai_recommendation="weak_yes",
            assessments=[
                ResponseAssessment(
                    relevance_score=4,
                    clarity_score=4,
                    technical_depth_score=4,
                    evidence_from_transcript="For our business project system, I fully isolated the staging databases by writing composite primary constraints across historical transaction items.",
                    concerns=[]
                )
            ]
        )
    ]

# ==========================================
# 3. INTERACTIVE SYSTEM WORKFLOW ENGINES
# ==========================================

@cl.on_chat_start
async def initialize_hr_dashboard():
    """Triggers instantly when Mariam opens localhost:8000 to launch the view architecture."""
    # Set up memory states to store our loaded records
    queue = get_mock_screening_queue()
    cl.user_session.set("candidate_queue", queue)
    
    # Welcome banner markup rendering via native Chainlit markdown
    welcome_message = (
        "## ⚖️ Mowafak — Async AI Interview Pre-Screen\n"
        "*Responsible Human-in-the-Loop Gateway Management System*\n"
        "--- \n"
        "Welcome back, **Mariam**. There are currently **2 applicants** who completed their voice "
        "pre-screenings awaiting your manual operational review. **Automated rejection paths are disabled by design.**\n\n"
        "Select a candidate below to unpack their parsed transcripts, AI-generated evidence records, and bias verification tracking scores."
    )
    await cl.Message(content=welcome_message).send()
    
    # Build a scannable dashboard menu layout using interactive click-action buttons
    # Build a scannable dashboard menu layout using interactive click-action buttons
    # Build a scannable dashboard menu layout using interactive click-action buttons
    actions = [
        cl.Action(
            name="view_candidate", 
            value="cand_8f29d1a", 
            label="🔍 Review: Youssef Mansour (Score: 4.3/5)",
            description="View full evaluation report",
            payload={"candidate_id": "cand_8f29d1a"}  # Pass explicitly inside the payload
        ),
        cl.Action(
            name="view_candidate", 
            value="cand_3a91b8c", 
            label="🔍 Review: Amira El-Sayed (Score: 3.8/5)",
            description="View full evaluation report",
            payload={"candidate_id": "cand_3a91b8c"}  # Pass explicitly inside the payload
        ),
        cl.Action(
            name="download_compliance_csv",  # Changed name string to bypass browser cache
            value="export", 
            label="📊 Export System Audit Logs (CSV)",
            description="Download copy of append-only audit tracking files",
            payload={"action": "export"}
        )
    ]
    
    await cl.Message(
        content="### 📋 Pending Pre-Screen Evaluation Queue:", 
        actions=actions
    ).send()
# ==========================================
# 4. REPORT PARSER & HIL GAUNTLET CONTROLLER
# ==========================================

@cl.action_callback("view_candidate")
async def handle_view_candidate(action: cl.Action):
    """Fires instantly when Mariam clicks on an applicant's button profile queue."""
    # Pull securely out of the action payload dictionary
    candidate_id = action.payload.get("candidate_id")
    if not candidate_id:
        await cl.Message(content="❌ System routing error: Action reference payload is missing.").send()
        return

    queue = cl.user_session.get("candidate_queue")
    candidate = next((c for c in queue if c.candidate_id == candidate_id), None)
    if not candidate:
        await cl.Message(content="❌ Error: Target candidate system instance record not found.").send()
        return

    # Track currently active record context inside user memory session storage namespaces
    cl.user_session.set("active_review_candidate", candidate)

    # 1. Map Recommendation Text Tags to Clean Visual Display Badges
    rec_badges = {
        "strong_yes": "🟢 STRONG YES (Highly Grounded Evaluation)",
        "weak_yes": "🟡 WEAK YES (Needs Targeted Manual Check)",
        "weak_no": "🟠 WEAK NO (Marginal Metric Fit)",
        "strong_no": "🔴 STRONG NO (Significant Competency Gaps)"
    }
    badge = rec_badges.get(candidate.ai_recommendation, "⚠️ Unrated Profile")

    # 2. Build Structured Metric Star Rating Display Row
    stars_display = " ".join([f"**{skill}:** `{rating}/5`⭐" for skill, rating in candidate.skills_ratings.items()])

    # 3. Construct Executive Summary Markdown Blocks
    report_md = (
        f"## 📊 AI Pre-Screening Evaluation Report\n"
        f"**Candidate Reference Name:** {candidate.name}  \n"
        f"**Contact Address Matrix:** `{candidate.email}`  \n"
        f"**Target Allocation Track:** *{candidate.applied_role}*  \n"
        f"--- \n"
        f"### ⚡ Executive Overview & Metrics\n"
        f"* **Overall Core Performance Score:** `{candidate.overall_score} / 5.0` \n"
        f"* **System Suggested Recommendation Tag:** {badge} \n"
        f"* **Quarterly Verification Bias Variance:** `{candidate.bias_variance_score * 100:.1f}%` "
        f"*(Target threshold less than 5.0% deviation metrics)*\n\n"
        f"> **Report Drafter Summary:** {candidate.summary}\n\n"
        f"#### 🛠️ Parsed Technical Skills Matrix:\n"
        f"{stars_display}\n\n"
        f"--- \n"
        f"### 🔍 Detailed Per-Question Transcript Audit Logs\n"
    )

    # 4. Iterate and Unroll Individual Response Evaluations
    for idx, eval_item in enumerate(candidate.assessments):
        report_md += (
            f"#### ❓ Question Breakdown #{idx + 1}\n"
            f"* **Relevance Metric:** `{eval_item.relevance_score}/5` | "
            f"**Clarity Metric:** `{eval_item.clarity_score}/5` | "
            f"**Technical Depth Metric:** `{eval_item.technical_depth_score}/5` \n\n"
            f"⚡ **Evidence Grounding Transcript Quote (Mandatory Trace):**  \n"
            f"*{{\"{eval_item.evidence_from_transcript}\"}}*  \n"
        )
        if eval_item.concerns:
            concerns_list = ", ".join([f"`{c}`" for c in eval_item.concerns])
            report_md += f"⚠️ **Flagged Structural Anomaly Annotations:** {concerns_list}\n"
        report_md += "\n"

    report_md += (
        f"--- \n"
        f"### 🛡️ Mandatory Human-in-the-Loop Action Gate\n"
        f"**Notice for Mariam:** This pipeline blocks all automatic email pathways. Candidate communication "
        f"requires manual confirmation below. You assume legal processing sign-off upon button interaction."
    )

    # 5. Inject the 3 Mutually Exclusive Decisive Action Buttons (Explicit Names for Callbacks)
    decision_actions = [
        cl.Action(name="approve_action", value="approve", label="✅ Approve to Next Round", description="Move candidate forward", payload={"candidate_id": candidate_id}),
        cl.Action(name="reject_action", value="reject", label="❌ Reject with Custom Feedback", description="Log human rejection path", payload={"candidate_id": candidate_id}),
        cl.Action(name="hold_action", value="hold", label="⏳ Hold for Additional Review", description="Lock state context", payload={"candidate_id": candidate_id})
    ]

    await cl.Message(content=report_md, actions=decision_actions).send()


# ==========================================
# 5. SPLIT HANDLERS FOR EACH GATE ACTION
# ==========================================

async def process_gate_decision(action: cl.Action, action_type: str):
    """Core helper to log the final human decision to append-only files."""
    candidate_id = action.payload.get("candidate_id")
    queue = cl.user_session.get("candidate_queue")
    candidate = next((c for c in queue if c.candidate_id == candidate_id), None)
    
    if not candidate:
        await cl.Message(content="❌ Session Context Expired. Please reload the queue.").send()
        return

    os.makedirs("responsible_ai", exist_ok=True)
    log_file_path = "responsible_ai/audit_log.jsonl"

    # Construct complete structured JSON log audit entry (Req 4)
    audit_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "candidate_id": candidate.candidate_id,
        "candidate_name": candidate.name,
        "ai_recommendation": candidate.ai_recommendation,
        "hr_decision": action_type,
        "hr_user_id": "hr_mgr_mariam_cairo",
        "hr_notes_hash": hash(f"{candidate.candidate_id}-{action_type}-verified")
    }

    # Write in append-only format to secure file lines
    with open(log_file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(audit_entry) + "\n")

    # Generate confirmation feedback UI alerts
    status_styling = "🟢" if "APPROVED" in action_type else "🔴" if "REJECTED" in action_type else "🟡"
    confirmation_text = (
        f"### {status_styling} Human Action Gate Cleared Successfully\n"
        f"The manual assessment determination for **{candidate.name}** has been recorded.\n"
        f"* **Logged Operational Resolution:** `{action_type}`\n"
        f"* **Secure Append-Only Signature Trace:** `{audit_entry['hr_notes_hash']}`\n\n"
        f"Transaction successfully written to structural auditing line trace: `responsible_ai/audit_log.jsonl`."
    )
    await cl.Message(content=confirmation_text).send()


@cl.action_callback("approve_action")
async def handle_approve(action: cl.Action):
    await process_gate_decision(action, "APPROVED_TO_NEXT_ROUND")

@cl.action_callback("reject_action")
async def handle_reject(action: cl.Action):
    await process_gate_decision(action, "REJECTED_WITH_FEEDBACK")

@cl.action_callback("hold_action")
async def handle_hold(action: cl.Action):
    await process_gate_decision(action, "HELD_FOR_REVIEW")


# ==========================================
# 6. AUDIT TRAILS TO CSV EXPORT ROUTER
# ==========================================

@cl.action_callback("download_compliance_csv")
async def handle_export_audit_csv(action: cl.Action):
    """Parses raw JSONL logs on-the-fly and drops them into a spreadsheet download format."""
    jsonl_path = "responsible_ai/audit_log.jsonl"
    csv_path = "responsible_ai/audit_log_export.csv"

    os.makedirs("responsible_ai", exist_ok=True)
    
    if not os.path.exists(jsonl_path) or os.path.getsize(jsonl_path) == 0:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "candidate_id": "cand_demo_init",
                "candidate_name": "System Audit Baseline Tracker",
                "ai_recommendation": "strong_yes",
                "hr_decision": "SYSTEM_INITIALIZATION_BOOT",
                "hr_user_id": "system_admin",
                "hr_notes_hash": 0
            }) + "\n")

    try:
        records = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line.strip()))

        if records:
            headers = records[0].keys()
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(records)

            # READ FILE BINARY CONTENT
            with open(csv_path, "rb") as file_buffer:
                file_content = file_buffer.read()

            # FIX: Explicitly supply the correct MIME type to prevent Attachment.tsx from crashing
            file_element = cl.File(
                content=file_content, 
                name="audit_log_export.csv",
                mime="text/csv"  # <-- THIS STRING PREVENTS THE CRASH
            )
            
            await cl.Message(
                content="📊 **System Audit Log Generated.** Click the download button below to fetch your compliance tracking file.",
                elements=[file_element]
            ).send()
            
    except Exception as e:
        await cl.Message(content=f"❌ Export pipeline encountered an error: `{str(e)}`").send()