# Architecture and decisions

## Flow

```text
pipeline failure (Airflow on_failure_callback, CLI, or any caller)
        |
        v
  log preparation  ---> tail (200 lines) + redact secrets
        |
        v
     triage  ---------> model answer?  ---> strict JSON schema check
        |                    |                     |
        |                    |                     +--> reject --> one repair attempt
        |                    |                                        |
        |                    +--> unavailable / still invalid --------+
        |                                                             v
        +---------------------------------------------> rule engine (8 signatures)
        |
        v
     routing  ---------> exact name -> prefix -> default on-call
        |
        v
  de-duplication  -----> same signature inside cooldown? -> group as repeat
        |
        v
   Slack Block Kit  ---> header, owner, diagnosis, workaround, redacted excerpt, signature
```

## Decisions worth defending

**The rule engine is a first-class path, not a stub.** Most "AI alerting" demos cannot run
without a key, which also means they cannot be tested or trusted during an incident when the
vendor is down. Here the rules produce a genuinely useful diagnosis, and the model is an
optional upgrade. That also makes the offline demo in the README possible.

**The model is treated as an unreliable dependency.** Its output is validated against a schema,
gets exactly one repair attempt, and then the rules answer. Retrying harder would raise
mean-time-to-recovery, which is the metric this project exists to improve. The message records
provenance (`Diagnosis by: rules|llm`, confidence) so a reader can calibrate how much to trust
it.

**Redaction happens at two boundaries.** Once in the pipeline before the log is handed to a
model, and again in the renderer before it is sent to Slack. The second pass exists because the
renderer is the last gate before a secret becomes public in a chat channel; defence in depth is
cheaper than an incident.

**De-duplication keys on a normalized error line.** Run ids, task numbers, timestamps, and
numeric values differ between runs of the same bug, so the signature strips them:
`ERROR Task <n> in stage <n>.<n> failed <n> times` is one signature across every occurrence. The
alternative — hashing the whole log — would never group anything.

**A failed delivery does not start a grouping window.** Grouping is a promise that someone was
told. If the webhook returned 500, nobody was told, and the next failure must page again. This
is the one place where being conservative about silence matters more than being quiet.

**Dry run is the default.** Delivery is an explicit `--send`. Reviewing the exact payload before
it reaches a channel is part of the workflow, and `tools/preview_slack.py` renders it as a
message for that purpose.

**Transports are injected.** The LLM client and the webhook client are parameters, so the whole
suite runs offline with fakes while still exercising parsing, repair, fallback, and payload
construction.

## Failure modes

| What breaks | What happens |
| --- | --- |
| Model API is down or slow | Rules triage the log; the message says `Diagnosis by: rules` |
| Model returns prose or invalid JSON | Answer rejected, one repair attempt, then rules |
| Log contains credentials | Redacted before triage, and again before rendering |
| Routing config is missing or lacks `default` | `RoutingError` — fail loudly rather than guess an owner |
| Pipeline name is unknown | Routed to the default on-call owner, so it still reaches a human |
| State file is corrupt | Treated as empty; an alert is never blocked by bookkeeping |
| Webhook returns 4xx/5xx | Reported in the run notes, no grouping window started |
| Failure log has no known signature | `unclassified` at 30% confidence with a human-investigation workaround |
