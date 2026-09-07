# Inline Writer Render Topology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep one canonical writer lock while rendering inside the active writer job so routine Render workflows cannot replace pending Fast/AI runs.

**Architecture:** A shared `scripts/render_published_site.sh` owns the existing render/build/verify/docs-publish sequence. Update, Fast, and AI call it inline after durable data publication; standalone Render delegates to the same script but no longer triggers on `data/**`. Monitor Watchdog adds a bounded Fast fallback based on the age of the most recent successful Fast run and the absence of any active Fast run.

**Tech Stack:** GitHub Actions YAML, Bash, Python 3.11, unittest/pytest, Git/GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-09-07-inline-writer-render-topology-design.md`

## Global Constraints

- `paper-monitor-main-writer` remains the only concurrency group for workflows that commit to `main`.
- `cancel-in-progress: false` remains on canonical writer workflows.
- Fast/Update/AI schedules are unchanged.
- Watchdog schedule remains `*/15 * * * *`.
- No canonical data schema, source ownership, or AI provider routing change.
- Watchdog Fast fallback threshold is exactly `40` minutes.
- Failed/cancelled Fast runs are not freshness evidence.
- Direct data publication remains durable even if subsequent inline rendering fails.

---

### Task 1: Add RED topology and watchdog contracts

**Files:**
- Modify: `tests/test_monitor_cadence_contract.py`

**Interfaces:**
- Consumes: current workflow text contracts.
- Produces: failing assertions that define the approved topology before production YAML changes.

- [ ] Add assertions that Update/Fast/AI retain `paper-monitor-main-writer` + `cancel-in-progress: false`, invoke `bash scripts/render_published_site.sh`, do not invoke `gh workflow run render-site.yml`, and do not declare unnecessary `actions: write`.
- [ ] Add standalone Render assertions requiring delegation to `scripts/render_published_site.sh` and forbidding a `data/**` trigger.
- [ ] Add Fast fallback assertions for `FAST_MAX_AGE_MINUTES=40`, active statuses `pending/requested/queued/waiting/in_progress`, and success-only freshness (`.conclusion == "success"`).
- [ ] Add `bash -n scripts/render_published_site.sh` syntax coverage.
- [ ] Run authoritative regression and require a clean RED caused only by the missing approved topology.

Observed RED evidence on PR #166 head `ad3a453b5eb93176e1d044c70435a96af5cdfcb4`: `542 passed / 4 failed / 32 subtests passed`; the four failures were exactly missing shared render script, old standalone Render topology, missing Fast fallback, and child Render dispatch.

---

### Task 2: Extract the shared render publisher and migrate display-layer contracts

**Files:**
- Create: `scripts/render_published_site.sh`
- Modify: `.github/workflows/render-site.yml`
- Modify: `tests/test_display_layer_boundary.py`
- Test: `tests/test_monitor_cadence_contract.py`

**Interfaces:**
- Consumes: repository root working tree, configured Git credentials, Python 3.11, current canonical `data/daily/YYYY-MM-DD.json`.
- Produces: generated `docs/**` commit pushed to `main` when docs change; exit code `0` when unchanged or published successfully.

- [ ] Create `scripts/render_published_site.sh` with `set -euo pipefail`.
- [ ] Resolve Beijing date and validate the canonical Daily input is an existing list of record objects.
- [ ] Reproduce the existing rendering sequence: clear lazy paper index, render secondary pages, build root + Daily vNext from one canonical input, build feed, render Semantic Scholar usage.
- [ ] Reproduce the existing public output-boundary assertions exactly.
- [ ] Stage only `docs/**`; no-op if unchanged; otherwise commit `Render paper monitor site` and publish with three bounded fetch/rebase/push attempts.
- [ ] Simplify `render-site.yml` to checkout main, set up Python, and run the shared publisher.
- [ ] Remove `data/**` from standalone Render push paths; retain render implementation/contract paths and `workflow_dispatch`.
- [ ] Migrate `tests/test_display_layer_boundary.py` from the old `data/** -> standalone Render` contract to the approved topology: standalone Render does not listen to data; shared publisher stages docs only; Update stages data and calls publisher inline; Update does not dispatch child Render; no `docs/**` trigger loop.

During GREEN verification, exact-head regression exposed the two old display boundary assertions as the only failures (`544 passed / 2 failed / 32 subtests passed`). They were stale contracts for the topology being intentionally replaced, not production implementation regressions; the test migration above is therefore part of the approved architectural change.

---

### Task 3: Make Update, Fast, and AI render inside their writer jobs

**Files:**
- Modify: `.github/workflows/update.yml`
- Modify: `.github/workflows/fast-discovery.yml`
- Modify: `.github/workflows/ai-enrichment.yml`
- Test: `tests/test_monitor_cadence_contract.py`

**Interfaces:**
- Consumes: `steps.commit.outputs.changed` from each existing canonical-data commit step and `scripts/render_published_site.sh` from Task 2.
- Produces: one serialized workflow execution that publishes canonical data, renders docs, and only then releases `paper-monitor-main-writer`.

- [ ] Replace each `Trigger display render` child-dispatch step with:

```yaml
- name: Render published site inline
  if: steps.commit.outputs.changed == 'true'
  run: bash scripts/render_published_site.sh
```

- [ ] Remove `actions: write` from Fast and AI, retaining `contents: write`.
- [ ] Remove `actions: write` from Update, retaining `contents: write` + `issues: write`.
- [ ] Preserve existing data commit/publish logic, including the #160 selective-writer cleanup/rebase protocol for Fast and AI.
- [ ] Inspect PR patches to verify Update/Fast/AI diffs contain no unrelated discovery/source/data behavior changes.

---

### Task 4: Add success-based Fast watchdog fallback

**Files:**
- Modify: `.github/workflows/watchdog.yml`
- Test: `tests/test_monitor_cadence_contract.py`

**Interfaces:**
- Consumes: GitHub Actions run history for `fast-discovery.yml` through `gh run list`.
- Produces: `steps.fast.outputs.stale` and `steps.fast.outputs.active`; dispatches one Fast workflow only when stale and inactive.

- [ ] Query up to 30 recent Fast runs with `createdAt,updatedAt,status,conclusion`.
- [ ] On query failure, warn, set `stale=false`, set `active=unknown`, and do not dispatch.
- [ ] Treat `pending`, `requested`, `queued`, `waiting`, and `in_progress` as active.
- [ ] Use the most recent `.conclusion == "success"` run `updatedAt` as freshness evidence.
- [ ] If no successful run exists, mark stale; if timestamp parsing fails, fail closed (`stale=false`).
- [ ] Mark stale when the latest success is at least 40 minutes old.
- [ ] Dispatch `fast-discovery.yml --ref main` only when `stale == true` and `active == 0`.
- [ ] Preserve all existing core/full watchdog semantics.

---

### Task 5: PR gate, exact-head merge, and production acceptance

**Files:**
- Verify only the approved topology/contract files below.
- No additional implementation files unless an exact gate exposes a real defect.

**Expected changed files:**

```text
.github/workflows/update.yml
.github/workflows/fast-discovery.yml
.github/workflows/ai-enrichment.yml
.github/workflows/render-site.yml
.github/workflows/watchdog.yml
scripts/render_published_site.sh
tests/test_monitor_cadence_contract.py
tests/test_display_layer_boundary.py
docs/superpowers/specs/2026-09-07-inline-writer-render-topology-design.md
docs/superpowers/plans/2026-09-07-inline-writer-render-topology.md
```

- [ ] Confirm PR changed-file boundary exactly matches the ten files above.
- [ ] Require exact-head `Test Paper Monitor` conclusion `success`; partial step success is insufficient.
- [ ] Require applicable exact-head Browser/Public smoke success.
- [ ] Re-read execution-time `main`, PR head, and mergeability immediately before merge.
- [ ] If main advanced, verify no incompatible writer/render workflow changes landed after PR base.
- [ ] Squash merge with expected-head protection.
- [ ] Verify merge-head regression/smoke workflows.

**Production acceptance:**

- [ ] A post-merge natural or watchdog-fallback Fast run is created and receives a real job; it must not repeat `created -> zero jobs -> cancelled`.
- [ ] Fast reaches its canonical commit step successfully under the #160 selective-writer protocol.
- [ ] If Fast changes canonical data, the same Fast job executes `Render published site inline` successfully and does not create a child `Render Paper Monitor Site` workflow.
- [ ] The next Watchdog run treats a recent successful Fast as fresh and does not dispatch a redundant Fast fallback.
- [ ] A post-#164 working-paper fetch updates `working-paper:bis-working-papers` without the historical HTTP 404 before BIS restoration is marked production-healthy.
