# Inline Writer Render Topology Design

Status: APPROVED by Daily Door Subsystem Brain on 2026-09-07.

## Problem

`Update Paper Monitor`, `Fast Discovery`, `AI Enrichment`, and `Render Paper Monitor Site` all use the same GitHub Actions concurrency group:

```yaml
concurrency:
  group: paper-monitor-main-writer
  cancel-in-progress: false
```

The intent is correct: only one workflow may mutate `main` at a time. The current composition is not correct because the three data writers finish by dispatching `render-site.yml`, which creates another workflow in the same concurrency group.

Production evidence on 2026-09-07 shows the failure mode:

- Fast Discovery run `34153910520` was created by schedule at `19:00:44Z`.
- It never received a job and was cancelled at `19:04:17Z`.
- Update Paper Monitor run `34152888410` held the shared writer group until `19:04:16Z`.
- That Update run dispatched Render Paper Monitor Site run `34154144951` at `19:04:15Z`.
- The later Render became the pending member of the shared concurrency group and the already-pending Fast run was cancelled.

This means `cancel-in-progress: false` protects the running writer but does not provide FIFO queueing for pending writers. Routine child Render dispatch can starve Fast or AI before those workflows ever start, so the existing selective-writer publish fix cannot receive production acceptance reliably.

## Goals

1. Preserve `paper-monitor-main-writer` as the single serialization boundary for every workflow that commits to `main`.
2. Eliminate routine child Render workflows from Update, Fast, and AI writers.
3. Render and publish `docs/**` inside the currently-running writer job after canonical data has been published.
4. Keep one standalone Render workflow for operator use and rendering-code changes without making it the routine data-publish path.
5. Add Fast watchdog fallback for genuine schedule-delivery degradation without treating cancelled or failed runs as freshness evidence.
6. Reduce unnecessary `actions: write` permissions from data writers once they no longer dispatch Render.

## Non-goals

- Do not change Fast, Update, AI, or Watchdog schedule cadence.
- Do not introduce a second canonical writer lock.
- Do not change canonical Daily data schemas, source ownership, or AI provider routing.
- Do not make Render concurrent with canonical writers.
- Do not use Watchdog as a second normal Fast lane.

## Architecture

### 1. Shared in-job render publisher

Create `scripts/render_published_site.sh` as the single implementation of the current Render workflow body. It runs inside an already-serialized writer job and performs:

1. Validate the current Beijing canonical daily input.
2. Resolve Beijing date.
3. Remove the generated lazy paper index.
4. Run `scripts/render_site.py`.
5. Build `docs/index.html` and `docs/daily-vnext/index.html` from the same canonical Daily input.
6. Build `docs/feed.xml`.
7. Render the Semantic Scholar usage page.
8. Verify the public output boundary and Daily vNext/classic/detail contracts currently enforced by `render-site.yml`.
9. Stage only `docs/**`.
10. If docs changed, commit `Render paper monitor site` and publish with bounded fetch/rebase/push retry.
11. If docs did not change, exit successfully without a commit.

The script assumes Python is already installed by the caller, which is true for Update, Fast, AI, and standalone Render.

### 2. Routine writers render inline

`update.yml`, `fast-discovery.yml`, and `ai-enrichment.yml` retain their existing canonical-data commit/publish steps. When `steps.commit.outputs.changed == 'true'`, they call:

```bash
bash scripts/render_published_site.sh
```

in the same job, before the shared writer lock is released.

They no longer execute:

```bash
gh workflow run render-site.yml
```

and therefore no longer need `actions: write` solely for display rendering.

Data publication remains durable even if the following inline render fails. In that case the writer run is failed visibly after the data commit, while the already-published canonical data is preserved. A later writer or manual standalone Render can repair display freshness.

### 3. Standalone Render becomes an operator/render-code lane

`render-site.yml` keeps `paper-monitor-main-writer` and `workflow_dispatch`, but it delegates implementation to `scripts/render_published_site.sh`.

Its automatic `push` trigger no longer includes `data/**`. Routine data writers now render inline, and leaving `data/**` would recreate a second Render workflow after non-routine data pushes that can compete for the one pending concurrency slot.

Automatic standalone Render remains for changes to rendering implementation/contracts, including:

