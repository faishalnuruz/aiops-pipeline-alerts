"""Shared types for the alert pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class Severity(str, Enum):
    """Alert severity, ordered from most to least urgent."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Owner:
    """Who is responsible for a pipeline."""

    def __init__(self, slack_id: str, team: str, oncall: bool = False) -> None:
        self.slack_id = slack_id
        self.team = team
        self.oncall = oncall

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Owner({self.slack_id}, {self.team}, oncall={self.oncall})"


@dataclass
class Triage:
    """The structured verdict for one failure."""

    root_cause: str
    workaround: str
    severity: Severity
    confidence: float
    source: str  # "llm" or "rules"


@dataclass
class Alert:
    """A ready-to-send alert, with everything the notification needs."""

    job_name: str
    triage: Triage
    owner: Owner
    signature: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    suppressed: bool = False
    related_to: str | None = None
    occurrences: int = 1
    log_excerpt: str = ""

    @property
    def slack_blocks(self) -> list[dict]:
        """Render the alert as Slack Block Kit blocks."""
        from aiops_alerts.notify import build_slack_blocks

        return build_slack_blocks(self)
