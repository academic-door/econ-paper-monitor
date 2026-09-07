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
- Test: `tests/test_monitor_cadence_contract.py`

**Interfaces:**
- Consumes: current workflow text contracts.
- Produces: failing assertions that define the approved topology before production YAML changes.

- [ ] **Step 1: Add writer-topology assertions**

Add a test equivalent to:

```python
def test_writer_lanes_render_inline_without_child_render_dispatch(self) -> None:
    for workflow in ("fast-discovery.yml", "update.yml", "ai-enrichment.yml"):
        text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        self.assertIn("group: paper-monitor-main-writer", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("bash scripts/render_published_site.sh", text)
        self.assertNotIn("gh workflow run render-site.yml", text)
```

Add a standalone Render contract equivalent to:

```python
def test_standalone_render_is_operator_lane_not_data_push_child(self) -> None:
    text = (ROOT / ".github" / "workflows" / "render-site.yml").read_text(encoding="utf-8")
    self.assertIn("group: paper-monitor-main-writer", text)
    self.assertIn("bash scripts/render_published_site.sh", text)
    self.assertNotIn('- "data/**"', text)
```

- [ ] **Step 2: Add Fast fallback assertions**

Extend `test_watchdog_is_fallback_not_second_hourly_lane` with:

```python
for status in ("pending", "requested", "queued", "waiting", "in_progress"):
    self.assertIn(f'.status == "{status}"', text)
self.assertIn('.conclusion == "success"', text)
self.assertIn("FAST_MAX_AGE_MINUTES=40", text)
self.assertIn("gh workflow run fast-discovery.yml", text)
```

- [ ] **Step 3: Add render-script syntax test**

Add:

```python
def test_render_publisher_shell_syntax(self) -> None:
    script = ROOT / "scripts" / "render_published_site.sh"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)
```

and import `subprocess`.

- [ ] **Step 4: Run the focused test and verify RED**

Run:

```bash
python -m pytest -q tests/test_monitor_cadence_contract.py
```

Expected: failures for the missing shared render script / inline calls / watchdog Fast contract, with no unrelated test errors.

- [ ] **Step 5: Commit RED contract**

```bash
git add tests/test_monitor_cadence_contract.py
git commit -m "test(monitor): define inline writer render topology"
```

---

### Task 2: Extract the shared render publisher and simplify standalone Render

**Files:**
- Create: `scripts/render_published_site.sh`
- Modify: `.github/workflows/render-site.yml`
- Test: `tests/test_monitor_cadence_contract.py`

**Interfaces:**
- Consumes: repository root working tree, configured Git credentials, Python 3.11, current canonical `data/daily/YYYY-MM-DD.json`.
- Produces: generated `docs/**` commit pushed to `main` when docs change; exit code `0` when unchanged or published successfully.

- [ ] **Step 1: Create `scripts/render_published_site.sh`**

Use this structure:

```bash
#!/usr/bin/env bash
set -euo pipefail

DAILY_DATE="$(python - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
print(datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"))
PY
)"

python - "$DAILY_DATE" <<'PY'
import json
import sys
from pathlib import Path
path = Path("data/daily") / f"{sys.argv[1]}.json"
if not path.exists():
    raise SystemExit(f"canonical daily file is missing: {path}")
payload = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
    raise SystemExit(f"canonical daily file is not a list of records: {path}")
print(f"Canonical input validated: {path} ({len(payload)} records)")
PY

rm -rf docs/paper-index
python scripts/render_site.py
python scripts/build_daily_vnext.py --date "$DAILY_DATE" --output docs/index.html --report "$RUNNER_TEMP/daily-home-report.json"
python scripts/build_daily_vnext.py --date "$DAILY_DATE" --output docs/daily-vnext/index.html --report "$RUNNER_TEMP/daily-vnext-report.json"
python scripts/build_feed.py --site-url https://academic-door.github.io/econ-paper-monitor/
python scripts/render_semantic_scholar_usage.py
```

