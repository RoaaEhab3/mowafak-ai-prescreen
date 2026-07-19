"""Mowafak — Chainlit HR review UI.

This is the Human-in-the-Loop decision surface. It talks to the FastAPI backend
over HTTP: it lists sessions awaiting review, shows each AI report, and records
the HR decision through POST /hr_decision — which is the ONLY path that runs
hil_gate.require_hr_decision() and writes the append-only, hash-chained audit
log via src/audit_log.py.

Earlier this file used a hardcoded mock queue and wrote decisions straight to a
local JSONL with Python's builtin hash(). That bypassed the HIL gate entirely
and produced audit entries that broke verify_chain() — i.e. the whole
Responsible-AI guarantee was not actually enforced. It now goes through the
backend, so there is exactly one writer and one audit ledger.
"""
import sys
import asyncio

# Python 3.14+ Windows event-loop compatibility patch (must run before chainlit).
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except AttributeError:
        pass

import os
import csv
import json

import chainlit as cl
import httpx

# ── Config ─────────────────────────────────────────────────────────────────────

API_BASE = os.environ.get("MOWAFAK_API_BASE", "http://localhost:8000").rstrip("/")
HR_REVIEWER_ID = os.environ.get("MOWAFAK_HR_REVIEWER_ID", "hr_reviewer")
# The backend writes the real audit ledger here (settings.audit_log_path). The
# CSV export reads that same file, so it must run on the same host as the API.
AUDIT_LOG_PATH = os.environ.get("MOWAFAK_AUDIT_LOG", "responsible_ai/audit_log.jsonl")
HTTP_TIMEOUT = 60.0

REC_BADGES = {
    "strong_yes": "🟢 STRONG YES",
    "weak_yes": "🟡 WEAK YES",
    "weak_no": "🟠 WEAK NO",
    "strong_no": "🔴 STRONG NO",
}

# Maps a UI action to the exact value backend /hr_decision accepts
# (Literal["approved","rejected","hold"]).
DECISION_VALUES = {
    "approve_action": "approved",
    "reject_action": "rejected",
    "hold_action": "hold",
}


# ── Backend calls ──────────────────────────────────────────────────────────────

async def _fetch_pending() -> list[dict]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}/pending_reports")
        resp.raise_for_status()
        return resp.json()


async def _fetch_report(session_id: str) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}/get_report/{session_id}")
        resp.raise_for_status()
        return resp.json()


