"""LLM triage with strict validation, one repair attempt, and a rules fallback.

The model is treated as an unreliable dependency, not an oracle:

* its answer must be valid JSON matching the schema, or it is rejected;
* a rejected answer gets exactly one repair attempt;
* if that also fails -- or the API is down, slow, or unconfigured -- the
  deterministic rule engine answers instead and the alert says so.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Protocol

from aiops_alerts import rules
from aiops_alerts.models import Severity, Triage

# Kept deliberately short: the fewer degrees of freedom, the fewer hallucinations.
SYSTEM_PROMPT = """You are a senior data engineer on call for a batch data platform.
You receive the tail of a failed pipeline log and return a diagnosis.

Rules:
- Reply with a single JSON object and nothing else. No markdown, no commentary.
- Use exactly these keys: "root_cause", "workaround", "severity", "confidence".
- "root_cause": at most two sentences, naming the mechanism, not just the symptom.
- "workaround": concrete steps an on-call engineer can execute now.
- "severity": one of "critical", "warning", "info".
- "confidence": a number between 0 and 1 reflecting how sure you are.
- If the log does not contain enough information, say so in "root_cause" and give a
  diagnosis-oriented workaround instead of inventing a specific fix.

Example answer:
{"root_cause": "A Spark executor ran out of memory during a skewed shuffle, so the container was killed before the task finished.", "workaround": "Inspect the stage's partition sizes in the Spark UI, salt the skewed key, and re-run only the failed stage.", "severity": "critical", "confidence": 0.78}"""

REPAIR_PROMPT = """Your previous answer was rejected: {reason}

Reply again with a single JSON object using exactly the keys "root_cause", "workaround",
"severity", "confidence". No markdown, no commentary."""

SEVERITIES = {severity.value for severity in Severity}


class TriageError(RuntimeError):
    """Raised when a model answer cannot be used."""


class LLMTransport(Protocol):
    """Anything that can turn a prompt into text (real API or a test double)."""

    def complete(self, system: str, user: str) -> str: ...


class GeminiTransport:
    """Minimal Gemini REST client built on the standard library."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", timeout: int = 30) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            f"?key={api_key}"
        )

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512},
        }).encode()

        request = urllib.request.Request(
            self.url, data=body, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:  # pragma: no cover - network path
            raise TriageError(f"Gemini returned {error.code}: {error.read().decode()[:200]}") from error
        except OSError as error:  # pragma: no cover - network path
            raise TriageError(f"Gemini request failed: {error}") from error

        try:
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as error:  # pragma: no cover - network path
            raise TriageError(f"unexpected Gemini response shape: {str(payload)[:200]}") from error


def parse_llm_answer(raw: str) -> Triage:
    """Validate a model answer, rejecting anything we cannot trust."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise TriageError(f"answer is not JSON ({error.msg})") from error

    if not isinstance(data, dict):
        raise TriageError("answer is not a JSON object")

    missing = {"root_cause", "workaround", "severity", "confidence"} - data.keys()
    if missing:
        raise TriageError(f"missing keys: {', '.join(sorted(missing))}")

    root_cause = str(data["root_cause"]).strip()
    workaround = str(data["workaround"]).strip()
    severity = str(data["severity"]).strip().lower()
    if not root_cause or not workaround:
        raise TriageError("root_cause and workaround must not be empty")
    if severity not in SEVERITIES:
        raise TriageError(f"severity must be one of {sorted(SEVERITIES)}, got {severity!r}")

    try:
        confidence = float(data["confidence"])
    except (TypeError, ValueError) as error:
        raise TriageError("confidence must be a number") from error
    confidence = min(1.0, max(0.0, confidence))

    return Triage(
        root_cause=root_cause,
        workaround=workaround,
        severity=Severity(severity),
        confidence=round(confidence, 2),
        source="llm",
    )


def llm_triage(excerpt: str, transport: LLMTransport, max_attempts: int = 2) -> Triage:
    """Ask the model, repairing at most once, and surface why an answer was rejected."""
    user = f"Failed pipeline log (tail):\n\n{excerpt}"
    last_error = "unknown error"
    for attempt in range(1, max_attempts + 1):
        raw = transport.complete(SYSTEM_PROMPT, user)
        try:
            return parse_llm_answer(raw)
        except TriageError as error:
            last_error = str(error)
            user = f"{REPAIR_PROMPT.format(reason=last_error)}\n\nFailed pipeline log (tail):\n\n{excerpt}"
    raise TriageError(f"model answer rejected after {max_attempts} attempts: {last_error}")


def triage_failure(
    job_name: str, excerpt: str, transport: LLMTransport | None = None, offline: bool = False
) -> tuple[Triage, list[str]]:
    """Diagnose a failure, preferring the model but never depending on it."""
    notes: list[str] = []
    if transport is not None and not offline:
        try:
            triage = llm_triage(excerpt, transport)
            notes.append(f"triaged by model ({transport.__class__.__name__})")
            return triage, notes
        except (TriageError, Exception) as error:  # noqa: BLE001 - resilience is the point
            notes.append(f"model triage failed ({error}); fell back to rules")

    triage, rule_name = rules.classify(excerpt)
    notes.append(f"triaged by rules (signature: {rule_name})")
    return triage, notes
