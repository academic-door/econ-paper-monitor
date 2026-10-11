// Decision 0021: no product logic, external HTTP ingress, or dynamic target.
const DISPATCH_URL = 'https://api.github.com/repos/academic-door/econ-paper-monitor/actions/workflows/watchdog.yml/dispatches';

export function buildDispatchRequest(token, scheduledAt) {
  if (typeof token !== 'string' || !token) throw new Error('GITHUB_ACTIONS_DISPATCH_TOKEN is required');
  if (!(scheduledAt instanceof Date) || !Number.isFinite(scheduledAt.getTime())) {
    throw new TypeError('Cloudflare scheduledTime must be valid');
  }
  return {
    url: DISPATCH_URL,
    init: {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'User-Agent': 'academic-door-daily-watchdog-trigger',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      body: JSON.stringify({
        ref: 'main',
        inputs: {
          trigger_source: 'cloudflare-cron',
          scheduled_at: scheduledAt.toISOString(),
        },
      }),
    },
  };
}

export async function runScheduled(controller, env, fetchImpl = fetch) {
  const scheduledAt = new Date(controller?.scheduledTime);
  const {url, init} = buildDispatchRequest(env?.GITHUB_ACTIONS_DISPATCH_TOKEN, scheduledAt);
  const response = await fetchImpl(url, init);
  if (response.status !== 204) {
    // No response body, token, or product payload in errors/logs.
    throw new Error(`Daily Watchdog dispatch rejected with HTTP ${response.status}`);
  }
  console.log(
    `DAILY_WATCHDOG_EXTERNAL_TICK scheduled_at=${scheduledAt.toISOString()} ` +
    'workflow=watchdog.yml ref=main dispatch_http=204',
  );
  return {dispatched: true, status: 204, scheduled_at: scheduledAt.toISOString()};
}

export default {
  async scheduled(controller, env, ctx) {
    return runScheduled(controller, env);
  },
};
