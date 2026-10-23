"""Group repeat failures so the channel stays readable.

Alert fatigue is the problem this project exists to solve, so a pipeline that
fails every five minutes with the same root cause should page once — and then
report itself as a repeat until the cooldown expires.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_COOLDOWN_MINUTES = 30


@dataclass
class Decision:
    """What the pipeline should do with this alert."""

    suppressed: bool
    occurrences: int
    first_seen: datetime | None = None
    last_sent: datetime | None = None

    @property
    def minutes_since_last_send(self) -> int | None:
        if self.last_sent is None:
            return None
        delta = datetime.now(UTC) - self.last_sent
        return int(delta.total_seconds() // 60)


def signature(job_name: str, error_key: str) -> str:
    """A stable key for 'this failure, on this pipeline'."""
    digest = hashlib.sha1(f"{job_name}::{error_key}".encode()).hexdigest()
    return digest[:12]


class Deduplicator:
    """File-backed state so suppression survives between DAG runs."""

    def __init__(self, state_path: Path | str, cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES) -> None:
        self.state_path = Path(state_path)
        self.cooldown = timedelta(minutes=cooldown_minutes)

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text() or "{}")
        except json.JSONDecodeError:
            # A corrupt state file must never block an alert.
            return {}

    def _save(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    def check(self, key: str, now: datetime | None = None) -> Decision:
        now = now or datetime.now(UTC)
        entry = self._load().get(key)
        if not entry:
            return Decision(suppressed=False, occurrences=1)

        last_sent = datetime.fromisoformat(entry["last_sent"]) if entry.get("last_sent") else None
        first_seen = datetime.fromisoformat(entry["first_seen"]) if entry.get("first_seen") else None
        occurrences = int(entry.get("occurrences", 1)) + 1

        suppressed = last_sent is not None and (now - last_sent) < self.cooldown
        return Decision(
            suppressed=suppressed,
            occurrences=occurrences,
            first_seen=first_seen,
            last_sent=last_sent,
        )

    def record(self, key: str, delivered: bool, now: datetime | None = None) -> Decision:
        """Persist the sighting.

        Only a delivered alert refreshes the grouping window: if the webhook
        failed, the next run should page again rather than stay quiet because
        of a notification nobody received.
        """
        now = now or datetime.now(UTC)
        state = self._load()
        entry = state.get(key, {})
        decision = self.check(key, now=now)

        entry["first_seen"] = entry.get("first_seen") or now.isoformat()
        entry["occurrences"] = decision.occurrences
        entry["last_seen"] = now.isoformat()
        if delivered:
            entry["last_sent"] = now.isoformat()
        state[key] = entry
        self._save(state)

        return Decision(
            suppressed=decision.suppressed,
            occurrences=decision.occurrences,
            first_seen=datetime.fromisoformat(entry["first_seen"]),
            last_sent=datetime.fromisoformat(entry["last_sent"]) if entry.get("last_sent") else None,
        )
