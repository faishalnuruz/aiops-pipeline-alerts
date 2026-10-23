from datetime import UTC, datetime

from aiops_alerts.models import Alert, Owner, Severity, Triage
from aiops_alerts.notify import build_payload, build_slack_blocks, post_alert


def block_text(blocks: list[dict]) -> str:
    """Flatten every block, including context elements, into one string."""
    parts: list[str] = []
    for block in blocks:
        if "text" in block:
            parts.append(block["text"].get("text", ""))
        for element in block.get("elements", []):
            parts.append(element.get("text", ""))
    return "\n".join(parts)


def make_alert(**overrides) -> Alert:
    values = {
        "job_name": "coros_trail_running_etl",
        "triage": Triage(
            root_cause="A Spark executor ran out of memory during a skewed shuffle.",
            workaround="Salt the skewed key and re-run only the failed stage.",
            severity=Severity.CRITICAL,
            confidence=0.78,
            source="llm",
        ),
        "owner": Owner("U0FAISHAL", "Sports Analytics"),
        "signature": "9f8e7d6c5b4a",
        "created_at": datetime(2026, 10, 20, 2, 31, tzinfo=UTC),
        "occurrences": 3,
    }
    values.update(overrides)
    return Alert(**values)


def test_blocks_lead_with_job_severity_and_owner():
    blocks = build_slack_blocks(make_alert())

    header = blocks[0]["text"]["text"]
    body = blocks[1]["text"]["text"]

    assert "coros_trail_running_etl" in header
    assert "Critical" in header
    assert "<@U0FAISHAL>" in body
    assert "Sports Analytics" in body


def test_blocks_carry_the_diagnosis_and_provenance():
    blocks = build_slack_blocks(make_alert())
    text = block_text(blocks)

    assert "skewed shuffle" in text
    assert "Salt the skewed key" in text
    assert "llm" in text
    assert "78%" in text
    assert "9f8e7d6c5b4a" in text
    assert "occurrence #3" in text


def test_suppressed_alert_explains_why_it_is_quiet():
    blocks = build_slack_blocks(make_alert(suppressed=True, related_to="12 minutes ago"))
    text = block_text(blocks)

    assert "alert fatigue" in text
    assert "12 minutes ago" in text


def test_payload_includes_plain_text_fallback_and_channel():
    payload = build_payload(make_alert(), channel="#data-alerts")

    assert payload["channel"] == "#data-alerts"
    assert "coros_trail_running_etl" in payload["text"]
    assert payload["blocks"]


def test_blocks_include_the_redacted_log_excerpt():
    alert = make_alert(log_excerpt="ERROR executor 3 lost\ntoken=ghp_9f8e7d6c5b4a3f2e1d0c\nERROR Pipeline failed")

    text = block_text(build_slack_blocks(alert))

    assert "Last log lines" in text
    assert "ERROR Pipeline failed" in text
    assert "ghp_" not in text or "[REDACTED]" in text


def test_excerpt_is_capped_so_the_message_stays_readable():
    alert = make_alert(log_excerpt="\n".join(f"line {index}" for index in range(40)))

    excerpt_block = next(block for block in build_slack_blocks(alert) if "Last log lines" in str(block))

    assert len(excerpt_block["text"]["text"]) < 900


class FakeWebhook:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, payload: dict) -> int:
        self.calls.append((url, payload))
        return self.status


def test_post_alert_uses_the_injected_transport():
    webhook = FakeWebhook(status=200)

    status = post_alert(make_alert(), "https://hooks.slack.com/services/T/B/X", transport=webhook)

    assert status == 200
    assert len(webhook.calls) == 1
    url, payload = webhook.calls[0]
    assert url.endswith("/X")
    assert payload["blocks"][0]["type"] == "header"
