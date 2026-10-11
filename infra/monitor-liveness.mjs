export const WORKFLOW_FRESHNESS = Object.freeze([
  Object.freeze({ name: "Monitor Watchdog", maxAgeMinutes: 45 }),
  Object.freeze({ name: "Fast Discovery", maxAgeMinutes: 45 }),
  Object.freeze({ name: "Update Paper Monitor", maxAgeMinutes: 90 }),
]);

function parseCreatedAt(run) {
  const value = Date.parse(String(run?.created_at || ""));
  return Number.isFinite(value) ? value : null;
}

export function latestWorkflowPageTimestamp(html) {
  const values = [];
  const pattern = /<(?:relative-time|time-ago)\b[^>]*\bdatetime=["']([^"']+)["']/gi;
  for (const match of String(html || "").matchAll(pattern)) {
    const parsed = Date.parse(match[1]);
    if (Number.isFinite(parsed)) values.push(parsed);
  }
  if (!values.length) return null;
  return new Date(Math.max(...values)).toISOString();
}

export function evaluateMonitorLiveness(runs, nowMs = Date.now()) {
  const scheduleRuns = Array.isArray(runs)
    ? runs.filter((run) => run && run.event === "schedule")
    : [];
  const workflows = {};
  const staleWorkflows = [];

  for (const spec of WORKFLOW_FRESHNESS) {
    const candidates = scheduleRuns
      .filter((run) => run.name === spec.name && parseCreatedAt(run) !== null)
      .sort((left, right) => parseCreatedAt(right) - parseCreatedAt(left));
    const latest = candidates[0] || null;
    const createdMs = latest ? parseCreatedAt(latest) : null;
    const ageMinutes = createdMs === null
      ? null
      : Math.max(0, Math.floor((nowMs - createdMs) / 60000));
    const fresh = ageMinutes !== null && ageMinutes <= spec.maxAgeMinutes;
    if (!fresh) staleWorkflows.push(spec.name);
    workflows[spec.name] = {
      fresh,
      max_age_minutes: spec.maxAgeMinutes,
      age_minutes: ageMinutes,
      created_at: latest?.created_at || null,
      run_started_at: latest?.run_started_at || null,
      status: latest?.status || null,
      conclusion: latest?.conclusion || null,
      run_url: latest?.html_url || null,
    };
  }

  return {
    schema_version: 1,
    ok: staleWorkflows.length === 0,
    state: staleWorkflows.length === 0 ? "healthy" : "stale",
    // This legacy state evaluates GitHub's native schedule lane only.
    // The independent external Cron path is not attested by this field.
    scope: "github_native_schedule_only",
    combined_reliability_verified: false,
    checked_at: new Date(nowMs).toISOString(),
    stale_workflows: staleWorkflows,
    workflows,
  };
}


// Diagnostic creation evidence only. Public GitHub workflow_run metadata cannot
// prove which independent scheduler dispatched a workflow_dispatch event.
// Do not treat creation freshness here as Cloudflare Cron acceptance, or as
// successful completion of a downstream recovery job.
export function evaluateObservedRunCreation(runs, nowMs = Date.now()) {
  const candidates = Array.isArray(runs) ? runs : [];
  const workflows = {};
  const staleWorkflows = [];
  for (const spec of WORKFLOW_FRESHNESS) {
    const matches = candidates
      .filter((run) => run && run.name === spec.name &&
        ["schedule", "workflow_dispatch"].includes(run.event) &&
        parseCreatedAt(run) !== null)
      .sort((left, right) => parseCreatedAt(right) - parseCreatedAt(left));
    const latest = matches[0] || null;
    const createdMs = latest ? parseCreatedAt(latest) : null;
    const ageMinutes = createdMs === null
      ? null
      : Math.max(0, Math.floor((nowMs - createdMs) / 60000));
    const fresh = ageMinutes !== null && ageMinutes <= spec.maxAgeMinutes;
    if (!fresh) staleWorkflows.push(spec.name);
    workflows[spec.name] = {
      fresh,
      max_age_minutes: spec.maxAgeMinutes,
      age_minutes: ageMinutes,
      event: latest?.event || null,
      attribution: latest?.event === "schedule"
        ? "github_schedule"
        : latest?.event === "workflow_dispatch"
          ? "workflow_dispatch_unverified"
          : "missing",
      created_at: latest?.created_at || null,
      status: latest?.status || null,
      conclusion: latest?.conclusion || null,
      run_url: latest?.html_url || null,
    };
  }
  const creationFresh = staleWorkflows.length === 0;
  return {
    scope: "github_run_creation_all_supported_events",
    state: creationFresh ? "creation_fresh_unverified" : "creation_stale",
    creation_fresh: creationFresh,
    external_origin_verified: false,
    acceptance: "not_evaluated",
    stale_workflows: staleWorkflows,
    workflows,
  };
}
