# Daily Door external Monitor Watchdog trigger

Authority: Parent Decision 0021, `main-control#89`, owner incident `econ-paper-monitor#302`.

## Contract

This isolated Cloudflare Cron Worker is a trigger only. Every 15 minutes (UTC
`:04/:19/:34/:49`, offset from native Watchdog `:11/:26/:41/:56`) it dispatches
only `academic-door/econ-paper-monitor/.github/workflows/watchdog.yml` on
`main`. There is no HTTP fetch handler, external Fast/Update dispatch, data
payload, publication logic, arbitrary input, or shared trigger runtime.

The owner Watchdog retains all Fast/Update stale detection, decisions, concurrency,
and recovery. Native GitHub schedule remains enabled as redundancy. Watchdog
concurrency is serialized without cancelling an active run, to avoid collision
between GitHub and Cloudflare triggers. Do not weaken 45m/45m/90m.

## Secret / deployment

Create a fine-grained GitHub token with repository selection **only**
`academic-door/econ-paper-monitor`, repository permission **Actions: read/write**
and no other writable or organization-wide permissions. Set its value only as a
Cloudflare Worker secret named `GITHUB_ACTIONS_DISPATCH_TOKEN`; never place it
in this repository, Github Actions secrets, Wrangler `vars`, chats, logs,
or PR comments.

The new `deploy-daily-watchdog-trigger.yml` runs on a push of this isolated
Worker's source/config, using already configured repository
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` for Cloudflare deployment.
It does not deploy the separate Presence Worker.

If account permissions require manual secret placement, from this directory
in an authenticated Wrangler session:

```sh
npx wrangler@latest secret put GITHUB_ACTIONS_DISPATCH_TOKEN
```

This prompts interactively; do not put the value on the command line. Confirm
the Worker has the configured Cron and a secret binding (never disclose value).

## Evidence and acceptance

Worker scheduled logs emit `DAILY_WATCHDOG_EXTERNAL_TICK` with UTC scheduled_at,
fixed target and HTTP 204. GitHub Watchdog logs the incoming
`WATCHDOG_TRIGGER_SOURCE=cloudflare_cron_claimed`,
`WATCHDOG_EXTERNAL_SCHEDULED_AT`, and `WATCHDOG_RUN_ID`.

**An input claim, a manual dispatch, or HTTP 204 alone does not prove a real
Cloudflare scheduled invocation.** Correlate actual Cloudflare Cron event logs
with GitHub `workflow_dispatch` event, matching tick, run id, and success,
then read downstream Fast/Update recovery, release gate, CNKI freshness and
product integrity. Only close #302 after real live scheduled proof + effective
45m/45m/90m freshness, with coexistence with native schedule.

Run focused contract/tests:
`python -m pytest -q tests/test_daily_watchdog_external_trigger_contract.py`.

Rollback: disable the external Cron or revoke its repo-scoped token; native
GitHub schedule remains available. No source-truth, threshold, or publication
semantics are changed.
