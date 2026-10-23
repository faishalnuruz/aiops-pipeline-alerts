import pytest

from aiops_alerts.logs import error_signature, extract_tail, prepare_log_excerpt, redact


def test_extract_tail_keeps_only_the_end():
    text = "\n".join(f"line {index}" for index in range(500))

    tail = extract_tail(text, lines=10)

    assert len(tail.splitlines()) == 10
    assert tail.splitlines()[0] == "line 490"


def test_extract_tail_rejects_non_positive_limits():
    with pytest.raises(ValueError):
        extract_tail("anything", lines=0)


def test_redact_removes_key_value_credentials():
    text = 'password=Sup3rS3cret! api_key="sk-live-123" Authorization: Bearer abcdef123456'

    redacted, count = redact(text)

    assert "Sup3rS3cret" not in redacted
    assert "sk-live-123" not in redacted
    assert "abcdef123456" not in redacted
    assert count >= 3


def test_redact_removes_connection_strings_aws_keys_and_jwts():
    text = (
        "jdbc:postgresql://etl:Winter2026@warehouse.internal:5432/olist "
        "AKIA0123456789ABCDEF "
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    )

    redacted, count = redact(text)

    assert "Winter2026" not in redacted
    assert "AKIA0123456789ABCDEF" not in redacted
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert count == 3


def test_redact_keeps_the_failure_signal_intact():
    """Redaction must not destroy the words the diagnosis depends on."""
    text = "psycopg2.OperationalError: FATAL:  password authentication failed for user \"etl_reader\""

    redacted, _ = redact(text)

    assert "password authentication failed" in redacted
    assert "etl_reader" in redacted


def test_error_signature_is_stable_across_runs():
    first = "2026-10-20 02:31:44 ERROR Task 87 in stage 14.0 failed 4 times (id 8f3c1a7d)"
    second = "2026-10-21 09:12:01 ERROR Task 12 in stage 9.0 failed 2 times (id 2b19c4ee)"

    assert error_signature(first) == error_signature(second)
    assert "<n>" in error_signature(first)


def test_error_signature_differs_between_failure_types():
    oom = "java.lang.OutOfMemoryError: Java heap space"
    auth = "FATAL: password authentication failed for user \"etl_reader\""

    assert error_signature(oom) != error_signature(auth)


def test_prepare_log_excerpt_tails_and_redacts_in_one_step():
    text = "\n".join(["noise"] * 50 + ["token=ghp_9f8e7d6c5b4a3f2e1d0c", "ERROR failed to push metrics"])

    excerpt, count = prepare_log_excerpt(text, lines=5)

    assert count == 1
    assert "ghp_9f8e7d6c5b4a3f2e1d0c" not in excerpt
    assert "ERROR failed to push metrics" in excerpt