async def _post_decision(session_id: str, decision: str, notes: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        return await client.post(
            f"{API_BASE}/hr_decision",
            json={
                "session_id": session_id,
                "hr_decision": decision,
                "hr_reviewer_id": HR_REVIEWER_ID,
                "hr_notes": notes,
            },
        )


# ── Queue ──────────────────────────────────────────────────────────────────────

@cl.on_chat_start
async def initialize_hr_dashboard():
    await cl.Message(
        content=(
            "## ⚖️ Mowafak — HR Review\n"
            "*Human-in-the-loop gateway. Automated reject paths are disabled by design.*"
        )
    ).send()

    try:
        pending = await _fetch_pending()
    except Exception as exc:
        await cl.Message(
            content=(
                f"❌ Could not reach the backend at `{API_BASE}`.\n\n"
                f"Make sure the API is running (`uvicorn backend.main:app`) and that "
                f"`MOWAFAK_API_BASE` points at it.\n\n`{type(exc).__name__}: {exc}`"
            )
        ).send()
        return

    export_action = cl.Action(
        name="download_compliance_csv",
        label="📊 Export Audit Log (CSV)",
        payload={"action": "export"},
    )

    if not pending:
        await cl.Message(
            content="✅ No candidates are awaiting review right now.",
            actions=[export_action],
        ).send()
        return

    actions = [
        cl.Action(
            name="view_candidate",
            label=f"🔍 Review {p['session_id'][:8]} — {REC_BADGES.get(p['ai_recommendation'], p['ai_recommendation'])} ({p['overall_score']}/5)",
            payload={"session_id": p["session_id"]},
        )
        for p in pending
    ]
    actions.append(export_action)

    await cl.Message(
        content=f"### 📋 {len(pending)} candidate(s) awaiting your review:",
        actions=actions,
    ).send()


@cl.action_callback("view_candidate")
async def handle_view_candidate(action: cl.Action):
    session_id = action.payload.get("session_id")
    if not session_id:
        await cl.Message(content="❌ Missing session reference.").send()
        return

    try:
        r = await _fetch_report(session_id)
    except Exception as exc:
        await cl.Message(content=f"❌ Could not load report: `{exc}`").send()
        return

    badge = REC_BADGES.get(r.get("ai_recommendation"), "⚠️ Unrated")
    skills = " ".join(
        f"**{k}:** `{v}/5`" for k, v in (r.get("per_skill_ratings") or {}).items()
    )
    strengths = "\n".join(f"- {s}" for s in (r.get("strengths") or [])) or "_none listed_"
    concerns = "\n".join(
        f"- {a}" for a in (r.get("areas_for_development") or [])
    ) or "_none listed_"

    already = r.get("hr_decision")
    decision_line = (
        f"\n\n> ⚠️ **Already decided:** `{already}` at {r.get('hr_decided_at')}"
        if already
        else ""
    )

    report_md = (
        f"## 📊 AI Pre-Screening Report\n"
        f"**Session:** `{session_id}`  \n"
        f"**Candidate:** `{r.get('candidate_id')}`  \n"
        f"**Status:** `{r.get('status')}`  \n"
        f"---\n"
        f"* **Overall score:** `{r.get('overall_score')} / 5.0`\n"
        f"* **AI recommendation:** {badge} *(advisory only — you decide)*\n\n"
        f"> {r.get('written_summary', '')}\n\n"
        f"#### 🛠️ Skills\n{skills or '_none_'}\n\n"
        f"#### ✅ Strengths\n{strengths}\n\n"
        f"#### ⚠️ Areas for development\n{concerns}\n"
        f"{decision_line}\n\n"
        f"---\n"
        f"### 🛡️ Your decision (recorded via the audit-logged HR gate)"
    )

    decision_actions = [
        cl.Action(name="approve_action", label="✅ Approve to next round", payload={"session_id": session_id}),
        cl.Action(name="reject_action", label="❌ Reject with feedback", payload={"session_id": session_id}),
        cl.Action(name="hold_action", label="⏳ Hold for review", payload={"session_id": session_id}),
    ]
    await cl.Message(content=report_md, actions=decision_actions).send()


async def _handle_decision(action: cl.Action):
    session_id = action.payload.get("session_id")
    decision = DECISION_VALUES.get(action.name)
    if not session_id or not decision:
        await cl.Message(content="❌ Malformed decision action.").send()
        return

    # HR must document reasoning — the backend requires non-empty notes.
    ask = await cl.AskUserMessage(
        content=f"Enter your HR notes for this **{decision}** decision (required):",
        timeout=240,
    ).send()
    notes = ""
    if ask:
        notes = (ask.get("output") if isinstance(ask, dict) else getattr(ask, "output", "")) or ""
    notes = notes.strip()
    if not notes:
        await cl.Message(
            content="⚠️ Decision cancelled — notes are required to record it."
        ).send()
        return

    try:
        resp = await _post_decision(session_id, decision, notes)
    except Exception as exc:
        await cl.Message(content=f"❌ Could not record decision: `{exc}`").send()
        return

    if resp.status_code == 200:
        data = resp.json()
        await cl.Message(
            content=(
                f"### ✅ Decision recorded\n"
                f"**{decision}** for session `{session_id}` at {data.get('hr_decided_at')}.\n\n"
                f"This decision is now immutable and written to the audit log."
            )
        ).send()
    else:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text
        await cl.Message(
            content=f"❌ Backend rejected the decision (HTTP {resp.status_code}): {detail}"
        ).send()


@cl.action_callback("approve_action")
async def _approve(action: cl.Action):
    await _handle_decision(action)


@cl.action_callback("reject_action")
async def _reject(action: cl.Action):
    await _handle_decision(action)


@cl.action_callback("hold_action")
async def _hold(action: cl.Action):
    await _handle_decision(action)


# ── Audit log CSV export ───────────────────────────────────────────────────────

@cl.action_callback("download_compliance_csv")
async def handle_export_audit_csv(action: cl.Action):
    """Export the backend's real audit ledger (JSONL) as CSV.

    Reads settings.audit_log_path — the same file src/audit_log.py appends to —
    so it only works when this UI runs on the same host as the API.
    """
    if not os.path.exists(AUDIT_LOG_PATH) or os.path.getsize(AUDIT_LOG_PATH) == 0:
        await cl.Message(content="ℹ️ The audit log is empty — no decisions recorded yet.").send()
        return

    records = []
    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not records:
        await cl.Message(content="ℹ️ No parseable audit entries found.").send()
        return

    # Union of keys across entries so no column is dropped.
    headers: list[str] = []
    for rec in records:
        for k in rec:
            if k not in headers:
                headers.append(k)

    csv_path = "responsible_ai/audit_log_export.csv"
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(records)

    await cl.Message(
        content=f"📊 Exported {len(records)} audit entr(y/ies).",
        elements=[cl.File(name="audit_log_export.csv", path=csv_path, mime="text/csv")],
    ).send()
