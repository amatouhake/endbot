// The Worker: static UI, cookies, Origin checks and the relay's front door (docs/WEB.md P5).
// It verifies no passkey, holds no account and makes no authorization decision: endbotd does.

import { Hono } from "hono";

export { Relay } from "./relay";

export interface Env {
  RELAY: DurableObjectNamespace;
  ASSETS?: Fetcher;
  ENDBOTD_LINK_KEY: string;
  ENDBOT_ORIGIN: string;
}

const SECURITY_HEADERS: Record<string, string> = {
  "Content-Security-Policy": "default-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    + "form-action 'self'; object-src 'none'",
  "Referrer-Policy": "no-referrer",
  "Strict-Transport-Security": "max-age=31536000",
  "X-Content-Type-Options": "nosniff",
};

const app = new Hono<{ Bindings: Env }>();

const relay = (env: Env): DurableObjectStub => env.RELAY.get(env.RELAY.idFromName("server"));

function refuse(status: number, code: string): Response {
  return Response.json({ code }, { status });
}

// endbotd's outbound link proves itself with its key in the Durable Object. endbotd sends no Origin and a browser
// always does, so a web page cannot take up the one unproven slot.
app.get("/link", (c) => {
  if (c.req.header("Upgrade")?.toLowerCase() !== "websocket") return refuse(426, "upgrade_required");
  if (c.req.header("Origin")) return refuse(403, "origin");
  return relay(c.env).fetch(c.req.raw);
});

// A WebSocket upgrade is a state-changing request a cross-site page could start: exact Origin only (P5).
app.get("/ws", (c) => {
  if (c.req.header("Upgrade")?.toLowerCase() !== "websocket") return refuse(426, "upgrade_required");
  if (c.req.header("Origin") !== c.env.ENDBOT_ORIGIN) return refuse(403, "origin");
  return relay(c.env).fetch(c.req.raw);
});

app.get("/api/display", (c) => relay(c.env).fetch(c.req.raw));

app.get("/auth/site/start", (c) => relay(c.env).fetch(c.req.raw));

// The site's auto-submitted form: cross-site by design. endbotd checks the Origin against the issuer, the nonce
// and the signature; here it needs only an Origin and the cookie that names a pending flow (P12).
app.post("/auth/site/return", (c) => {
  if (!c.req.header("Origin")) return refuse(403, "origin");
  if (!c.req.header("Cookie")?.includes("__Host-endbot_site=")) {
    return c.redirect("/?site_error=site_expired", 303);
  }
  return relay(c.env).fetch(c.req.raw);
});

app.post("/auth/*", (c) => {
  if (c.req.header("Origin") !== c.env.ENDBOT_ORIGIN) return refuse(403, "origin");
  // Forces a CORS preflight, which this Worker never grants (P5).
  const type = c.req.header("Content-Type")?.split(";")[0]?.trim().toLowerCase();
  if (type !== "application/json") return refuse(415, "content_type");
  return relay(c.env).fetch(c.req.raw);
});

app.all("/auth/*", () => refuse(405, "method"));
app.all("/link", () => refuse(405, "method"));
app.all("/ws", () => refuse(405, "method"));

app.get("*", (c) => (c.env.ASSETS ? c.env.ASSETS.fetch(c.req.raw) : refuse(404, "not_found")));
app.all("*", () => refuse(405, "method"));

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const response = await app.fetch(request, env, ctx);
    if (response.webSocket) return response;
    const secured = new Response(response.body, response);
    for (const [name, value] of Object.entries(SECURITY_HEADERS)) secured.headers.set(name, value);
    return secured;
  },
} satisfies ExportedHandler<Env>;
