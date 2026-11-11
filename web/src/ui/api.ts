// HTTP and WebSocket to endbotd, through the relay. The session lives in an HttpOnly cookie: no script here sees it.
import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { WEB_PROTOCOL } from "../generated/protocol-version";
import { errorText, language } from "./i18n";

const REQUEST_MS = 45_000;

export class ApiError extends Error {
  constructor(readonly code: string, readonly status: number) {
    super(code);
  }
}

export async function post<T = any>(path: string, body: unknown = {}): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.code ?? "unavailable", response.status);
  return data as T;
}

function deviceLabel(): string {
  const agent = navigator.userAgent;
  const system = /iPhone|iPad/.test(agent) ? "iOS" : /Android/.test(agent) ? "Android"
    : /Mac OS/.test(agent) ? "macOS" : /Windows/.test(agent) ? "Windows" : /Linux/.test(agent) ? "Linux" : "";
  const browser = /Edg\//.test(agent) ? "Edge" : /Chrome\//.test(agent) ? "Chrome"
    : /Firefox\//.test(agent) ? "Firefox" : /Safari\//.test(agent) ? "Safari" : "";
  return [browser, system].filter(Boolean).join(" / ");
}

export async function registerWith(started: { ceremony: string; options: any }): Promise<void> {
  const credential = await startRegistration({ optionsJSON: started.options });
  await post("/auth/link/verify", { ceremony: started.ceremony, credential, label: deviceLabel() });
}

export async function linkWithCode(code: string): Promise<void> {
  await registerWith(await post("/auth/link/options", { code }));
}

export async function signIn(): Promise<void> {
  const started = await post<{ ceremony: string; options: any }>("/auth/login/options");
  const credential = await startAuthentication({ optionsJSON: started.options });
  await post("/auth/login/verify", { ceremony: started.ceremony, credential, label: deviceLabel() });
}

type Listener = (event: { topic: string; data: any }) => void;

/** One WebSocket with request ids. `onClose` reports why it ended; the page decides whether to sign in again. */
export class Connection {
  private socket!: WebSocket;
  private next = 1;
  private readonly waiting = new Map<number, (answer: any) => void>();
  private readonly listeners = new Set<Listener>();

  constructor(private readonly onEvent: Listener, private readonly onClose: (signedOut: boolean) => void) {}

  /** A screen listens for the topics it subscribed to; the returned function stops it. */
  listen(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  open(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.socket = new WebSocket(`${location.origin.replace(/^http/, "ws")}/ws`);
      this.socket.onopen = () => resolve();
      this.socket.onerror = () => reject(new ApiError("unavailable", 0));
      this.socket.onmessage = (message) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(message.data);
        } catch {
          return; // not JSON: nothing this page can act on
        }
        this.receive(parsed);
      };
      this.socket.onclose = (event) => {
        for (const waiter of this.waiting.values()) waiter({ code: "unavailable" });
        this.waiting.clear();
        this.onClose(event.code === 4010 && event.reason === "signed_out");
      };
    });
  }

  private receive(message: any): void {
    if (message.type === "reload") {
      location.reload();
      return;
    }
    if (message.type === "event") {
      this.onEvent(message);
      for (const listener of this.listeners) {
        try {
          listener(message);
        } catch {
          // One screen's failure must not keep the event from the others.
        }
      }
      return;
    }
    const waiter = this.waiting.get(message.id);
    if (waiter) {
      this.waiting.delete(message.id);
      waiter(message);
    }
  }

  request(type: string, fields: Record<string, unknown> = {}): Promise<any> {
    const id = this.next++;
    return new Promise((resolve) => {
      if (this.socket.readyState !== WebSocket.OPEN) {
        resolve({ code: "unavailable", messages: [errorText("unavailable")] });
        return;
      }
      // Never leave a screen waiting for good: the link can drop an answer, and the server can go away.
      const timer = setTimeout(() => {
        this.waiting.delete(id);
        resolve({ code: "timeout", messages: [errorText("timeout")] });
      }, REQUEST_MS);
      this.waiting.set(id, (answer) => {
        clearTimeout(timer);
        resolve(answer);
      });
      this.socket.send(JSON.stringify({ v: WEB_PROTOCOL, id, type, lang: language, ...fields }));
    });
  }

  close(): void {
    this.socket.onclose = null;
    this.socket.close();
  }
}