Then reproduce the existing Render workflow output-boundary assertions exactly, followed by:

```bash
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add docs
if git diff --cached --quiet; then
  echo "No generated site changes to commit."
  exit 0
fi

git commit -m "Render paper monitor site"
for attempt in 1 2 3; do
  if git pull --rebase -X theirs origin main && git push origin HEAD:main; then
    exit 0
  fi
  git rebase --abort || true
  echo "Render publish attempt ${attempt} failed; retrying."
  sleep $((attempt * 8))
done

echo "Failed to publish generated site after retries."
exit 1
```

- [ ] **Step 2: Replace standalone Render body with the shared script**

Keep checkout, Python setup, `contents: write`, and:

```yaml
concurrency:
  group: paper-monitor-main-writer
  cancel-in-progress: false
```

Change push paths to rendering implementation only:

```yaml
on:
  push:
    branches: [main]
    paths:
      - "scripts/render_published_site.sh"
      - "scripts/build_daily_vnext.py"
      - "scripts/display_contract.py"
      - "scripts/render_site.py"
      - "scripts/build_feed.py"
      - "scripts/render_semantic_scholar_usage.py"
      - "scripts/templates/daily_vnext.html"
      - ".github/workflows/render-site.yml"
  workflow_dispatch:
```

The job implementation becomes:

```yaml
- name: Render and publish site
  run: bash scripts/render_published_site.sh
```

- [ ] **Step 3: Run shell syntax and focused tests**

```bash
bash -n scripts/render_published_site.sh
python -m pytest -q tests/test_monitor_cadence_contract.py
```

Expected: standalone Render and shell-syntax assertions pass; writer-inline and watchdog assertions remain RED until later tasks.

- [ ] **Step 4: Commit**

```bash
git add scripts/render_published_site.sh .github/workflows/render-site.yml tests/test_monitor_cadence_contract.py
git commit -m "refactor(render): extract serialized site publisher"
```

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

- [ ] **Step 1: Replace child Render dispatch in all three workflows**

Replace the current `Trigger display render` step with:

```yaml
- name: Render published site inline
  if: steps.commit.outputs.changed == 'true'
  run: bash scripts/render_published_site.sh
```

Do this in Update, Fast, and AI.

- [ ] **Step 2: Remove no-longer-required action-dispatch permission**

Fast and AI permissions become:

```yaml
permissions:
  contents: write
```

Update permissions become:

```yaml
permissions:
  contents: write
  issues: write
```

- [ ] **Step 3: Run focused topology tests**

```bash
python -m pytest -q tests/test_monitor_cadence_contract.py
```

Expected: inline writer topology assertions pass. Watchdog fallback assertions may remain RED until Task 4.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/update.yml .github/workflows/fast-discovery.yml .github/workflows/ai-enrichment.yml tests/test_monitor_cadence_contract.py
git commit -m "fix(monitor): render inside canonical writer jobs"
```

---

### Task 4: Add success-based Fast watchdog fallback

**Files:**
- Modify: `.github/workflows/watchdog.yml`
- Test: `tests/test_monitor_cadence_contract.py`

**Interfaces:**
- Consumes: GitHub Actions run history for `fast-discovery.yml` through `gh run list`.
- Produces: `steps.fast.outputs.stale` and `steps.fast.outputs.active`; dispatches one Fast workflow only when stale and inactive.

- [ ] **Step 1: Add Fast run-history check after local CNKI freshness**

Use one query:

```bash
FAST_MAX_AGE_MINUTES=40
if ! RUNS="$(gh run list \
  --repo "${{ github.repository }}" \
  --workflow fast-discovery.yml \
  --limit 30 \
  --json createdAt,updatedAt,status,conclusion 2>/tmp/fast-run-list.err)"; then
  echo "::warning::Unable to query Fast Discovery history; skipping Fast fallback this watchdog run."
  cat /tmp/fast-run-list.err || true
  echo "stale=false" >> "$GITHUB_OUTPUT"
  echo "active=unknown" >> "$GITHUB_OUTPUT"
  exit 0
