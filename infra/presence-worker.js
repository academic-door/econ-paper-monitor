import { evaluateMonitorLiveness } from "./monitor-liveness.mjs";

const ACTIVE_WINDOW_MS = 120000;
const GITHUB_SCHEDULE_RUNS_URL = "https://api.github.com/repos/academic-door/econ-paper-monitor/actions/runs?event=schedule&per_page=100";

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
  };
}

function rssHeaders(origin, contentType) {
  return {
    "Access-Control-Allow-Origin": origin || "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Cache-Control": "public, max-age=300",
    "Content-Type": contentType || "application/rss+xml; charset=utf-8",
  };
}

function livenessRoom(env) {
  const roomId = env.PRESENCE_ROOM.idFromName("monitor-liveness");
  return env.PRESENCE_ROOM.get(roomId);
}

async function readMonitorLiveness(env, origin = "*") {
  const room = livenessRoom(env);
  const response = await room.fetch(new Request("https://presence.internal/monitor-liveness", {
    method: "GET",
    headers: { "x-origin": origin },
  }));
  return response.json();
}

async function storeMonitorLiveness(env, snapshot) {
  const room = livenessRoom(env);
  await room.fetch(new Request("https://presence.internal/monitor-liveness", {
    method: "PUT",
    headers: { "content-type": "application/json", "x-origin": "*" },
    body: JSON.stringify(snapshot),
  }));
}

async function refreshMonitorLiveness(env) {
  const checkedAt = new Date().toISOString();
  let snapshot;
  try {
    const upstream = await fetch(GITHUB_SCHEDULE_RUNS_URL, {
      headers: {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Academic-Door-Monitor-Liveness/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    });
    if (!upstream.ok) {
      throw new Error(`github_api_${upstream.status}`);
    }
    const payload = await upstream.json();
    snapshot = evaluateMonitorLiveness(payload.workflow_runs, Date.parse(checkedAt));
  } catch (error) {
    snapshot = {
      schema_version: 1,
      ok: false,
      state: "observer_error",
      checked_at: checkedAt,
      stale_workflows: [],
      workflows: {},
      error: String(error?.message || error || "unknown_error").slice(0, 120),
    };
  }
  await storeMonitorLiveness(env, snapshot);
  return snapshot;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "*";
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(origin) });
    }
    const url = new URL(request.url);
    if (url.pathname === "/monitor-liveness" && request.method === "GET") {
      let snapshot = await readMonitorLiveness(env, origin);
      if (!snapshot || snapshot.state === "unknown") {
        snapshot = await refreshMonitorLiveness(env);
      }
      return new Response(JSON.stringify(snapshot), { headers: corsHeaders(origin) });
    }
    if (url.pathname === "/cnki-rss" && request.method === "GET") {
      const targetText = (url.searchParams.get("url") || "").trim();
      let target;
      try {
        target = new URL(targetText);
      } catch {
        return new Response("invalid CNKI RSS URL", { status: 400, headers: rssHeaders(origin, "text/plain; charset=utf-8") });
      }
      if (target.protocol !== "https:" || !["rss.cnki.net", "navi.cnki.net", "kns.cnki.net"].includes(target.hostname)) {
        return new Response("CNKI RSS host required", { status: 403, headers: rssHeaders(origin, "text/plain; charset=utf-8") });
      }
      // rss.cnki.net currently serves a certificate whose common name is not
      // valid for that host. CNKI exposes the same feed at navi.cnki.net with
      // a valid certificate, so use that official alias inside Cloudflare.
      if (target.hostname === "rss.cnki.net") target.hostname = "navi.cnki.net";
      const upstream = await fetch(target.toString(), {
        headers: {
          "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
          "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
          "Referer": "https://www.cnki.net/",
          "User-Agent": "Mozilla/5.0 (compatible; Academic-Door-CNKI-Relay/1.0)",
        },
        cf: { cacheTtl: 300, cacheEverything: true },
      });
      const body = await upstream.arrayBuffer();
      return new Response(body, {
        status: upstream.status,
        headers: rssHeaders(origin, upstream.headers.get("content-type") || "application/rss+xml; charset=utf-8"),
      });
    }
    if (url.pathname !== "/presence" || request.method !== "GET") {
      return new Response(JSON.stringify({ error: "not_found" }), { status: 404, headers: corsHeaders(origin) });
    }
    const clientId = (url.searchParams.get("client_id") || "").trim();
    if (!/^[a-zA-Z0-9_-]{16,80}$/.test(clientId)) {
      return new Response(JSON.stringify({ error: "invalid_client_id" }), { status: 400, headers: corsHeaders(origin) });
    }
    const site = (url.searchParams.get("site") || "default").trim();
    const windowMode = (url.searchParams.get("window") || "active").trim();
    if (!/^[a-z0-9-]{1,40}$/.test(site) || !["active", "day"].includes(windowMode)) {
      return new Response(JSON.stringify({ error: "invalid_scope" }), { status: 400, headers: corsHeaders(origin) });
    }
    const activeWindowMs = windowMode === "day" ? 86400000 : ACTIVE_WINDOW_MS;
    const roomId = env.PRESENCE_ROOM.idFromName(`${origin}:${site}:${windowMode}`);
    const room = env.PRESENCE_ROOM.get(roomId);
    return room.fetch(new Request("https://presence.internal/heartbeat", {
      method: "POST",
      headers: { "content-type": "application/json", "x-origin": origin },
      body: JSON.stringify({ clientId, activeWindowMs }),
    }));
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(refreshMonitorLiveness(env));
  },
};

export class PresenceRoom {
  constructor(state) {
    this.state = state;
  }

  async fetch(request) {
    const url = new URL(request.url);
    const origin = request.headers.get("x-origin") || "*";
    if (url.pathname === "/monitor-liveness") {
      if (request.method === "PUT") {
        const payload = await request.json();
        await this.state.storage.put("monitor:liveness", payload);
        return new Response(JSON.stringify(payload), { headers: corsHeaders(origin) });
      }
      if (request.method === "GET") {
        const payload = await this.state.storage.get("monitor:liveness") || {
          schema_version: 1,
          ok: false,
          state: "unknown",
          checked_at: null,
          stale_workflows: [],
          workflows: {},
        };
        return new Response(JSON.stringify(payload), { headers: corsHeaders(origin) });
      }
      return new Response(JSON.stringify({ error: "method_not_allowed" }), { status: 405, headers: corsHeaders(origin) });
    }
    if (url.pathname !== "/heartbeat" || request.method !== "POST") {
      return new Response(JSON.stringify({ error: "not_found" }), { status: 404, headers: corsHeaders(origin) });
    }
    const payload = await request.json();
    const now = Date.now();
    const entries = await this.state.storage.list({ prefix: "client:" });
    const stale = [];
    let online = 0;
    const clientKey = `client:${payload.clientId}`;
    for (const [key, timestamp] of entries) {
      if (now - Number(timestamp) > Number(payload.activeWindowMs || ACTIVE_WINDOW_MS)) stale.push(key);
      else online += 1;
    }
    if (stale.length) await this.state.storage.delete(stale);
    const isNewClient = !entries.has(clientKey) || stale.includes(clientKey);
    await this.state.storage.put(clientKey, now);
    if (isNewClient) online += 1;
    return new Response(JSON.stringify({ online }), {
      headers: corsHeaders(origin),
    });
  }
}
