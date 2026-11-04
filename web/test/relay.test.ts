// The Worker and Durable Object in workerd, with the test playing endbotd (docs/WEB_PROTOCOL.md).
import Ajv2020 from "ajv/dist/2020";
import { env, runInDurableObject, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import schema from "../../core/src/endbotd/web_protocol.schema.json";
import examples from "../protocol-examples.json";
import { WEB_PROTOCOL } from "../src/generated/protocol-version";

const ORIGIN = "https://endbot.example";

function base64Url(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return btoa(String.fromCharCode(...view)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

class Messages {
  readonly received: any[] = [];
  private waiters: (() => void)[] = [];
  closed: { code: number; reason: string } | null = null;

  constructor(readonly socket: WebSocket) {
    socket.addEventListener("message", (event) => {
      this.received.push(JSON.parse(event.data as string));
      this.wake();
    });
    socket.addEventListener("close", (event) => {
      this.closed = { code: event.code, reason: event.reason };
      this.wake();
    });
    socket.accept();
  }

  private wake(): void {
    for (const waiter of this.waiters.splice(0)) waiter();
  }

  async next(predicate: (message: any) => boolean = () => true): Promise<any> {
    for (let attempt = 0; attempt < 200; attempt += 1) {
      const index = this.received.findIndex(predicate);
      if (index >= 0) return this.received.splice(index, 1)[0];
      await Promise.race([new Promise<void>((resolve) => this.waiters.push(resolve)),
        new Promise((resolve) => setTimeout(resolve, 25))]);
    }
    throw new Error(`no matching message; have ${JSON.stringify(this.received)}`);
  }

  async whenClosed(): Promise<{ code: number; reason: string }> {
    for (let attempt = 0; attempt < 200 && !this.closed; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    if (!this.closed) throw new Error("not closed");
    return this.closed;
  }

  send(message: unknown): void {
    this.socket.send(JSON.stringify(message));
  }
}

async function upgrade(path: string, headers: Record<string, string> = {}): Promise<Response> {
  return SELF.fetch(`${ORIGIN}${path}`, { headers: { Upgrade: "websocket", ...headers } });
}

let keys: CryptoKeyPair;

async function installKey(): Promise<void> {
  keys = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
  const raw = (await crypto.subtle.exportKey("raw", keys.publicKey)) as ArrayBuffer;
  (env as any).ENDBOTD_LINK_KEY = JSON.stringify({ key: base64Url(raw), keyId: "k1" });
}

async function sign(nonce: string, origin = ORIGIN, key = keys.privateKey): Promise<string> {
  const message = new TextEncoder().encode(`endbot-link-v1\n${origin}\n${nonce}`);
  return base64Url(await crypto.subtle.sign({ name: "Ed25519" }, key, message));
}

async function openLink(): Promise<Messages> {
  const response = await upgrade("/link");
  expect(response.status).toBe(101);
  return new Messages(response.webSocket!);
}

async function provenLink(): Promise<Messages> {
  const link = await openLink();
  const challenge = await link.next();
  link.send({ t: "prove", protocol: WEB_PROTOCOL, endbotdVersion: "test", keyId: "k1", sig: await sign(challenge.nonce) });
  await link.next((message) => message.t === "ready");
  return link;
}

/** Plays endbotd for one HTTP request: answers the next open with `answer`. */
async function answerNext(link: Messages, answer: unknown): Promise<any> {
  const open = await link.next((message) => message.t === "open");
  const message = await link.next((frame) => frame.t === "msg" && frame.conn === open.conn);
  link.send({ t: "msg", conn: open.conn, id: message.id, body: answer });
  link.send({ t: "close", conn: open.conn, reason: "done" });
  return { open, message };
}

function post(path: string, body: unknown = {}, headers: Record<string, string> = {}): Promise<Response> {
  return SELF.fetch(`${ORIGIN}${path}`, {
    method: "POST",
    redirect: "manual",
    headers: { Origin: ORIGIN, "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

const relayStub = () => (env as any).RELAY.get((env as any).RELAY.idFromName("server"));

// One Durable Object per server means one per test run here: start each test from a clean one.
beforeEach(async () => {
  await installKey();
  await (runInDurableObject as any)(relayStub(), async (instance: any, state: DurableObjectState) => {
    for (const socket of state.getWebSockets()) socket.close(1000, "next test");
    instance.buckets.clear();
    instance.pending.clear();
    await state.storage.deleteAll();
  });
});

describe("protocol schema", () => {
  const validate = new Ajv2020({ strict: false }).compile(schema);

  it("accepts every valid example and refuses every invalid one", () => {
    for (const example of examples.valid) expect(validate(example), JSON.stringify(example)).toBe(true);
    for (const example of examples.invalid) expect(validate(example), JSON.stringify(example)).toBe(false);
  });

  it("agrees with the compiled protocol version", () => {
    expect(schema.version).toBe(WEB_PROTOCOL);
  });
});

describe("the link handshake", () => {
  it("challenges with the protocol and origin, and accepts a good proof", async () => {
    const link = await openLink();
    const challenge = await link.next();
    expect(challenge).toMatchObject({ t: "challenge", protocol: WEB_PROTOCOL, origin: ORIGIN });
    expect(challenge.nonce.length).toBeGreaterThanOrEqual(16);
    link.send({ t: "prove", protocol: WEB_PROTOCOL, endbotdVersion: "x", keyId: "k1", sig: await sign(challenge.nonce) });
    expect(await link.next()).toEqual({ t: "ready" });
  });

  it.each([
    ["a wrong key", async (nonce: string) => {
      const other = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
      return { sig: await sign(nonce, ORIGIN, other.privateKey) };
    }],
    ["another origin", async (nonce: string) => ({ sig: await sign(nonce, "https://evil.example") })],
    ["another nonce", async () => ({ sig: await sign("another-nonce-entirely") })],
    ["another key id", async (nonce: string) => ({ sig: await sign(nonce), keyId: "k2" })],
    ["another protocol", async (nonce: string) => ({ sig: await sign(nonce), protocol: WEB_PROTOCOL + 1 })],
    ["a malformed signature", async () => ({ sig: "not base64 !!" })],
  ])("closes a link proving with %s", async (_name, change) => {
    const link = await openLink();
    const challenge = await link.next();
    link.send({ t: "prove", protocol: WEB_PROTOCOL, endbotdVersion: "x", keyId: "k1", ...(await change(challenge.nonce)) });
    expect((await link.whenClosed()).code).toBe(4003);
    expect(link.received).toEqual([]);
  });

  it("holds one unproven link at a time", async () => {
    const first = await openLink();
    const second = await openLink();
    expect((await first.whenClosed()).code).toBe(4001);
    await second.next();
    expect(second.closed).toBeNull();
  });

  it("lets a newly proven link replace the old one", async () => {
    const first = await provenLink();
    await provenLink();
    expect((await first.whenClosed()).code).toBe(4004);
  });

  it("closes a link that does not prove itself when the alarm fires", async () => {
    const link = await openLink();
    await link.next();
    await (runInDurableObject as any)(relayStub(), async (instance: any) => {
      const realNow = Date.now;
      Date.now = () => realNow() + 6_000;
      try {
        await instance.alarm();
      } finally {
        Date.now = realNow;
      }
    });
    expect((await link.whenClosed()).code).toBe(4002);
  });

  it("does not carry requests on an unproven link", async () => {
    const link = await openLink();
    await link.next();
    const response = await post("/auth/login/options");
    expect(response.status).toBe(503);
    expect(link.received).toEqual([]);
  });
});

describe("HTTP through the relay", () => {
  it("is 503 with no link", async () => {
    expect((await post("/auth/login/options")).status).toBe(503);
  });

  it("carries the path, body and cookies to endbotd and returns its answer", async () => {
    const link = await provenLink();
    const pending = post("/auth/login/verify", { ceremony: "c" }, {
      Cookie: "other=1; __Host-endbot_session=tok; __Host-endbot_site=sit",
    });
    const { open, message } = await answerNext(link, { status: 200, body: { gamertag: "Alice" } });
    expect(open).toMatchObject({ kind: "http", path: "/auth/login/verify", origin: ORIGIN, session: "tok", siteCookie: "sit" });
    expect(open.ipBucket).toMatch(/^[0-9a-f]{16}$/);
    expect(message.body).toEqual({ ceremony: "c" });
    const response = await pending;
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ gamertag: "Alice" });
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it("turns a session into a cookie and never into a body", async () => {
    const link = await provenLink();
    const pending = post("/auth/login/verify");
    await answerNext(link, { status: 200, body: { gamertag: "Alice" }, session: "secret-token" });
    const response = await pending;
    const cookie = response.headers.get("Set-Cookie")!;
    expect(cookie).toContain("__Host-endbot_session=secret-token");
    for (const attribute of ["Secure", "HttpOnly", "SameSite=Strict", "Path=/"]) expect(cookie).toContain(attribute);
    expect(await response.text()).not.toContain("secret-token");
  });

  it("clears the cookie when endbotd says so", async () => {
    const link = await provenLink();
    const pending = post("/auth/logout");
    await answerNext(link, { status: 200, body: {}, clearSession: true });
    expect((await pending).headers.get("Set-Cookie")).toContain("Max-Age=0");
  });

  it("is 503 when the link drops during a request", async () => {
    const link = await provenLink();
    const pending = post("/auth/login/options");
    await link.next((message) => message.t === "open");
    link.socket.close(1000, "bye");
    expect((await pending).status).toBe(503);
  });

  it("refuses a body that is not JSON", async () => {
    await provenLink();
    const response = await SELF.fetch(`${ORIGIN}/auth/login/options`, {
      method: "POST", headers: { Origin: ORIGIN, "Content-Type": "application/json" }, body: "{",
    });
    expect(response.status).toBe(400);
  });

  it("limits requests per client address", async () => {
    const link = await provenLink();
    const answering = (async () => {
      for (let index = 0; index < 10; index += 1) await answerNext(link, { status: 200, body: {} });
    })();
    const headers = { "CF-Connecting-IP": "203.0.113.9" };
    for (let index = 0; index < 10; index += 1) expect((await post("/auth/info", {}, headers)).status).toBe(200);
    await answering;
    expect((await post("/auth/info", {}, headers)).status).toBe(429);
  });
});

describe("what the Worker refuses by itself", () => {
  it.each([
    ["no Origin", { "Content-Type": "application/json" }],
    ["another Origin", { Origin: "https://evil.example", "Content-Type": "application/json" }],
    ["a look-alike Origin", { Origin: `${ORIGIN}.evil.example`, "Content-Type": "application/json" }],
    ["a form", { Origin: ORIGIN, "Content-Type": "application/x-www-form-urlencoded" }],
    ["no content type", { Origin: ORIGIN }],
  ])("refuses a POST with %s", async (_name, headers) => {
    const link = await provenLink();
    const response = await SELF.fetch(`${ORIGIN}/auth/logout`, { method: "POST", headers: headers as any, body: "{}" });
    expect([403, 415]).toContain(response.status);
    expect(link.received).toEqual([]);
  });

  it("refuses a WebSocket from another Origin", async () => {
    const link = await provenLink();
    expect((await upgrade("/ws", { Origin: "https://evil.example" })).status).toBe(403);
    expect((await upgrade("/ws")).status).toBe(403);
    expect(link.received).toEqual([]);
  });

  it("refuses a GET on an auth route and other methods elsewhere", async () => {
    expect((await SELF.fetch(`${ORIGIN}/auth/logout`)).status).toBe(405);
    expect((await SELF.fetch(`${ORIGIN}/`, { method: "DELETE" })).status).toBe(405);
  });

  it("sets the security headers on a response", async () => {
    const response = await SELF.fetch(`${ORIGIN}/nothing`);
    expect(response.headers.get("Content-Security-Policy")).toBe(
      "default-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'");
    expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
    expect(response.headers.get("Strict-Transport-Security")).toContain("max-age=");
    expect(response.headers.get("Set-Cookie")).toBeNull();
  });
});

describe("the trusted site routes", () => {
  it("sets the site cookie and follows the redirect on start", async () => {
    const link = await provenLink();
    const pending = SELF.fetch(`${ORIGIN}/auth/site/start`, { redirect: "manual" });
    await answerNext(link, { status: 303, location: "https://alice.example/start?nonce=n", siteCookie: "pending", body: {} });
    const response = await pending;
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("https://alice.example/start?nonce=n");
    const cookie = response.headers.get("Set-Cookie")!;
    expect(cookie).toContain("__Host-endbot_site=pending");
    expect(cookie).toContain("SameSite=None");
    expect(cookie).toContain("Max-Age=120");
  });

  function formPost(headers: Record<string, string>): Promise<Response> {
    return SELF.fetch(`${ORIGIN}/auth/site/return`, {
      method: "POST", redirect: "manual",
      headers: { "Content-Type": "application/x-www-form-urlencoded", ...headers }, body: "assertion=a.b.c",
    });
  }

  it("relays the return POST with the assertion, Origin and cookie", async () => {
    const link = await provenLink();
    const pending = formPost({ Origin: "https://alice.example", Cookie: "__Host-endbot_site=pending" });
    const { open, message } = await answerNext(link, { status: 303, location: "/", session: "tok", body: {} });
    expect(open).toMatchObject({ origin: "https://alice.example", siteCookie: "pending", path: "/auth/site/return" });
    expect(message.body).toEqual({ assertion: "a.b.c" });
    const response = await pending;
    expect(response.headers.get("Location")).toBe("/");
    const cookies = response.headers.getSetCookie();
    expect(cookies.some((cookie) => cookie.startsWith("__Host-endbot_session=tok"))).toBe(true);
    expect(cookies.some((cookie) => cookie.startsWith("__Host-endbot_site=;"))).toBe(true);
  });

  it("refuses a return POST without an Origin, and redirects one without the cookie", async () => {
    const link = await provenLink();
    expect((await formPost({ Cookie: "__Host-endbot_site=pending" })).status).toBe(403);
    const response = await formPost({ Origin: "https://alice.example" });
    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe("/?site_error=site_expired");
    expect(link.received).toEqual([]);
  });
});

describe("browser WebSockets", () => {
  async function browser(session = "tok"): Promise<Messages> {
    const response = await upgrade("/ws", { Origin: ORIGIN, Cookie: `__Host-endbot_session=${session}` });
    expect(response.status).toBe(101);
    return new Messages(response.webSocket!);
  }

  it("is 503 with no link", async () => {
    expect((await upgrade("/ws", { Origin: ORIGIN })).status).toBe(503);
  });

  it("opens through the link and relays both ways", async () => {
    const link = await provenLink();
    const socket = await browser();
    const open = await link.next((message) => message.t === "open");
    expect(open).toMatchObject({ kind: "ws", session: "tok", path: "/ws" });
    socket.send({ v: 1, id: 5, type: "query" });
    const message = await link.next((frame) => frame.t === "msg");
    expect(message).toMatchObject({ conn: open.conn, body: { v: 1, id: 5, type: "query" } });
    link.send({ t: "msg", conn: open.conn, id: message.id, body: { id: 5, type: "result", code: "ok" } });
    expect(await socket.next()).toEqual({ id: 5, type: "result", code: "ok" });
  });

  it("closes the browser when endbotd closes its connection", async () => {
    const link = await provenLink();
    const socket = await browser();
    const open = await link.next((message) => message.t === "open");
    link.send({ t: "close", conn: open.conn, reason: "signed_out" });
    expect(await socket.whenClosed()).toMatchObject({ code: 4010, reason: "signed_out" });
  });

  it("tells endbotd when a browser leaves", async () => {
    const link = await provenLink();
    const socket = await browser();
    const open = await link.next((message) => message.t === "open");
    socket.socket.close(1000, "bye");
    expect(await link.next((message) => message.t === "close")).toMatchObject({ conn: open.conn, reason: "browser" });
  });

  it("closes a browser that sends something other than JSON", async () => {
    await provenLink();
    const socket = await browser();
    socket.socket.send("{");
    expect((await socket.whenClosed()).code).toBe(1007);
  });

  it("closes browsers when the link is lost", async () => {
    const link = await provenLink();
    const socket = await browser();
    await link.next((message) => message.t === "open");
    link.socket.close(1000, "bye");
    expect((await socket.whenClosed()).code).toBe(1013);
  });

  it("does not forward a browser's frames as link frames", async () => {
    const link = await provenLink();
    const socket = await browser();
    await link.next((message) => message.t === "open");
    socket.send({ t: "prove", protocol: WEB_PROTOCOL, sig: "x" });
    const message = await link.next((frame) => frame.t === "msg");
    expect(message.body).toEqual({ t: "prove", protocol: WEB_PROTOCOL, sig: "x" });
  });
});

describe("the display cache", () => {
  it("keeps only the display fields", async () => {
    const link = await provenLink();
    link.send({ t: "cache", displayName: "Mine", bds: "up", secret: "must not stay" });
    let display: any = {};
    for (let attempt = 0; attempt < 100 && display.bds !== "up"; attempt += 1) {
      display = await (await SELF.fetch(`${ORIGIN}/api/display`)).json();
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    expect(display).toMatchObject({ displayName: "Mine", bds: "up" });
    expect(JSON.stringify(display)).not.toContain("must not stay");
  });

  it("does not take a cache from an unproven link", async () => {
    const link = await openLink();
    await link.next();
    link.send({ t: "cache", displayName: "Forged", bds: "up" });
    expect((await link.whenClosed()).code).toBe(4003);
    expect(await (await SELF.fetch(`${ORIGIN}/api/display`)).json()).toEqual({ bds: "down" });
  });
});