fi

ACTIVE="$(printf '%s' "$RUNS" | jq '[.[] | select(
  .status == "pending" or
  .status == "requested" or
  .status == "queued" or
  .status == "waiting" or
  .status == "in_progress"
)] | length')"
LATEST_SUCCESS="$(printf '%s' "$RUNS" | jq -r '[.[] | select(.conclusion == "success")] | sort_by(.updatedAt) | last | .updatedAt // ""')"
```

If `LATEST_SUCCESS` is empty, set `stale=true`. Otherwise parse it with `date -u -d`, compute age in minutes, and set stale when age is at least 40. Timestamp parse failure must set `stale=false` and warn.

- [ ] **Step 2: Add bounded dispatch**

```yaml
- name: Dispatch fast discovery fallback
  if: steps.fast.outputs.stale == 'true' && steps.fast.outputs.active == '0'
  env:
    GH_TOKEN: ${{ github.token }}
  run: |
    gh workflow run fast-discovery.yml \
      --repo "${{ github.repository }}" \
      --ref main
```

Add a companion skip step when stale but active is nonzero/unknown.

- [ ] **Step 3: Run focused tests**

```bash
python -m pytest -q tests/test_monitor_cadence_contract.py
```

Expected: all cadence/topology tests pass.

- [ ] **Step 4: Run full regression locally/CI-equivalent**

```bash
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/watchdog.yml tests/test_monitor_cadence_contract.py
git commit -m "fix(monitor): recover stale Fast discovery from watchdog"
```

---

### Task 5: PR gate, exact-head merge, and production acceptance

**Files:**
- Verify all files above.
- No new implementation files unless a gate exposes a defect.

**Interfaces:**
- Consumes: GitHub PR mergeability, exact-head Actions, natural Fast/Watchdog/Update production runs.
- Produces: merged topology with evidence-backed acceptance status.

- [ ] **Step 1: Open PR from `fix/inline-writer-render-topology` to `main`**

PR body must record the observed Fast cancellation chain and state that #165 is superseded.

- [ ] **Step 2: Verify changed-file boundary**

Expected changed files only:

```text
.github/workflows/update.yml
.github/workflows/fast-discovery.yml
.github/workflows/ai-enrichment.yml
.github/workflows/render-site.yml
.github/workflows/watchdog.yml
scripts/render_published_site.sh
tests/test_monitor_cadence_contract.py
docs/superpowers/specs/2026-09-07-inline-writer-render-topology-design.md
docs/superpowers/plans/2026-09-07-inline-writer-render-topology.md
```

- [ ] **Step 3: Require exact-head Test Paper Monitor success**

Do not merge on partial step success. Require the workflow conclusion `success` for the current PR head SHA.

- [ ] **Step 4: Re-read execution-time main and PR mergeability**

If main advanced, ensure GitHub reports the PR mergeable and no incompatible writer/render workflow change landed after the branch base.

- [ ] **Step 5: Squash merge with expected head SHA**

Use exact-head merge protection.

- [ ] **Step 6: Verify merge-head regression/public smoke**

Require post-merge Test/Render/Public/Browser evidence applicable to the merge head.

- [ ] **Step 7: Verify first post-merge Fast execution**

Acceptance requires a Fast run that receives a real job. It must not repeat the prior `created -> zero jobs -> cancelled` pattern.

If the run changes canonical data, confirm the same Fast job executes `Render published site inline` successfully and no child `Render Paper Monitor Site` workflow is created by that Fast run.

- [ ] **Step 8: Verify watchdog non-duplication**

After a successful Fast run, inspect the next Watchdog run. It must see recent success and avoid a redundant Fast fallback.

- [ ] **Step 9: Verify BIS source restoration on a post-#164 working-paper fetch**

Read durable `data/status.json`. `working-paper:bis-working-papers` must no longer report the historical HTTP 404 before BIS restoration is marked production-healthy.
