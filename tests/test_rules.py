from aiops_alerts.models import Severity
from aiops_alerts.rules import classify


def test_out_of_memory_log_is_critical_and_actionable():
    triage, rule = classify("java.lang.OutOfMemoryError: Java heap space\n    at ExternalSorter.scala:190")

    assert rule == "spark_oom"
    assert triage.severity is Severity.CRITICAL
    assert "memory" in triage.workaround.lower()
    assert triage.source == "rules"


def test_credential_failure_maps_to_the_database_auth_rule():
    triage, rule = classify('FATAL:  password authentication failed for user "etl_reader"')

    assert rule == "database_auth"
    assert triage.severity is Severity.CRITICAL
    assert "secret manager" in triage.workaround


def test_schema_change_is_a_warning_not_a_page():
    triage, rule = classify("AnalysisException: cannot resolve 'o.customer_zip_code' given input columns")

    assert rule == "schema_change"
    assert triage.severity is Severity.WARNING


def test_unknown_failure_falls_back_to_a_honest_answer():
    triage, rule = classify("something we have never seen before")

    assert rule == "unclassified"
    assert triage.severity is Severity.WARNING
    assert triage.confidence <= 0.3
    assert "no known failure signature matched" in triage.root_cause.lower()


def test_more_matching_signatures_raise_confidence():
    single, _ = classify("java.lang.OutOfMemoryError: Java heap space")
    multiple, _ = classify(
        "java.lang.OutOfMemoryError: Java heap space\n"
        "Container killed by YARN for exceeding memory limits"
    )

    assert multiple.confidence > single.confidence
