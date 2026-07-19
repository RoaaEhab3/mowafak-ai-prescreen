# Mowafak — Async AI Interview Pre-Screen

An **async voice pre-screen** where AI assists and HR decides. Candidates upload a
CV and record voice answers in their own time; the system transcribes, evaluates,
and drafts a structured report. **Every report is reviewed by a human HR reviewer —
the system makes auto-reject impossible by design.**

## Architecture

```
Candidate                         Backend (FastAPI)                 HR (Chainlit)
─────────                         ─────────────────                 ─────────────
cv_upload.html ──POST /upload_cv──▶  cv_parser (Gemini)  ─┐
record.html   ──/start_interview──▶  question_generator  ─┤
              ──/upload_answer────▶  whisper_stt + evaluator │  Supabase
              ──/finalize─────────▶  report_generator    ─┘   (schema.sql)
                                     hil_gate + audit_log ◀── app.py
                                       (POST /hr_decision, GET /pending_reports)
```

- **AI pipeline:** `src/agents/` (question generator, response evaluator), `src/cv_parser.py`, `src/whisper_stt.py`, `src/report_generator.py`, orchestrated in `src/orchestrator.py`.
- **Responsible AI:** `src/hil_gate.py` (mandatory HR review — no auto-reject path), `src/audit_log.py` (append-only SHA-256 hash chain), `responsible_ai/` (bias audit, RAI config), `tests/test_evaluator.py` (DeepEval Faithfulness + custom HiLRespect).
- **Storage:** Supabase (Postgres via PostgREST). Schema in `backend/schema.sql`.

## Setup

Requires **Python 3.11 or 3.12** and **ffmpeg** on PATH (Whisper decodes audio with it).

```bash
cp .env.example .env          # then fill in GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY
# apply backend/schema.sql to your Supabase project (SQL editor or `supabase db push`)

pip install -r requirements.txt          # backend + HR UI
pip install -r requirements-stt.txt      # + Whisper (torch/numba) — only where you transcribe
pip install -r requirements-dev.txt      # + DeepEval/pytest — for tests only
```

The requirements are split so `chainlit` (HR UI) and `deepeval` (tests) never
install into the same environment — co-installing them is the main dependency
conflict. See each `requirements-*.txt` header.

## Run

```bash
uvicorn backend.main:app --reload        # API on http://localhost:8000
chainlit run app.py                      # HR review UI on http://localhost:8000 (Chainlit's own port)
# open candidate_app/cv_upload.html in a browser for the candidate flow
```

End-to-end: candidate uploads a CV → records answers → `/finalize` drafts the
report → it appears in the HR UI's review queue → HR approves/rejects/holds,
which is recorded through the audit-logged HIL gate.

## Responsible AI policy

- **Mandatory HR review, no auto-reject.** No candidate-facing decision exists
  without an explicit HR action through `POST /hr_decision` → `hil_gate`.
- **Consent required** before recording (candidate pages gate on it).
- **Append-only, hash-chained audit log**; exportable as CSV from the HR UI.
- **Bias audit** (`responsible_ai/bias_audit.py`) measures score variance across
  name/gender variants of identical answers.
- See `responsible_ai/RAI_Config.yaml` and `SECURITY.md`.

## Reset (dev)

```bash
bash scripts/reset.sh        # clears all rows + the audit log; never touches schema
```
