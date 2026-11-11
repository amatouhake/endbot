// The Durable Object: one per server. It holds endbotd's outbound link and multiplexes browsers onto it
// (docs/WEB.md P1, P2, P7, P8). It keeps nothing but the display cache, and decides nothing but who holds the link.

import { DurableObject } from "cloudflare:workers";
import { WEB_PROTOCOL } from "../generated/protocol-version";
import type { Env } from "./index";

export const MAX_FRAME_BYTES = 1024 * 1024;
export const PROVE_MS = 5_000;
// endbotd says `link-ping` every 30 s, answered by the runtime with no wake-up of this object and nothing stored.
// A proven link heard from neither way for this long is retired: a PC that lost power or network sends no close.
export const LINK_SILENT_MS = 90_000;
const LINK_PING = "link-ping";
const LINK_PONG = "link-pong";
export const REQUEST_MS = 15_000;
export const MAX_BROWSERS = 64;
export const MAX_UNAUTHENTICATED = 16;
export const BUCKET_PER_MINUTE = 10;
// WebSocket upgrades count apart from /auth/*: a session cookie is all they carry, endbotd refuses a wrong one, and
// the connection caps bound them. Sharing the sign-in count refused the page's own socket after two sign-ins in a
// minute, and the page then looked signed out.
export const WS_PER_MINUTE = 30;
// The sign-in page's public display read: its own count, so it neither eats the sign-in budget nor goes unbounded.
export const DISPLAY_PER_MINUTE = 60;
const MAX_BODY_BYTES = MAX_FRAME_BYTES / 2;

class BodyTooLarge extends Error {}

const SESSION_COOKIE = "__Host-endbot_session";
const SITE_COOKIE = "__Host-endbot_site";
const SESSION_MAX_AGE = 30 * 86_400;
const SITE_MAX_AGE = 120;

type LinkAttachment = { role: "link"; proven: boolean; nonce: string; opened: number; retired?: boolean };
type Attachment = LinkAttachment | { role: "browser"; conn: string; authenticated: boolean; next: number };

interface Pending {
  resolve: (answer: RelayAnswer | null) => void;
}

interface RelayAnswer {
  status?: number;
  body?: unknown;
  location?: string;
  session?: string;
  clearSession?: boolean;
  siteCookie?: string;
}

function readCookie(header: string | null, name: string): string | null {
  for (const part of (header ?? "").split(";")) {
    const index = part.indexOf("=");
    if (index > 0 && part.slice(0, index).trim() === name) return part.slice(index + 1).trim() || null;
  }
  return null;
}

