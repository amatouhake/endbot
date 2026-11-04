// The Durable Object: one per server. It holds endbotd's outbound link and multiplexes browsers onto it
// (docs/WEB.md P1, P2, P7, P8). It keeps nothing but the display cache, and decides nothing but who holds the link.

import { DurableObject } from "cloudflare:workers";
import { WEB_PROTOCOL } from "../generated/protocol-version";
import type { Env } from "./index";

export const MAX_FRAME_BYTES = 1024 * 1024;
export const PROVE_MS = 5_000;
export const REQUEST_MS = 15_000;
export const MAX_BROWSERS = 64;
export const MAX_UNAUTHENTICATED = 16;
export const BUCKET_PER_MINUTE = 10;
const MAX_BODY_BYTES = MAX_FRAME_BYTES / 2;

class BodyTooLarge extends Error {}

const SESSION_COOKIE = "__Host-endbot_session";
const SITE_COOKIE = "__Host-endbot_site";
const SESSION_MAX_AGE = 30 * 86_400;
const SITE_MAX_AGE = 120;

type Attachment =
  | { role: "link"; proven: boolean; nonce: string; opened: number }
  | { role: "browser"; conn: string; authenticated: boolean; next: number };

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
  private readonly buckets = new Map<string, { minute: number; count: number }>();
  private nextConn = 0;

  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/api/display") return Response.json((await this.ctx.storage.get("display")) ?? { bds: "down" });
    const bucket = await hashIp(request.headers.get("CF-Connecting-IP") ?? "unknown");
    if (url.pathname === "/link") {
      // A separate count: browsers behind the server's own address must not stop it reconnecting.
      if (!this.allow(`link:${bucket}`)) return Response.json({ code: "rate_limited" }, { status: 429 });
      return this.acceptLink();
    }
    if (!this.allow(bucket)) return Response.json({ code: "rate_limited" }, { status: 429 });
    return url.pathname === "/ws" ? this.acceptBrowser(request, bucket) : this.relayHttp(request, url, bucket);
  }

  private allow(bucket: string): boolean {
    const minute = Math.floor(Date.now() / 60_000);
    const entry = this.buckets.get(bucket);
    if (!entry || entry.minute !== minute) {
      if (this.buckets.size > 4096) this.buckets.clear();
      this.buckets.set(bucket, { minute, count: 1 });
      return true;
    }
    entry.count += 1;
    return entry.count <= BUCKET_PER_MINUTE;
  }

  // -- endbotd's link ---------------------------------------------------------------

  private links(): WebSocket[] {
    // A socket we just closed can still be listed until the close completes.
    return this.ctx.getWebSockets("link").filter((socket) => socket.readyState === WebSocket.OPEN);
  }

  private provenLink(): WebSocket | null {
    return this.links().find((socket) => (socket.deserializeAttachment() as Attachment | null)?.role === "link"
      && (socket.deserializeAttachment() as { proven: boolean }).proven) ?? null;
  }

  private async acceptLink(): Promise<Response> {
    // At most one unproven link at a time (P1).
    for (const socket of this.links()) {
      const attachment = socket.deserializeAttachment() as { proven: boolean } | null;
      if (!attachment?.proven) socket.close(4001, "replaced");
    }
    const pair = new WebSocketPair();
    const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(24))))
      .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
    this.ctx.acceptWebSocket(pair[1], ["link"]);
    const attachment: Attachment = { role: "link", proven: false, nonce, opened: Date.now() };
    pair[1].serializeAttachment(attachment);
    pair[1].send(JSON.stringify({ t: "challenge", protocol: WEB_PROTOCOL, nonce, origin: this.env.ENDBOT_ORIGIN }));
    await this.ctx.storage.setAlarm(Date.now() + PROVE_MS);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  override async alarm(): Promise<void> {
    for (const socket of this.links()) {
      const attachment = socket.deserializeAttachment() as { proven: boolean; opened: number } | null;
      if (!attachment?.proven && Date.now() - (attachment?.opened ?? 0) >= PROVE_MS) {
        socket.close(4002, "not proven");
      }
    }
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

  private async onLinkFrame(socket: WebSocket, attachment: Extract<Attachment, { role: "link" }>, raw: string):
    Promise<void> {
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
      if (socket.readyState !== WebSocket.OPEN) return;
      if (!proven) {
        socket.close(4003, "proof refused");
        return;
      }
      // A newly proven link replaces the old one (P1). endbotd's table of browsers went with it, so they go too.
      const previous = this.provenLink();
      socket.serializeAttachment({ ...attachment, proven: true });
      if (previous) {
        previous.close(4004, "replaced by a newer link");
        this.linkLost();
      }
      socket.send(JSON.stringify({ t: "ready" }));
      return;
    }
    if (frame.t === "cache") await this.storeDisplay(frame);
    else if (frame.t === "msg") this.onLinkMessage(frame);
    else if (frame.t === "close") this.onLinkClose(frame);
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
    if (attachment?.role === "link" && attachment.proven && !this.provenLink()) this.linkLost();
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
      headers.append("Set-Cookie", `${SITE_COOKIE}=${answer.siteCookie}; Secure; HttpOnly; SameSite=None; Path=/; `
        + `Max-Age=${SITE_MAX_AGE}`);
    }
    if (url.pathname === "/auth/site/return") {
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