- `scripts/render_published_site.sh`
- `scripts/build_daily_vnext.py`
- `scripts/display_contract.py`
- `scripts/render_site.py`
- `scripts/build_feed.py`
- `scripts/render_semantic_scholar_usage.py`
- `scripts/templates/daily_vnext.html`
- `.github/workflows/render-site.yml`

Direct/non-routine data pushes are rendered by the next serialized writer or by manual `workflow_dispatch`.

### 4. Fast schedule fallback remains bounded

The existing Monitor Watchdog remains on its independent `paper-monitor-watchdog` group and keeps current core/full fallback semantics.

Add a Fast-specific check with `FAST_MAX_AGE_MINUTES=40`:

- Query recent `fast-discovery.yml` runs.
- Treat `pending`, `requested`, `queued`, `waiting`, and `in_progress` as active.
- Freshness is based on the most recent **successful completed** Fast run, not merely the newest run creation time.
- A cancelled, failed, timed-out, skipped, or otherwise non-successful run does not reset freshness.
- If no successful Fast run exists within 40 minutes and no Fast run is active, dispatch one `workflow_dispatch` Fast run.
- If GitHub run-history lookup fails or timestamps cannot be parsed, fail closed for this watchdog iteration: warn and do not dispatch.

This fallback addresses schedule delivery degradation only. It must not compensate for writer-topology starvation; the inline-render topology removes that root cause.

## Permissions

After routine child Render dispatch is removed:

- Fast Discovery: `contents: write` only.
- AI Enrichment: `contents: write` only.
- Update Paper Monitor: retain `contents: write` and `issues: write`; remove `actions: write` unless another current step demonstrably requires it.
- Monitor Watchdog retains `actions: write` and `contents: read` because it dispatches fallback workflows.
- Standalone Render retains `contents: write`.

## Tests

Extend `tests/test_monitor_cadence_contract.py` with static topology contracts:

- all canonical data writers still use `paper-monitor-main-writer` and `cancel-in-progress: false`;
- Update/Fast/AI contain `bash scripts/render_published_site.sh` behind their data-change gate;
- Update/Fast/AI do not contain `gh workflow run render-site.yml`;
- Update/Fast/AI no longer declare `actions: write`;
- standalone Render uses the shared render script;
- standalone Render does not trigger on `data/**`;
- watchdog contains the 40-minute Fast fallback;
- watchdog considers `pending`, `requested`, `queued`, `waiting`, and `in_progress` active;
- watchdog filters freshness to `conclusion == success`.

Migrate the existing `tests/test_display_layer_boundary.py` executable contract from the old topology to the approved topology:

- standalone Render must **not** listen to `data/**`;
- standalone Render delegates to `scripts/render_published_site.sh`;
- the shared publisher stages/commits only `docs/**` and never stages canonical `data/**`;
- Update stages canonical data and invokes the shared publisher inline;
- Update must not dispatch `render-site.yml` as a child workflow;
- neither side may use a `docs/**` push trigger that can form a render loop.

Add a focused test that runs `bash -n scripts/render_published_site.sh` so shell syntax is part of CI.

Full authoritative regression remains `python -m pytest -q` plus the existing Daily vNext build/contract steps in `Test Paper Monitor`.

## Acceptance

Code-level acceptance requires all of the following:

1. Exact-head `Test Paper Monitor` is green.
2. PR is mergeable against execution-time `main` with no unrelated file changes.
3. Exact-head squash merge.
4. Merge-head regression/smoke workflows are green.

Production acceptance requires:

1. A post-merge natural or fallback Fast run is created and actually receives a job instead of being cancelled while pending.
2. The Fast run reaches its canonical commit step successfully under the existing selective-writer cleanup/rebase protocol.
3. If Fast changes canonical data, the same Fast job runs the inline render step and publishes docs without creating a child Render workflow.
4. A later Watchdog iteration sees a recent successful Fast run and does not create a redundant fallback.
5. A post-#164 Update/working-paper fetch updates `working-paper:bis-working-papers` without the historical HTTP 404 before BIS source restoration is marked production-healthy.

## Rollback

Revert the topology PR. The previous standalone Render workflow remains recoverable from Git history, and canonical data commits are independent of generated docs commits.
