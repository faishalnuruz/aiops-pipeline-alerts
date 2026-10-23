import json

import pytest

from aiops_alerts.routing import RoutingError, load_config, resolve_owner

CONFIG = {
    "default": {"slack_id": "U0ONCALL", "team": "Data Platform", "oncall": True},
    "pipelines": {
        "coros_trail_running_etl": {"slack_id": "U0FAISHAL", "team": "Sports"},
        "olist_": {"slack_id": "U0COMMERCE", "team": "Commerce"},
        "olist_gold_daily_sales": {"slack_id": "U0GOLD", "team": "Commerce"},
    },
}


def test_exact_match_wins_over_prefix():
    owner, reason = resolve_owner("olist_gold_daily_sales", CONFIG)

    assert owner.slack_id == "U0GOLD"
    assert reason == "exact match"


def test_prefix_match_routes_related_pipelines_together():
    owner, reason = resolve_owner("olist_orders_ingest", CONFIG)

    assert owner.slack_id == "U0COMMERCE"
    assert "prefix match" in reason


def test_unknown_pipeline_goes_to_the_default_on_call():
    owner, reason = resolve_owner("some_new_pipeline", CONFIG)

    assert owner.slack_id == "U0ONCALL"
    assert owner.oncall is True
    assert reason == "default owner"


def test_load_config_requires_a_default_owner(tmp_path):
    path = tmp_path / "no_default.json"
    path.write_text(json.dumps({"pipelines": {}}))

    with pytest.raises(RoutingError, match="default"):
        load_config(path)


def test_load_config_reports_a_missing_file(tmp_path):
    with pytest.raises(RoutingError, match="not found"):
        load_config(tmp_path / "absent.json")
