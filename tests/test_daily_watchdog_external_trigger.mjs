import test from 'node:test';
import assert from 'node:assert/strict';
import {buildDispatchRequest, runScheduled} from '../infra/cloudflare/daily-watchdog-trigger/worker.mjs';

const tick = Date.parse('2026-10-11T04:19:00.000Z');

test('fixed repo, single Watchdog workflow, default branch and attribution', () => {
  const {url, init} = buildDispatchRequest('fake-token', new Date(tick));
  assert.equal(url, 'https://api.github.com/repos/academic-door/econ-paper-monitor/actions/workflows/watchdog.yml/dispatches');
  assert.equal(init.method, 'POST');
  assert.equal(init.headers.Authorization, 'Bearer fake-token');
  assert.deepEqual(JSON.parse(init.body), {
    ref: 'main', inputs: {trigger_source: 'cloudflare-cron', scheduled_at: '2026-10-11T04:19:00.000Z'},
  });
});

test('scheduled event makes exactly one dispatch without product payload', async () => {
  const requests = [];
  const result = await runScheduled(
    {scheduledTime: tick}, {GITHUB_ACTIONS_DISPATCH_TOKEN: 'fake-token'},
    async (url, init) => {requests.push({url, init}); return new Response(null, {status: 204});},
  );
  assert.deepEqual(result, {dispatched: true, status: 204, scheduled_at: '2026-10-11T04:19:00.000Z'});
  assert.equal(requests.length, 1);
  assert.deepEqual(Object.keys(JSON.parse(requests[0].init.body)).sort(), ['inputs', 'ref']);
});

test('missing secret or invalid tick fails closed without network', async () => {
  let calls = 0;
  const fetchImpl = async () => {calls++; return new Response(null, {status: 204});};
  await assert.rejects(runScheduled({scheduledTime: tick}, {}, fetchImpl), /GITHUB_ACTIONS_DISPATCH_TOKEN/);
  await assert.rejects(runScheduled({scheduledTime: NaN}, {GITHUB_ACTIONS_DISPATCH_TOKEN: 'fake'}, fetchImpl), /valid/);
  assert.equal(calls, 0);
});

test('GitHub errors do not disclose response body or secret', async () => {
  const token = 'very-secret-token';
  await assert.rejects(
    runScheduled({scheduledTime: tick}, {GITHUB_ACTIONS_DISPATCH_TOKEN: token},
      async () => new Response('secret echoed: ' + token, {status: 403})),
    (error) => error.message === 'Daily Watchdog dispatch rejected with HTTP 403',
  );
});
