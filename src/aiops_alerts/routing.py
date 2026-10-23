"""Decide who gets paged.

Routing is configuration, not code: ``config/pic_mapping.json`` maps pipeline
names (or prefixes) to owners, with an on-call fallback so an alert never lands
in a channel where nobody is responsible.
"""

from __future__ import annotations

import json
from pathlib import Path

from aiops_alerts.models import Owner

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "pic_mapping.json"


class RoutingError(RuntimeError):
    pass


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise RoutingError(f"routing config not found: {config_path}")
    with config_path.open() as handle:
        config = json.load(handle)
    if "default" not in config:
        raise RoutingError("routing config must define a 'default' owner")
    return config


def _owner_from(entry: dict) -> Owner:
    return Owner(
        slack_id=entry["slack_id"],
        team=entry.get("team", "unknown"),
        oncall=bool(entry.get("oncall", False)),
    )


def resolve_owner(job_name: str, config: dict) -> tuple[Owner, str]:
    """Resolve an owner by exact match, then longest prefix, then default."""
    pipelines = config.get("pipelines", {})
    if job_name in pipelines:
        return _owner_from(pipelines[job_name]), "exact match"

    matches = [name for name in pipelines if job_name.startswith(name)]
    if matches:
        best = max(matches, key=len)
        return _owner_from(pipelines[best]), f"prefix match on {best!r}"

    return _owner_from(config["default"]), "default owner"
