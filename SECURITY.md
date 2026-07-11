# Security

## Secrets handling
- **Never** hardcode API keys in source. The Gemini key is read from the
  `GEMINI_API_KEY` environment variable (see `src/settings.py` / `.env`).
- `.env` is git-ignored. Use `.env.example` as the template.

## Incident: exposed Gemini API key (resolved in code)
A live Gemini API key was committed to the branch
`feature/whisper-response-eval` (commit `2d7a502`,
`agents/response_evaluator.py`).

**Remediation:**
- ✅ Code on `main` no longer contains the key — the module was re-ported in
  PR #4 to read the key from the environment.
- 🔄 The exposed key is being **revoked** in Google AI Studio and rotated to a
  fresh key (env-only). Tracked in issue #5.
- 🔄 The `feature/whisper-response-eval` branch is scheduled for deletion by the
  repo owner. Tracked in issue #5.

> Deleting the branch removes the ref, but the secret persists in git
> history/GitHub caches until the key is revoked — **revocation is the real
> fix.**
