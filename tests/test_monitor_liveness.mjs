import assert from "node:assert/strict";
import test from "node:test";
import { evaluateMonitorLiveness } from "../infra/monitor-liveness.mjs";

const NOW = Date.parse("2026-09-21T12:00:00Z");

function run(name, createdAt, extra = {}) {
  return {
    name,
    event: "schedule",
    created_at: createdAt,
    run_started_at: createdAt,
    status: "completed",
    conclusion: "success",
    html_url: "https://example.test/run",
    ...extra,
  };
}

test("healthy scheduler history stays green", () => {
  const snapshot = evaluateMonitorLiveness([
    run("Monitor Watchdog", "2026-09-21T11:30:00Z"),
    run("Fast Discovery", "2026-09-21T11:25:00Z"),
    run("Update Paper Monitor", "2026-09-21T10:45:00Z"),
  ], NOW);
  assert.equal(snapshot.ok, true);
  assert.equal(snapshot.state, "healthy");
  assert.deepEqual(snapshot.stale_workflows, []);
});

test("stale watchdog is surfaced independently", () => {
  const snapshot = evaluateMonitorLiveness([
    run("Monitor Watchdog", "2026-09-21T10:00:00Z"),
    run("Fast Discovery", "2026-09-21T11:40:00Z"),
    run("Update Paper Monitor", "2026-09-21T11:00:00Z"),
  ], NOW);
  assert.equal(snapshot.ok, false);
  assert.equal(snapshot.state, "stale");
  assert.deepEqual(snapshot.stale_workflows, ["Monitor Watchdog"]);
  assert.equal(snapshot.workflows["Monitor Watchdog"].age_minutes, 120);
});

test("missing workflow history fails closed", () => {
  const snapshot = evaluateMonitorLiveness([
    run("Monitor Watchdog", "2026-09-21T11:45:00Z"),
    run("Update Paper Monitor", "2026-09-21T11:00:00Z"),
  ], NOW);
  assert.equal(snapshot.ok, false);
  assert.ok(snapshot.stale_workflows.includes("Fast Discovery"));
  assert.equal(snapshot.workflows["Fast Discovery"].age_minutes, null);
});

test("scheduler liveness keys off run creation, not workflow success", () => {
  const snapshot = evaluateMonitorLiveness([
    run("Monitor Watchdog", "2026-09-21T11:45:00Z", { conclusion: "failure" }),
    run("Fast Discovery", "2026-09-21T11:45:00Z", { conclusion: "failure" }),
    run("Update Paper Monitor", "2026-09-21T11:30:00Z", { conclusion: "failure" }),
  ], NOW);
  assert.equal(snapshot.ok, true);
});
