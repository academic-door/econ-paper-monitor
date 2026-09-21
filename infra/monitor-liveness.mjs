export const WORKFLOW_FRESHNESS = Object.freeze([
  Object.freeze({ name: "Monitor Watchdog", maxAgeMinutes: 45 }),
  Object.freeze({ name: "Fast Discovery", maxAgeMinutes: 45 }),
  Object.freeze({ name: "Update Paper Monitor", maxAgeMinutes: 90 }),
]);

function parseCreatedAt(run) {
  const value = Date.parse(String(run?.created_at || ""));
  return Number.isFinite(value) ? value : null;
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
    checked_at: new Date(nowMs).toISOString(),
    stale_workflows: staleWorkflows,
    workflows,
  };
}
