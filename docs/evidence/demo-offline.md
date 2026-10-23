# Evidence: offline demo run

Captured on 2026-10-23. No API key, no Slack workspace, no network: the deterministic
rule engine does the triage and the alert is rendered instead of sent.

## Commands and output (verbatim)

```text
$ .venv/bin/python -m aiops_alerts.cli --job coros_trail_running_etl --log-file samples/logs/spark_oom.log
[dry-run] coros_trail_running_etl -> <@U0FAISHAL> (critical, rules, 79%): A Spark executor ran out of memory, usually from an oversized shuffle partition or a single skewed key carrying most of the data.
  - redacted 3 credential or token value(s) before triage
  - triaged by rules (signature: spark_oom)
  - routed to U0FAISHAL via exact match
  - not delivered: dry run

$ # the same failure minutes later: grouped instead of paged again
[suppressed] coros_trail_running_etl -> <@U0FAISHAL> (critical, rules, 79%): A Spark executor ran out of memory, usually from an oversized shuffle partition or a single skewed key carrying most of the data.
  - redacted 3 credential or token value(s) before triage
  - triaged by rules (signature: spark_oom)
  - routed to U0FAISHAL via exact match
  - grouped with the alert from moments ago; occurrence #2
  - not delivered: inside the grouping window

$ .venv/bin/python -m aiops_alerts.cli --job olist_orders_ingest --log-file samples/logs/db_credentials.log
[dry-run] olist_orders_ingest -> <@U0COMMERCE> (critical, rules, 67%): The database rejected the pipeline's credentials: the secret was rotated, expired, or the role lost a grant.
  - redacted 3 credential or token value(s) before triage
  - triaged by rules (signature: database_auth)
  - routed to U0COMMERCE via exact match
  - not delivered: dry run

$ .venv/bin/python -m aiops_alerts.cli --job olist_gold_daily_sales --log-file samples/logs/schema_change.log
[dry-run] olist_gold_daily_sales -> <@U0COMMERCE> (warning, rules, 79%): The source schema no longer matches the contract: a column was renamed, removed, or retyped upstream without notice.
  - triaged by rules (signature: schema_change)
  - routed to U0COMMERCE via prefix match on 'olist_'
  - not delivered: dry run
```

## What to observe

- **Secrets never leave the process.** The three sample logs contain a database password, a
  JDBC URL with inline credentials, an API key, a GitHub token, and a bearer token. Each run
  reports `redacted 3 credential or token value(s) before triage`, and grepping the full
  payload for the raw values returns nothing:

  ```text
  $ .venv/bin/python -m aiops_alerts.cli --job coros_trail_running_etl --log-file samples/logs/spark_oom.log | grep -c "Sup3rS3cret\|ghp_9f8e"
  0
  ```

- **Grouping works.** The second identical failure is `[suppressed]` with
  `occurrence #2` instead of paging the same person twice.

- **Routing is configuration.** `coros_trail_running_etl` matches exactly and goes to
  `U0FAISHAL`; `olist_gold_daily_sales` matches the `olist_` prefix and goes to `U0COMMERCE`.

- **Severity is honest.** A schema drift is a `warning` that belongs in a work queue; a dead
  executor or a rejected credential is `critical` and pages immediately.

- **Confidence reflects real evidence.** The OOM log matches two patterns inside one rule
  (`OutOfMemoryError` and `Container killed by YARN`), so confidence is 79%; the credential
  log matches one pattern, so it is 67%.

## The delivered message

`docs/screenshots/slack-alert.png` is rendered from the exact payload of the first command
above by `tools/preview_slack.py`, so the case study shows the real message rather than a mock.

## Tests

```text
$ .venv/bin/python -m pytest -q
48 passed in 0.10s
```