function decodeBase64Url(text: string): Uint8Array {
  const binary = atob(text.replaceAll("-", "+").replaceAll("_", "/"));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

async function hashIp(address: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`endbot-ip\n${address}`));
  return [...new Uint8Array(digest).slice(0, 8)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export class Relay extends DurableObject<Env> {
  // In memory only: losing these to hibernation costs a retry at most (P7).
  private readonly pending = new Map<string, Pending>();
  private linkSilentMs = LINK_SILENT_MS;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair(LINK_PING, LINK_PONG));
  }

  private readonly buckets = new Map<string, { minute: number; count: number }>();
  private nextConn = 0;

  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const bucket = await hashIp(request.headers.get("CF-Connecting-IP") ?? "unknown");
    if (url.pathname === "/api/display") {
      if (!this.allow(`display:${bucket}`, DISPLAY_PER_MINUTE)) {
        return Response.json({ code: "rate_limited" }, { status: 429 });
      }
      return Response.json(await this.display());
    }
    if (url.pathname === "/link") {
      // A separate count: browsers behind the server's own address must not stop it reconnecting.
      if (!this.allow(`link:${bucket}`)) return Response.json({ code: "rate_limited" }, { status: 429 });
      return this.acceptLink();
    }
    if (url.pathname === "/ws") {
      if (!this.allow(`ws:${bucket}`, Math.max(WS_PER_MINUTE, this.bucketLimit()))) {
        return Response.json({ code: "rate_limited" }, { status: 429 });
      }
      return this.acceptBrowser(request, bucket);
    }
    if (!this.allow(bucket)) return Response.json({ code: "rate_limited" }, { status: 429 });
    return this.relayHttp(request, url, bucket);
  }

  private bucketLimit(): number {
    // Only the end-to-end test sets this, so that many sign-ins fit in a minute; a deploy never does.
    const configured = Number((this.env as { AUTH_BUCKET_PER_MINUTE?: string }).AUTH_BUCKET_PER_MINUTE);
    return configured > 0 ? configured : BUCKET_PER_MINUTE;
  }

  private allow(bucket: string, limit = this.bucketLimit()): boolean {
    const minute = Math.floor(Date.now() / 60_000);
    const entry = this.buckets.get(bucket);
    if (!entry || entry.minute !== minute) {
      if (this.buckets.size > 4096) this.buckets.clear();
      this.buckets.set(bucket, { minute, count: 1 });
      return true;
    }
    entry.count += 1;
    return entry.count <= limit;
  }

  // -- endbotd's link ---------------------------------------------------------------

  /** The links still in play. A closed socket can stay listed as open, for good when its peer vanished. */
  private links(): WebSocket[] {
    return this.ctx.getWebSockets("link").filter((socket) => socket.readyState === WebSocket.OPEN
      && !(socket.deserializeAttachment() as LinkAttachment | null)?.retired);
  }

  /** Out of play at once, whether or not the close ever completes. */
  private retire(socket: WebSocket, code: number, reason: string): void {
    const attachment = socket.deserializeAttachment() as LinkAttachment | null;
    socket.serializeAttachment({ ...attachment, retired: true });
    try {
      socket.close(code, reason);
    } catch {
      // Already closing: it is out of play all the same.
    }
  }

  private heardAt(socket: WebSocket, attachment: LinkAttachment): number {
    return Math.max(attachment.opened, this.ctx.getWebSocketAutoResponseTimestamp(socket)?.getTime() ?? 0);
  }

  /** The link that speaks for the server: proven and heard from lately. Finding a silent one retires it. */
  private provenLink(): WebSocket | null {
    let current: WebSocket | null = null;
    let newest = -1;
    let silenced = false;
    for (const socket of this.links()) {
      const attachment = socket.deserializeAttachment() as LinkAttachment | null;
      if (!attachment?.proven) continue;
      if (Date.now() - this.heardAt(socket, attachment) >= this.linkSilentMs) {
        this.retire(socket, 4005, "silent");
        silenced = true;
      } else if (attachment.opened > newest) {
        current = socket;
        newest = attachment.opened;
      }
    }
    // Its browsers were talking to an endbotd that is gone: they must reconnect rather than wait.
    if (silenced && !current) this.linkLost();
    return current;
  }

  /** One alarm serves both deadlines: an unproven link's proof and a proven link's silence. */
  private async scheduleAlarm(): Promise<void> {
    let next = Number.POSITIVE_INFINITY;
    for (const socket of this.links()) {
      const attachment = socket.deserializeAttachment() as LinkAttachment | null;
      if (!attachment) continue;
      next = Math.min(next, attachment.proven ? this.heardAt(socket, attachment) + this.linkSilentMs
        : attachment.opened + PROVE_MS);
    }
    if (next === Number.POSITIVE_INFINITY) await this.ctx.storage.deleteAlarm();
    else await this.ctx.storage.setAlarm(Math.max(next, Date.now() + 1_000));
  }

  private async acceptLink(): Promise<Response> {
    // At most one unproven link at a time (P1).
    for (const socket of this.links()) {
      const attachment = socket.deserializeAttachment() as LinkAttachment | null;
      if (!attachment?.proven) this.retire(socket, 4001, "replaced");
    }
    const pair = new WebSocketPair();
    const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(24))))
      .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
    this.ctx.acceptWebSocket(pair[1], ["link"]);
    const attachment: Attachment = { role: "link", proven: false, nonce, opened: Date.now() };
    pair[1].serializeAttachment(attachment);
    pair[1].send(JSON.stringify({ t: "challenge", protocol: WEB_PROTOCOL, nonce, origin: this.env.ENDBOT_ORIGIN }));
    await this.scheduleAlarm();
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  override async alarm(): Promise<void> {
    for (const socket of this.links()) {
      const attachment = socket.deserializeAttachment() as LinkAttachment | null;
      if (!attachment?.proven && Date.now() - (attachment?.opened ?? 0) >= PROVE_MS) {
        this.retire(socket, 4002, "not proven");
      }
    }
    // With nobody asking, this is what tells open pages that the PC went quiet.
    this.provenLink();
    await this.scheduleAlarm();
  }

  private async verifyProof(frame: Record<string, unknown>, nonce: string): Promise<boolean> {
    if (frame.t !== "prove" || frame.protocol !== WEB_PROTOCOL || typeof frame.sig !== "string") return false;
    try {
      const expected = JSON.parse(this.env.ENDBOTD_LINK_KEY) as { key: string; keyId: string };
      if (frame.keyId !== expected.keyId) return false;
      const key = await crypto.subtle.importKey("raw", decodeBase64Url(expected.key), { name: "Ed25519" }, false,
        ["verify"]);
      const message = new TextEncoder().encode(`endbot-link-v1\n${this.env.ENDBOT_ORIGIN}\n${nonce}`);
      return await crypto.subtle.verify({ name: "Ed25519" }, key, decodeBase64Url(frame.sig), message);
    } catch {
      return false;
    }
  }

  private async onLinkFrame(socket: WebSocket, attachment: LinkAttachment, raw: string): Promise<void> {
    if (attachment.retired) return;
    let frame: Record<string, unknown>;
    try {
      frame = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    if (typeof frame !== "object" || frame === null) return;
    if (!attachment.proven) {
      const proven = await this.verifyProof(frame, attachment.nonce);
      // The check yields: this socket may have been replaced or timed out meanwhile, and then it decides nothing.
      if (!this.inPlay(socket)) return;
      if (!proven) {
        this.retire(socket, 4003, "proof refused");
        return;
      }
      // A newly proven link replaces every other (P1). endbotd's table of browsers went with them, so they go too.
      const others = this.links().filter((other) => other !== socket);
      const replaced = others.some((other) => (other.deserializeAttachment() as LinkAttachment | null)?.proven);
      socket.serializeAttachment({ ...attachment, proven: true });
      for (const other of others) this.retire(other, 4004, "replaced by a newer link");
      if (replaced) this.linkLost();
      socket.send(JSON.stringify({ t: "ready" }));
      await this.scheduleAlarm();
      return;
    }
    // Replaced while this frame was on its way: it no longer speaks for the server.
    if (!this.inPlay(socket)) return;
    if (frame.t === "cache") await this.storeDisplay(frame);
    else if (frame.t === "msg") this.onLinkMessage(frame);
    else if (frame.t === "close") this.onLinkClose(frame);
  }

  private inPlay(socket: WebSocket): boolean {
    return socket.readyState === WebSocket.OPEN && !(socket.deserializeAttachment() as LinkAttachment | null)?.retired;
  }

  /** The cache is what endbotd last said: with no live link, nothing vouches that the server is still up. */
  private async display(): Promise<Record<string, unknown>> {
    const stored = (await this.ctx.storage.get<Record<string, unknown>>("display")) ?? {};
    return this.provenLink() ? { bds: "down", ...stored } : { ...stored, bds: "down" };
  }

  private async storeDisplay(frame: Record<string, unknown>): Promise<void> {
    // Nothing else is kept here (P8).
    await this.ctx.storage.put("display", {
      displayName: typeof frame.displayName === "string" ? frame.displayName.slice(0, 64) : "",
      bds: frame.bds === "up" ? "up" : "down",
      since: Date.now(),
    });
  }

  private browser(conn: unknown): WebSocket | null {
    return typeof conn === "string" ? this.ctx.getWebSockets(`conn:${conn}`)[0] ?? null : null;
  }

  private onLinkMessage(frame: Record<string, unknown>): void {
    const pending = typeof frame.conn === "string" ? this.pending.get(frame.conn) : undefined;
    if (pending) {
      this.pending.delete(frame.conn as string);
      pending.resolve((frame.body ?? null) as RelayAnswer | null);
      return;
    }
    this.browser(frame.conn)?.send(JSON.stringify(frame.body));
  }

  private onLinkClose(frame: Record<string, unknown>): void {
    const pending = typeof frame.conn === "string" ? this.pending.get(frame.conn) : undefined;
    if (pending) {
      this.pending.delete(frame.conn as string);
      pending.resolve(null);
      return;
    }
    this.browser(frame.conn)?.close(4010, typeof frame.reason === "string" ? frame.reason.slice(0, 120) : "closed");
  }

  private sendToLink(frame: unknown): boolean {
    const link = this.provenLink();
    if (!link) return false;
    const data = JSON.stringify(frame);
    if (new TextEncoder().encode(data).length > MAX_FRAME_BYTES) return false;
    link.send(data);
    return true;
  }

  // -- browsers ---------------------------------------------------------------------

  private newConn(prefix: string): string {
    this.nextConn += 1;
    return `${prefix}${this.nextConn.toString(36)}-${crypto.randomUUID().slice(0, 8)}`;
  }

  private async acceptBrowser(request: Request, bucket: string): Promise<Response> {
    if (!this.provenLink()) return Response.json({ code: "unavailable" }, { status: 503 });
    const browsers = this.ctx.getWebSockets("browser").filter((socket) => socket.readyState === WebSocket.OPEN);
    const session = readCookie(request.headers.get("Cookie"), SESSION_COOKIE);
    const unauthenticated = browsers.filter((socket) =>
      !(socket.deserializeAttachment() as { authenticated: boolean }).authenticated).length;
    if (browsers.length >= MAX_BROWSERS || (!session && unauthenticated >= MAX_UNAUTHENTICATED)) {
      return Response.json({ code: "busy" }, { status: 429 });
    }
    const conn = this.newConn("w");
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1], ["browser", `conn:${conn}`]);
    const attachment: Attachment = { role: "browser", conn, authenticated: Boolean(session), next: 1 };
    pair[1].serializeAttachment(attachment);
    this.sendToLink({ t: "open", conn, kind: "ws", path: "/ws", origin: request.headers.get("Origin"), session,
      siteCookie: null, ipBucket: bucket });
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  override async webSocketMessage(socket: WebSocket, message: string | ArrayBuffer): Promise<void> {
    const attachment = socket.deserializeAttachment() as Attachment | null;
    if (typeof message !== "string" || new TextEncoder().encode(message).length > MAX_FRAME_BYTES) {
      socket.close(1009, "too large");
      return;
    }
    if (attachment?.role === "link") {
      await this.onLinkFrame(socket, attachment, message);
      return;
    }
    if (attachment?.role !== "browser") return;
    let body: unknown;
    try {
      body = JSON.parse(message);
    } catch {
      socket.close(1007, "not JSON");
      return;
    }
    const id = attachment.next;
    socket.serializeAttachment({ ...attachment, next: id + 1 });
    if (!this.sendToLink({ t: "msg", conn: attachment.conn, id, body })) socket.close(1013, "server offline");
  }

  override async webSocketClose(socket: WebSocket, code?: number, reason?: string): Promise<void> {
    try {
      socket.close(code === 1005 || code === 1006 || code === undefined ? 1000 : code, reason);
    } catch {
      // Already closed: nothing to complete.
    }
    const attachment = socket.deserializeAttachment() as Attachment | null;
    if (attachment?.role === "browser") {
      this.sendToLink({ t: "close", conn: attachment.conn, reason: "browser" });
      return;
    }
    // A retired link was dealt with when it was retired.
    if (attachment?.role === "link" && attachment.proven && !attachment.retired && !this.provenLink()) this.linkLost();
  }

  override async webSocketError(socket: WebSocket): Promise<void> {
    await this.webSocketClose(socket);
  }

  private linkLost(): void {
    for (const browser of this.ctx.getWebSockets("browser")) browser.close(1013, "server offline");
    for (const pending of this.pending.values()) pending.resolve(null);
    this.pending.clear();
  }

  // -- HTTP through the link ---------------------------------------------------------

  /** The body as text, never holding more than the limit in memory, whatever Content-Length claims. */
  private async boundedText(request: Request): Promise<string> {
    if (Number(request.headers.get("Content-Length") ?? 0) > MAX_BODY_BYTES) throw new BodyTooLarge();
    if (!request.body) return "";
    const decoder = new TextDecoder();
    const reader = request.body.getReader();
    let text = "";
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return text + decoder.decode();
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        throw new BodyTooLarge();
      }
      text += decoder.decode(value, { stream: true });
    }
  }

  private async requestBody(request: Request, url: URL): Promise<unknown> {
    if (request.method === "GET") return {};
    const text = await this.boundedText(request);
    // The site's form is urlencoded: multipart has no use here, and no bounded parser.
    if (url.pathname === "/auth/site/return") return { assertion: new URLSearchParams(text).get("assertion") };
    return text ? JSON.parse(text) : {};
  }

  private async relayHttp(request: Request, url: URL, bucket: string): Promise<Response> {
    if (!this.provenLink()) return Response.json({ code: "unavailable" }, { status: 503 });
    let body: unknown;
    try {
      body = await this.requestBody(request, url);
    } catch (error) {
      if (error instanceof BodyTooLarge) return Response.json({ code: "too_large" }, { status: 413 });
      return Response.json({ code: "invalid_request" }, { status: 400 });
    }
    const conn = this.newConn("h");
    const cookies = request.headers.get("Cookie");
    const answer = new Promise<RelayAnswer | null>((resolve) => {
      this.pending.set(conn, { resolve });
    });
    const timer = new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), REQUEST_MS));
    const opened = this.sendToLink({ t: "open", conn, kind: "http", path: url.pathname,
      origin: request.headers.get("Origin"), session: readCookie(cookies, SESSION_COOKIE),
      siteCookie: readCookie(cookies, SITE_COOKIE), ipBucket: bucket });
    if (!opened || !this.sendToLink({ t: "msg", conn, id: 1, body })) {
      this.pending.delete(conn);
      return Response.json({ code: "unavailable" }, { status: 503 });
    }
    const result = await Promise.race([answer, timer]);
    this.pending.delete(conn);
    if (result === "timeout") return Response.json({ code: "timeout" }, { status: 504 });
    if (result === null) return Response.json({ code: "unavailable" }, { status: 503 });
    return this.browserResponse(result, url);
  }

  private browserResponse(answer: RelayAnswer, url: URL): Response {
    const headers = new Headers();
    // The token leaves here only as the cookie; it is never in a body the browser can read (P5).
    if (answer.session) {
      headers.append("Set-Cookie", `${SESSION_COOKIE}=${answer.session}; Secure; HttpOnly; SameSite=Strict; Path=/; `
        + `Max-Age=${SESSION_MAX_AGE}`);
    } else if (answer.clearSession) {
      headers.append("Set-Cookie", `${SESSION_COOKIE}=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0`);
    }
    if (answer.siteCookie) {
      // Set by the start redirect, and again by a link-mode return to bind its passkey ceremony (P12).
      headers.append("Set-Cookie", `${SITE_COOKIE}=${answer.siteCookie}; Secure; HttpOnly; SameSite=None; Path=/; `
        + `Max-Age=${SITE_MAX_AGE}`);
    } else if (url.pathname === "/auth/site/return") {
      headers.append("Set-Cookie", `${SITE_COOKIE}=; Secure; HttpOnly; SameSite=None; Path=/; Max-Age=0`);
    }
    const status = typeof answer.status === "number" && answer.status >= 200 && answer.status < 600 ? answer.status : 502;
    if (status === 303 && typeof answer.location === "string") {
      headers.set("Location", answer.location);
      return new Response(null, { status, headers });
    }
    headers.set("Content-Type", "application/json");
    headers.set("Cache-Control", "no-store");
    return new Response(JSON.stringify(answer.body ?? {}), { status, headers });
  }
}
