"""Wiring the alert handler into Airflow.

Documentation-as-code: the test suite does not import this file and Airflow is
not a project dependency, so the library stays installable and testable
anywhere. Copy this pattern into your own DAG.

The key idea: ``on_failure_callback`` fires on any task failure, reads the
task's own log, and hands it to the same handler the CLI uses.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.context import Context

from aiops_alerts.pipeline import handle_failure

DAG_ID = "coros_trail_running_etl"


def _read_task_log(context: Context) -> str:
    """Prefer the task's real log; fall back to the exception Airflow gives us."""
    task_instance = context.get("task_instance")
    candidates = [
        getattr(task_instance, "log_filepath", None),
        getattr(getattr(task_instance, "log", None), "filepath", None),
    ]
    for candidate in candidates:
        if candidate and Path(str(candidate)).exists():
            return Path(str(candidate)).read_text()
    exception = context.get("exception")
    return str(exception) if exception else "task failed without a readable log"


def on_failure(context: Context) -> None:
    """Send one routed, de-duplicated alert for the failed task."""
    result = handle_failure(
        job_name=context["dag"].dag_id,
        log_text=_read_task_log(context),
        state_path=Path(os.environ.get("ALERT_STATE", ".state/alert_history.json")),
        offline=not os.environ.get("GEMINI_API_KEY"),
        webhook_url=os.environ.get("SLACK_WEBHOOK_URL"),
        dry_run=os.environ.get("ALERTS_DRY_RUN", "0") == "1",
        channel=os.environ.get("SLACK_CHANNEL"),
    )
    print(result.summary)
    for note in result.notes:
        print(f"  - {note}")


default_args = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": on_failure,
}

with DAG(
    DAG_ID,
    default_args=default_args,
    description="Flatten COROS trail-running telemetry into Gold marts",
    start_date=datetime(2026, 1, 1),
    schedule="0 2 * * *",
    catchup=False,
    tags=["sports", "etl"],
) as dag:
    extract = PythonOperator(task_id="extract_coros_exports", python_callable=lambda: None)
    flatten = PythonOperator(task_id="flatten_telemetry", python_callable=lambda: None)
    load = PythonOperator(task_id="load_gold_marts", python_callable=lambda: None)

    extract >> flatten >> load
