"""Deterministic triage rules.

This is the offline path and the safety net: when the model is unavailable, too
slow, or returns something unparseable, the alert still goes out with an
actionable summary. Every rule is a known failure signature with the fix a
senior engineer would suggest first.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiops_alerts.models import Severity, Triage


@dataclass(frozen=True)
class Rule:
    name: str
    patterns: tuple[str, ...]
    severity: Severity
    root_cause: str
    workaround: str


RULES: tuple[Rule, ...] = (
    Rule(
        name="spark_oom",
        patterns=(
            "OutOfMemoryError",
            "Container killed by YARN for exceeding memory",
            "GC overhead limit exceeded",
        ),
        severity=Severity.CRITICAL,
        root_cause=(
            "A Spark executor ran out of memory, usually from an oversized shuffle partition "
            "or a single skewed key carrying most of the data."
        ),
        workaround=(
            "Check the stage's partition sizes in the Spark UI. If one task dwarfs the rest, "
            "salt the join key or repartition; otherwise raise spark.executor.memory or reduce "
            "the partition size with spark.sql.shuffle.partitions."
        ),
    ),
    Rule(
        name="database_auth",
        patterns=(
            "Invalid credentials",
            "password authentication failed",
            "Access denied for user",
            "401 Unauthorized",
            "permission denied for",
        ),
        severity=Severity.CRITICAL,
        root_cause=(
            "The database rejected the pipeline's credentials: the secret was rotated, expired, "
            "or the role lost a grant."
        ),
        workaround=(
            "Rotate the credential in the secret manager, confirm the reader role still has "
            "SELECT on the source schema, then re-run the failed task rather than the whole DAG."
        ),
    ),
    Rule(
        name="schema_change",
        patterns=(
            "cannot resolve",
            "AnalysisException",
            "Unknown column",
            "column not found",
            "does not exist in the schema",
            "SchemaChangeException",
        ),
        severity=Severity.WARNING,
        root_cause=(
            "The source schema no longer matches the contract: a column was renamed, removed, "
            "or retyped upstream without notice."
        ),
        workaround=(
            "Diff the incoming schema against the contract file, update the contract and the "
            "transformation, and backfill the affected partition."
        ),
    ),
    Rule(
        name="skew_or_shuffle_failure",
        patterns=("FetchFailedException", "shuffle fetch failure", "Task failed while writing rows"),
        severity=Severity.CRITICAL,
        root_cause=(
            "Shuffle data could not be fetched, typically an executor dying under skew or "
            "losing its local disk."
        ),
        workaround=(
            "Re-run the failed stage alone. If it repeats, look for skewed keys in the shuffle "
            "and enable adaptive query execution or salting."
        ),
    ),
    Rule(
        name="network_timeout",
        patterns=("TimeoutError", "timed out after", "Connection reset by peer", "ETIMEDOUT"),
        severity=Severity.WARNING,
        root_cause=(
            "A network call to an upstream service exceeded its timeout; often a slow or "
            "overloaded dependency rather than a code defect."
        ),
        workaround=(
            "Check the dependency's latency and error rate for the same window, then re-run. "
            "If it is a known-flaky source, add a bounded retry with backoff."
        ),
    ),
    Rule(
        name="rate_limit",
        patterns=("rateLimitExceeded", "Too many requests", "429", "quota exceeded"),
        severity=Severity.WARNING,
        root_cause=(
            "An API refused the request because the pipeline exhausted its quota, usually from "
            "a retry storm or a backfill running alongside the scheduled load."
        ),
        workaround=(
            "Throttle the job or move the backfill outside the production window, and add "
            "exponential backoff so retries stop amplifying load."
        ),
    ),
    Rule(
        name="disk_space",
        patterns=("No space left on device", "Disk quota exceeded"),
        severity=Severity.CRITICAL,
        root_cause="A worker ran out of local disk, typically from spilled shuffle data or unmanaged logs.",
        workaround=(
            "Clear the worker's temp directory, lower the shuffle spill footprint, and set a "
            "retention policy on logs before re-running."
        ),
    ),
    Rule(
        name="code_bug",
        patterns=("TypeError", "KeyError", "AttributeError", "NameError", "ValueError"),
        severity=Severity.WARNING,
        root_cause=(
            "The transformation hit an unexpected value or shape, which usually means a recent "
            "code change assumed something about the data that is not true."
        ),
        workaround=(
            "Reproduce with the failing partition locally, patch the assumption, and add a "
            "contract test so the same shape fails fast next time."
        ),
    ),
)

FALLBACK = Rule(
    name="unclassified",
    patterns=(),
    severity=Severity.WARNING,
    root_cause="No known failure signature matched this log, so the cause needs a human look.",
    workaround=(
        "Read the excerpt above, check what changed since the last green run (deploys, upstream "
        "releases, credential rotations), then re-run the failed task rather than the whole DAG."
    ),
)


def classify(text: str) -> tuple[Triage, str]:
    """Match the log against known signatures; always returns something useful."""
    matched: list[Rule] = [rule for rule in RULES if any(p in text for p in rule.patterns)]
    if not matched:
        return Triage(
            root_cause=FALLBACK.root_cause,
            workaround=FALLBACK.workaround,
            severity=FALLBACK.severity,
            confidence=0.3,
            source="rules",
        ), FALLBACK.name

    # More evidence means a more confident classification: each extra pattern that
    # fires within the primary rule, plus any additional rule that matched.
    primary = matched[0]
    matched_patterns = sum(1 for pattern in primary.patterns if pattern in text)
    confidence = min(0.9, 0.55 + 0.12 * matched_patterns + 0.05 * (len(matched) - 1))
    triage = Triage(
        root_cause=primary.root_cause,
        workaround=primary.workaround,
        severity=primary.severity,
        confidence=round(confidence, 2),
        source="rules",
    )
    return triage, primary.name
