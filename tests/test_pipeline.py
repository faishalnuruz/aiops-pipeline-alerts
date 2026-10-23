import json
from pathlib import Path

from aiops_alerts.models import Severity
from aiops_alerts.pipeline import handle_failure

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "logs"
CONFIG = Path(__file__).resolve().parents[1] / "config" / "pic_mapping.json"

VALID_ANSWER = (
    '{"root_cause": "The executor ran out of memory while sorting a skewed shuffle partition.",'
    ' "workaround": "Salt the join key and re-run the failed stage.",'
    ' "severity": "critical", "confidence": 0.82}'
)


class FakeModel:
    def __init__(self, answer: str = VALID_ANSWER) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.answer


class FakeWebhook:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict] = []

    def post(self, url: str, payload: dict) -> int:
        self.calls.append(payload)
        return self.status


def run(log_name: str, tmp_path, **overrides):
    job = overrides.pop("job", "coros_trail_running_etl")
    values = {
        "config_path": CONFIG,
        "state_path": tmp_path / "history.json",
        "offline": True,
        "dry_run": True,
    }
    values.update(overrides)
    return handle_failure(job, (SAMPLES / log_name).read_text(), **values)


def test_end_to_end_offline_run_produces_a_routable_alert(tmp_path):
    result = run("spark_oom.log", tmp_path)

    assert result.alert.triage.severity is Severity.CRITICAL
    assert result.alert.triage.source == "rules"
    assert result.alert.owner.slack_id == "U0FAISHAL"
    assert result.sent is False
    assert any("dry run" in note for note in result.notes)
    assert "coros_trail_running_etl failed" in result.payload["text"]


def test_credentials_never_reach_the_outgoing_payload(tmp_path):
    result = run("db_credentials.log", tmp_path, job="olist_orders_ingest")
    rendered = json.dumps(result.payload)

    assert result.redactions >= 2
    for secret in ("Sup3rS3cret", "Winter2026", "sk-live-4c1f9a7b2e8d6f3a"):
        assert secret not in rendered

    # The words the diagnosis relies on are still there.
    assert "password authentication failed" in rendered


def test_repeat_failure_is_grouped_instead_of_paging_twice(tmp_path):
    first = run("spark_oom.log", tmp_path)
    second = run("spark_oom.log", tmp_path)

    assert first.alert.suppressed is False
    assert second.alert.suppressed is True
    assert second.alert.occurrences == 2
    assert second.sent is False
    assert any("grouped with the alert" in note for note in second.notes)

    text = json.dumps(second.payload)
    assert "alert fatigue" in text


def test_webhook_is_called_when_sending_is_enabled(tmp_path):
    webhook = FakeWebhook(status=200)

    result = run(
        "schema_change.log",
        tmp_path,
        job="olist_gold_daily_sales",
        dry_run=False,
        webhook_url="https://hooks.slack.com/services/T/B/X",
        webhook_transport=webhook,
    )

    assert result.sent is True
    assert result.status_code == 200
    assert len(webhook.calls) == 1
    assert result.alert.owner.slack_id == "U0COMMERCE"
    assert result.alert.triage.severity is Severity.WARNING


def test_a_failed_webhook_does_not_silence_the_next_alert(tmp_path):
    """A notification nobody received must not start a grouping window."""
    webhook = FakeWebhook(status=500)

    result = run(
        "spark_oom.log", tmp_path, dry_run=False,
        webhook_url="https://hooks.slack.com/services/T/B/X", webhook_transport=webhook,
    )
    follow_up = run("spark_oom.log", tmp_path)

    assert result.sent is False
    assert result.status_code == 500
    assert "webhook responded 500" in " ".join(result.notes)
    assert follow_up.alert.suppressed is False


def test_model_triage_is_used_when_a_transport_is_configured(tmp_path):
    model = FakeModel()

    result = run("spark_oom.log", tmp_path, offline=False, transport=model)

    assert result.alert.triage.source == "llm"
    assert result.alert.triage.confidence == 0.82
    assert len(model.calls) == 1
    # The excerpt handed to the model must already be redacted.
    assert "Sup3rS3cret" not in model.calls[0][1]


def test_model_failure_still_produces_an_alert(tmp_path):
    class ExplodingModel:
        def complete(self, system: str, user: str) -> str:
            raise ConnectionError("vendor outage")

    result = run("spark_oom.log", tmp_path, offline=False, transport=ExplodingModel())

    assert result.alert.triage.source == "rules"
    assert any("fell back to rules" in note for note in result.notes)
    assert result.payload["blocks"]


def test_summary_reads_like_a_log_line(tmp_path):
    result = run("spark_oom.log", tmp_path)

    assert result.summary.startswith("[dry-run] coros_trail_running_etl -> <@U0FAISHAL>")
    assert "critical" in result.summary
