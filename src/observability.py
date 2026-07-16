"""Structured logging + trace IDs. No PII in log messages."""
import uuid
import structlog
import logging
from contextvars import ContextVar

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        # Render tracebacks for any log call made with exc_info=True/an exception.
        # Without this we logged only str(exc) — e.g. "argument of type 'NoneType'
        # is not iterable" — with no file or line, which made errors raised inside
        # third-party libraries (whisper, google-generativeai) effectively
        # untraceable. Log the stack, not just the message.
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()


def new_trace(candidate_id: str | None = None) -> str:
    """Create a new trace ID and bind it to the context. Never log raw PII."""
    tid = str(uuid.uuid4())[:8]
    _trace_id_var.set(tid)
    structlog.contextvars.bind_contextvars(trace_id=tid)
    if candidate_id:
        # bind the ID (not name/email) for correlation
        structlog.contextvars.bind_contextvars(candidate_id=candidate_id)
    return tid


def get_trace_id() -> str:
    return _trace_id_var.get() or "no-trace"
