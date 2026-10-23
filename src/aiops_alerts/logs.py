"""Log preparation: keep the signal, drop the secrets.

Failure logs are sent to a third-party model, so credentials must never leave
the machine. Everything here is deterministic and covered by tests.
"""

from __future__ import annotations

import re

DEFAULT_TAIL_LINES = 200

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # key=value secrets: api_key=..., PASSWORD: "...", token='...'
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd)\b"
            r"\s*[:=]\s*[\"']?([^\s\"',;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "[REDACTED_JWT]"),
    # connection strings with inline credentials
    (
        re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s\"']+"),
        r"\1://[REDACTED]",
    ),
]

# Lines that usually carry the actual failure, most specific first.
_SIGNATURE_PATTERNS = [
    re.compile(r"(?i)\b(Exception|Error|Failure|FATAL|CRITICAL)\b.*"),
    re.compile(r"(?i)\b(OutOfMemoryError|FetchFailedException|AnalysisException)\b.*"),
    re.compile(r"(?i)^.*\b(error|failed|timeout|denied)\b.*$"),
]


def extract_tail(text: str, lines: int = DEFAULT_TAIL_LINES) -> str:
    """Return the last ``lines`` lines; failures are almost always at the end."""
    if lines <= 0:
        raise ValueError("lines must be positive")
    return "\n".join(text.splitlines()[-lines:])


def redact(text: str) -> tuple[str, int]:
    """Replace credentials with placeholders; return the text and how many hits."""
    count = 0
    redacted = text
    for pattern, replacement in _REDACTIONS:
        redacted, hits = pattern.subn(replacement, redacted)
        count += hits
    return redacted, count


def error_signature(text: str) -> str:
    """Normalize the failure line so repeats of the same bug share one key.

    Volatile parts (timestamps, numbers, ids, paths) are removed, which is what
    makes de-duplication work across separate runs.
    """
    candidate = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in _SIGNATURE_PATTERNS:
            if pattern.search(stripped):
                candidate = stripped
                break
        if candidate:
            break

    if not candidate:
        candidate = text.strip().splitlines()[-1] if text.strip() else "unknown-failure"

    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?\b", "<ts>", candidate)
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()[:300]


def prepare_log_excerpt(text: str, lines: int = DEFAULT_TAIL_LINES) -> tuple[str, int]:
    """Tail the log and strip secrets in one step."""
    tail = extract_tail(text, lines)
    return redact(tail)
