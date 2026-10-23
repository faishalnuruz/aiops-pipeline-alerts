"""Command line entry point.

    python -m aiops_alerts.cli --job coros_trail_running_etl \
        --log-file samples/logs/spark_oom.log

Dry run by default: the rendered payload is printed so you can review exactly
what would reach the channel before anything is sent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aiops_alerts import routing
from aiops_alerts import triage as triage_module
from aiops_alerts.pipeline import handle_failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", required=True, help="pipeline or DAG name that failed")
    parser.add_argument("--log-file", type=Path, required=True, help="file containing the failure log")
    parser.add_argument("--config", type=Path, default=routing.DEFAULT_CONFIG, help="routing config")
    parser.add_argument("--state", type=Path, default=Path(".state/alert_history.json"))
    parser.add_argument("--cooldown", type=int, default=30, help="grouping window in minutes")
    parser.add_argument("--tail", type=int, default=200, help="log lines to consider")
    parser.add_argument("--use-llm", action="store_true", help="use Gemini for triage (needs GEMINI_API_KEY)")
    parser.add_argument("--model", default="gemini-2.0-flash")
    parser.add_argument("--send", action="store_true", help="deliver to Slack (needs SLACK_WEBHOOK_URL)")
    parser.add_argument("--channel", default=None, help="override the webhook's default channel")
    parser.add_argument("--payload-only", action="store_true", help="print only the Slack payload")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.log_file.exists():
        print(f"error: log file not found: {args.log_file}", file=sys.stderr)
        return 2

    transport = None
    if args.use_llm:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            print("error: --use-llm needs GEMINI_API_KEY in the environment", file=sys.stderr)
            return 2
        transport = triage_module.GeminiTransport(api_key, model=args.model)

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if args.send and not webhook_url:
        print("error: --send needs SLACK_WEBHOOK_URL in the environment", file=sys.stderr)
        return 2

    result = handle_failure(
        args.job,
        args.log_file.read_text(),
        config_path=args.config,
        state_path=args.state,
        cooldown_minutes=args.cooldown,
        tail_lines=args.tail,
        transport=transport,
        webhook_url=webhook_url,
        dry_run=not args.send,
        channel=args.channel,
    )

    if args.payload_only:
        print(json.dumps(result.payload, indent=2))
        return 0

    print(result.summary)
    for note in result.notes:
        print(f"  - {note}")
    print(json.dumps(result.payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
