import pytest

from aiops_alerts.models import Severity
from aiops_alerts.triage import TriageError, llm_triage, parse_llm_answer, triage_failure

VALID = (
    '{"root_cause": "The executor ran out of memory while sorting a skewed shuffle partition.",'
    ' "workaround": "Salt the join key and re-run the failed stage.",'
    ' "severity": "critical", "confidence": 0.82}'
)


class FakeTransport:
    """Stands in for the model so the whole suite runs offline."""

    def __init__(self, answers: list[str] | list[Exception]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_parse_accepts_plain_and_fenced_json():
    plain = parse_llm_answer(VALID)
    fenced = parse_llm_answer(f"```json\n{VALID}\n```")

    assert plain.severity is Severity.CRITICAL
    assert plain.confidence == 0.82
    assert plain.source == "llm"
    assert fenced.root_cause == plain.root_cause


def test_parse_rejects_answers_we_cannot_trust():
    for bad in [
        "not json at all",
        '["a", "list"]',
        '{"root_cause": "x", "workaround": "y", "severity": "critical"}',  # missing confidence
        '{"root_cause": "x", "workaround": "y", "severity": "apocalyptic", "confidence": 0.5}',
        '{"root_cause": "", "workaround": "y", "severity": "critical", "confidence": 0.5}',
        '{"root_cause": "x", "workaround": "y", "severity": "critical", "confidence": "high"}',
    ]:
        with pytest.raises(TriageError):
            parse_llm_answer(bad)


def test_parse_clamps_out_of_range_confidence():
    answer = VALID.replace("0.82", "4.5")

    assert parse_llm_answer(answer).confidence == 1.0


def test_llm_triage_repairs_once_then_succeeds():
    transport = FakeTransport(["I think it is probably memory related?", VALID])

    triage = llm_triage("java.lang.OutOfMemoryError: Java heap space", transport)

    assert triage.source == "llm"
    assert len(transport.calls) == 2
    assert "rejected" in transport.calls[1][1]


def test_llm_triage_gives_up_after_the_repair_attempt():
    transport = FakeTransport(["garbage", "still garbage"])

    with pytest.raises(TriageError, match="rejected after 2 attempts"):
        llm_triage("anything", transport)


def test_triage_failure_falls_back_when_the_model_is_unavailable():
    transport = FakeTransport([ConnectionError("api is down")])

    triage, notes = triage_failure("job", "java.lang.OutOfMemoryError: Java heap space", transport=transport)

    assert triage.source == "rules"
    assert any("fell back to rules" in note for note in notes)


def test_offline_mode_never_calls_the_model():
    transport = FakeTransport([VALID])

    triage, notes = triage_failure(
        "job", "AnalysisException: cannot resolve 'customer_zip_code'", transport=transport, offline=True
    )

    assert triage.source == "rules"
    assert transport.calls == []
    assert any("rules (signature: schema_change)" in note for note in notes)


def test_triage_failure_uses_the_model_when_it_answers_well():
    transport = FakeTransport([VALID])

    triage, notes = triage_failure("job", "java.lang.OutOfMemoryError", transport=transport)

    assert triage.source == "llm"
    assert any("triaged by model" in note for note in notes)
