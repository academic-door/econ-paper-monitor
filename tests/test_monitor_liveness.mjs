import assert from "node:assert/strict";
import test from "node:test";
import { evaluateMonitorLiveness, evaluateObservedRunCreation, latestWorkflowPageTimestamp } from "../infra/monitor-liveness.mjs";

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

test("workflow HTML fallback extracts the newest GitHub relative-time timestamp", () => {
  const html = `
    <relative-time datetime="2026-09-21T10:10:00Z">2 hours ago</relative-time>
    <time-ago datetime='2026-09-21T11:35:00Z'>25 minutes ago</time-ago>
    <relative-time datetime="not-a-date">unknown</relative-time>
  `;
  assert.equal(latestWorkflowPageTimestamp(html), "2026-09-21T11:35:00.000Z");
});

test("workflow HTML fallback fails closed when no GitHub time element exists", () => {
  assert.equal(latestWorkflowPageTimestamp("<html><body>No runs</body></html>"), null);
});


test("external-aligned but unverified dispatch cannot hide native schedule staleness", () => {
  const recentDispatch = [
    run("Monitor Watchdog", "2026-09-21T11:56:00Z", { event: "workflow_dispatch" }),
    run("Fast Discovery", "2026-09-21T11:55:00Z", { event: "workflow_dispatch" }),
    run("Update Paper Monitor", "2026-09-21T11:52:00Z", { event: "workflow_dispatch" }),
  ];
  const native = evaluateMonitorLiveness(recentDispatch, NOW);
  assert.equal(native.scope, "github_native_schedule_only");
  assert.equal(native.combined_reliability_verified, false);
  assert.equal(native.ok, false);
  assert.deepEqual(native.stale_workflows, [
    "Monitor Watchdog", "Fast Discovery", "Update Paper Monitor",
  ]);
  const observed = evaluateObservedRunCreation(recentDispatch, NOW);
  assert.equal(observed.creation_fresh, true);
  assert.equal(observed.state, "creation_fresh_unverified");
  assert.equal(observed.external_origin_verified, false);
  assert.equal(observed.acceptance, "not_evaluated");
  assert.equal(observed.workflows["Monitor Watchdog"].attribution, "workflow_dispatch_unverified");
});

test("cancelled Fast run supplies creation evidence but never a successful recovery", () => {
  const observed = evaluateObservedRunCreation([
    run("Monitor Watchdog", "2026-09-21T11:50:00Z"),
    run("Fast Discovery", "2026-09-21T11:55:00Z", {
      event: "workflow_dispatch", status: "completed", conclusion: "cancelled",
    }),
    run("Update Paper Monitor", "2026-09-21T11:40:00Z"),
  ], NOW);
  assert.equal(observed.creation_fresh, true);
  assert.equal(observed.workflows["Fast Discovery"].conclusion, "cancelled");
  assert.equal(observed.workflows["Fast Discovery"].attribution, "workflow_dispatch_unverified");
  assert.equal(observed.external_origin_verified, false);
});

test("observed creation reports stale and missing runs without claiming attribution", () => {
  const observed = evaluateObservedRunCreation([
    run("Monitor Watchdog", "2026-09-21T11:00:00Z"),
  ], NOW);
  assert.equal(observed.creation_fresh, false);
  assert.equal(observed.state, "creation_stale");
  assert.deepEqual(observed.stale_workflows, [
    "Monitor Watchdog", "Fast Discovery", "Update Paper Monitor",
  ]);
  assert.equal(observed.workflows["Fast Discovery"].age_minutes, null);
  assert.equal(observed.workflows["Fast Discovery"].attribution, "missing");
});
