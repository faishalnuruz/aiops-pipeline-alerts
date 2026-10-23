"""Render alerts as Slack Block Kit messages and deliver them."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from aiops_alerts.logs import redact
from aiops_alerts.models import Alert, Severity

SEVERITY_META = {
    Severity.CRITICAL: ("🔴", "Critical"),
    Severity.WARNING: ("🟡", "Warning"),
    Severity.INFO: ("🔵", "Info"),
}


class WebhookTransport(Protocol):
    def post(self, url: str, payload: dict) -> int: ...


class UrlLibTransport:
    """Posts the payload and returns the HTTP status."""

    def post(self, url: str, payload: dict) -> int:  # pragma: no cover - network path
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def build_slack_blocks(alert: Alert) -> list[dict]:
    """Build the message a human will actually read at 3am."""
    emoji, label = SEVERITY_META[alert.triage.severity]
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} {alert.job_name} failed — {label}"},
        },
        _section(
            f"*Owner:* <@{alert.owner.slack_id}> ({alert.owner.team})\n"
            f"*Diagnosis by:* {alert.triage.source}"
            f" · *confidence:* {alert.triage.confidence:.0%}\n"
            f"*Detected:* {alert.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
        ),
    ]

    if alert.suppressed:
        blocks.append(_section(
            f":warning: _Repeat of an alert sent "
            f"{alert.related_to or 'recently'} — grouped to avoid alert fatigue._"
        ))

    blocks.extend([
        _section(f"*Likely root cause*\n{alert.triage.root_cause}"),
        _section(f"*Suggested workaround*\n{alert.triage.workaround}"),
    ])

    if alert.log_excerpt.strip():
        # Defence in depth: the pipeline redacts before building the alert, and the
        # renderer redacts again, because Slack is the last place a secret can leak.
        excerpt = redact("\n".join(alert.log_excerpt.strip().splitlines()[-6:])[:600])[0]
        blocks.append(_section(f"*Last log lines*\n```{excerpt}```"))

    blocks.extend([
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"signature `{alert.signature}` · occurrence #{alert.occurrences}",
            }],
        },
    ])
    return blocks


def build_payload(alert: Alert, channel: str | None = None) -> dict:
    payload = {"text": f"{alert.job_name} failed — {alert.triage.severity.value}", "blocks": build_slack_blocks(alert)}
    if channel:
        payload["channel"] = channel
    return payload


def post_alert(
    alert: Alert, webhook_url: str, transport: WebhookTransport | None = None, channel: str | None = None
) -> int:
    sender = transport or UrlLibTransport()
    return sender.post(webhook_url, build_payload(alert, channel=channel))
