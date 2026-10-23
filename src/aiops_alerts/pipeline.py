"""Turn a failed pipeline log into a routed, de-duplicated alert."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aiops_alerts import dedupe, notify, routing
from aiops_alerts import triage as triage_module
from aiops_alerts.logs import error_signature, prepare_log_excerpt
from aiops_alerts.models import Alert


def _humanize_minutes(minutes: int | None) -> str:
    """Turn a cooldown delta into something readable in a channel."""
    if minutes is None:
        return "recently"
    if minutes < 1:
        return "moments ago"
    if minutes == 1:
        return "1 minute ago"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    return "1 hour ago" if hours == 1 else f"{hours} hours ago"


@dataclass
class HandleResult:
    """Everything the caller needs to log, assert, or send."""

    alert: Alert
    payload: dict
    notes: list[str] = field(default_factory=list)
    sent: bool = False
    status_code: int | None = None
    redactions: int = 0

    @property
    def summary(self) -> str:
        state = "suppressed" if self.alert.suppressed else ("sent" if self.sent else "dry-run")
        return (
            f"[{state}] {self.alert.job_name} -> <@{self.alert.owner.slack_id}> "
            f"({self.alert.triage.severity.value}, {self.alert.triage.source}, "
            f"{self.alert.triage.confidence:.0%}): {self.alert.triage.root_cause}"
        )


def handle_failure(
    job_name: str,
    log_text: str,
    *,
    config_path: Path | str = routing.DEFAULT_CONFIG,
    state_path: Path | str = ".state/alert_history.json",
    cooldown_minutes: int = dedupe.DEFAULT_COOLDOWN_MINUTES,
    tail_lines: int = 200,
    transport: triage_module.LLMTransport | None = None,
    offline: bool = False,
    webhook_url: str | None = None,
    webhook_transport: notify.WebhookTransport | None = None,
    channel: str | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> HandleResult:
    """Diagnose, route, group, and (optionally) deliver one failure alert."""
    now = now or datetime.now(UTC)
    notes: list[str] = []

    excerpt, redactions = prepare_log_excerpt(log_text, lines=tail_lines)
    if redactions:
        notes.append(f"redacted {redactions} credential or token value(s) before triage")

    verdict, triage_notes = triage_module.triage_failure(
        job_name, excerpt, transport=transport, offline=offline
    )
    notes.extend(triage_notes)

    config = routing.load_config(config_path)
    owner, route_reason = routing.resolve_owner(job_name, config)
    notes.append(f"routed to {owner.slack_id} via {route_reason}")

    key = dedupe.signature(job_name, error_signature(excerpt) or verdict.root_cause)
    tracker = dedupe.Deduplicator(state_path, cooldown_minutes=cooldown_minutes)
    decision = tracker.check(key, now=now)

    alert = Alert(
        job_name=job_name,
        triage=verdict,
        owner=owner,
        signature=key,
        created_at=now,
        suppressed=decision.suppressed,
        related_to=_humanize_minutes(decision.minutes_since_last_send)
        if decision.suppressed
        else None,
        occurrences=decision.occurrences,
        log_excerpt=excerpt,
    )
    if decision.suppressed:
        notes.append(
            f"grouped with the alert from {alert.related_to}; occurrence #{decision.occurrences}"
        )

    payload = notify.build_payload(alert, channel=channel)

    status_code: int | None = None
    sent = False
    if not decision.suppressed and not dry_run and webhook_url:
        status_code = notify.post_alert(alert, webhook_url, transport=webhook_transport, channel=channel)
        sent = 200 <= status_code < 300
        notes.append(f"webhook responded {status_code}")
    elif decision.suppressed:
        notes.append("not delivered: inside the grouping window")
    else:
        notes.append("not delivered: dry run")

    # A dry run simulates delivery, so it should exercise the grouping window too;
    # a real attempt that failed must not, or a broken webhook would silence the
    # next failure as well.
    delivered = sent or (dry_run and not decision.suppressed)
    tracker.record(key, delivered=delivered, now=now)
    return HandleResult(
        alert=alert, payload=payload, notes=notes, sent=sent, status_code=status_code, redactions=redactions
    )
