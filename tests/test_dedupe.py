import json
from datetime import UTC, datetime, timedelta

from aiops_alerts.dedupe import Deduplicator, signature

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_first_sighting_is_never_suppressed(tmp_path):
    tracker = Deduplicator(tmp_path / "state.json")

    decision = tracker.check("abc", now=NOW)

    assert decision.suppressed is False
    assert decision.occurrences == 1


def test_repeat_inside_the_cooldown_is_grouped(tmp_path):
    tracker = Deduplicator(tmp_path / "state.json", cooldown_minutes=30)
    tracker.record("abc", delivered=True, now=NOW)

    decision = tracker.check("abc", now=NOW + timedelta(minutes=5))

    assert decision.suppressed is True
    assert decision.occurrences == 2
    assert decision.last_sent == NOW


def test_repeat_after_the_cooldown_pages_again(tmp_path):
    tracker = Deduplicator(tmp_path / "state.json", cooldown_minutes=30)
    tracker.record("abc", delivered=True, now=NOW)

    decision = tracker.check("abc", now=NOW + timedelta(minutes=45))

    assert decision.suppressed is False
    assert decision.occurrences == 2


def test_a_suppressed_alert_does_not_extend_the_cooldown(tmp_path):
    """Otherwise a pipeline failing every minute would never page again."""
    tracker = Deduplicator(tmp_path / "state.json", cooldown_minutes=30)
    tracker.record("abc", delivered=True, now=NOW)
    tracker.record("abc", delivered=False, now=NOW + timedelta(minutes=10))

    assert tracker.check("abc", now=NOW + timedelta(minutes=35)).suppressed is False


def test_corrupt_state_file_never_blocks_an_alert(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{ this is not json")

    decision = Deduplicator(state).check("abc", now=NOW)

    assert decision.suppressed is False


def test_state_file_is_human_readable(tmp_path):
    state = tmp_path / "state.json"
    tracker = Deduplicator(state)
    tracker.record("abc", delivered=True, now=NOW)

    stored = json.loads(state.read_text())

    assert stored["abc"]["occurrences"] == 1
    assert stored["abc"]["last_sent"] == NOW.isoformat()


def test_signature_is_stable_and_pipeline_specific():
    first = signature("etl_orders", "OutOfMemoryError on shuffle")
    second = signature("etl_orders", "OutOfMemoryError on shuffle")
    other_job = signature("etl_customers", "OutOfMemoryError on shuffle")

    assert first == second
    assert first != other_job
    assert len(first) == 12
