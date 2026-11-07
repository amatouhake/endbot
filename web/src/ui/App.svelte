<script lang="ts">
  import { ApiError, Connection, linkWithCode, post, registerWith, signIn } from "./api";
  import Bots from "./Bots.svelte";
  import Devices from "./Devices.svelte";
  import Dialog from "./Dialog.svelte";
  import { cancelDialog } from "./dialogs.svelte";
  import Groups from "./Groups.svelte";
  import History from "./History.svelte";
  import { errorText, t } from "./i18n";
  import { href, navigate, route, type Section } from "./route.svelte";
  import Scripts from "./Scripts.svelte";
  import SignIn from "./SignIn.svelte";

  const TABS: [Section, "tabBots" | "tabScripts" | "tabGroups" | "tabHistory"][] = [
    ["bots", "tabBots"], ["scripts", "tabScripts"], ["groups", "tabGroups"], ["history", "tabHistory"],
  ];
  const RETRY_FIRST_MS = 3000;
  const RETRY_LONGEST_MS = 30_000;
  // Each read is one Worker request; often enough to see a server start or stop without reloading (step 10).
  const DISPLAY_EVERY_MS = 30_000;

  let view = $state<"loading" | "signedOut" | "signedIn">("loading");
  let display = $state<{ displayName?: string; bds?: string }>({});
  let site = $state<{ name: string; mode: string } | null>(null);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let offline = $state(false);
  let status = $state.raw<any>(null);
  let log = $state.raw<any[]>([]);
  let devicesRevision = $state(0);
  let connection = $state.raw<Connection | null>(null);
  let accountMenu = $state<HTMLDetailsElement>();
  let retry: ReturnType<typeof setTimeout> | undefined;
  let retryDelay = RETRY_FIRST_MS;

  // A tab reopens on what was chosen there, and stays built, so a draft or a search survives looking elsewhere.
  let chosen = $state<Record<string, string | null>>({});
  let visited = $state<Record<string, boolean>>({});
  $effect(() => {
    chosen[route.section] = route.item;
    visited[route.section] = true;
  });

  // The sign-in page asks again now and then, while anyone can see it, instead of keeping what it saw on opening.
  $effect(() => {
    if (view !== "signedOut") return;
    const timer = setInterval(() => {
      if (document.hidden) return;
      void fetch("/api/display").then((response) => response.json()).then((value) => {
        if (view === "signedOut") display = value;
      }).catch(() => {});
    }, DISPLAY_EVERY_MS);
    return () => clearInterval(timer);
  });

  function explain(failure: unknown): string {
    if (failure instanceof ApiError) return errorText(failure.code);
    if (failure instanceof Error && failure.name === "NotAllowedError") return errorText("cancelled");
    return errorText("generic");
  }

  async function act(action: () => Promise<void>): Promise<void> {
    busy = true;
    error = "";
    try {
      await action();
    } catch (failure) {
      error = explain(failure);
    } finally {
      busy = false;
    }
  }

  function onEvent(event: { topic: string; data: any }): void {
    if (event.topic === "log") log = [event.data, ...log].slice(0, 200);
    else if (event.topic === "status") status = event.data;
    else if (event.topic === "account" && event.data.kind === "passkey_added") {
      notice = t("passkeyAdded");
      devicesRevision += 1;
    }
  }

  /** Everything that belongs to the account: the next person on this page must not see it. */
  function forgetAccount(): void {
    clearTimeout(retry);
    retryDelay = RETRY_FIRST_MS;
    cancelDialog();
    status = null;
    log = [];
    notice = "";
    offline = false;
    chosen = {};
    visited = {};
    navigate("bots", null, true);
  }

  async function showSignIn(): Promise<void> {
    connection = null;
    forgetAccount();
    view = "signedOut";
    display = await fetch("/api/display").then((response) => response.json()).catch(() => ({}));
    site = await post("/auth/info").then((info) => info.site).catch(() => null);
  }

  function retryLater(): void {
    clearTimeout(retry);
    retry = setTimeout(() => void reconnect(), retryDelay);
    retryDelay = Math.min(retryDelay * 2, RETRY_LONGEST_MS);
  }

  /** Back by itself once the server is. A session that ended meanwhile cannot open the socket, so ask plainly. */
  async function reconnect(): Promise<void> {
    if (view !== "signedIn" || await connect()) return;
    try {
      await post("/auth/devices");
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        await showSignIn();
        return;
      }
    }
    retryLater();
  }

  async function connect(): Promise<boolean> {
    const next: Connection = new Connection(onEvent, (signedOut) => {
      if (connection !== next) return;
      if (signedOut) {
        void showSignIn();
      } else if (view === "signedIn") {
        // The old connection stays with the screens, answering "unavailable", so that drafts and selections stay too.
        offline = true;
        retryLater();
      }
    });
    try {
      await next.open();
    } catch {
      return false;
    }
    const answer = await next.request("query", { what: "status" });
    if (answer.code !== "ok") {
      next.close();
      return false;
    }
    connection = next;
    status = answer.data;
    view = "signedIn";
    error = "";
    offline = false;
    retryDelay = RETRY_FIRST_MS;
    clearTimeout(retry);
    await next.request("subscribe", { topic: "status" });
    await next.request("subscribe", { topic: "log" });
    const history = await next.request("query", { what: "log" });
    if (history.code === "ok") log = [...history.data].reverse();
    return true;
  }

  async function refreshStatus(): Promise<void> {
    const answer = await connection?.request("query", { what: "status" });
    if (answer?.code === "ok") status = answer.data;
  }

  async function afterSignIn(): Promise<void> {
    connection?.close();
    connection = null;
    forgetAccount();
    if (await connect()) return;
    await showSignIn();
    throw new ApiError("unavailable", 0);
  }

  /** The same person, confirmed again on another session: the screens and what is typed in them stay. */
  async function reauthenticated(): Promise<void> {
    const previous = connection;
    if (await connect()) {
      previous?.close();
      return;
    }
    previous?.close();
    await showSignIn();
    error = errorText("unavailable");
  }

  async function signOut(): Promise<void> {
    closeMenu();
    try {
      await post("/auth/logout");
    } catch (failure) {
      // The session may still be valid: saying "signed out" here would be untrue.
      error = explain(failure);
      return;
    }
    connection?.close();
    await showSignIn();
  }

  function closeMenu(): void {
    if (accountMenu) accountMenu.open = false;
  }

  function closeMenuOutside(event: MouseEvent): void {
    if (accountMenu?.open && !accountMenu.contains(event.target as Node)) closeMenu();
  }

  function follow(event: MouseEvent, section: Section, item: string | null): void {
    // Opening in a new tab stays the browser's.
    if (event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    closeMenu();
    navigate(section, item);
  }

  async function start(): Promise<void> {
    const params = new URLSearchParams(location.search);
    const siteError = params.get("site_error");
    const ceremony = params.get("site_ceremony");
    if (siteError || ceremony) history.replaceState(null, "", "/");
    if (await connect()) return;
    await showSignIn();
    if (siteError) error = errorText(siteError);
    if (ceremony) {
      await act(async () => {
        await registerWith(await post("/auth/site/options", { ceremony }));
        await afterSignIn();
      });
    }
  }

  void start();
</script>

<svelte:window onclick={closeMenuOutside} />

{#if view === "loading"}
  <div class="center" role="status"><div class="spinner"></div>{t("loading")}</div>
{:else if view === "signedOut"}
  <SignIn {display} {site} {error} {busy}
          onsignin={() => act(async () => { await signIn(); await afterSignIn(); })}
          onlink={(code) => act(async () => { await linkWithCode(code); await afterSignIn(); })} />
{:else if connection}
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="dot" class:up={status?.bds === "up"}></span>
        <h1>{status?.displayName || t("title")}</h1>
        <small class="quiet">{status?.bds === "up" ? t("serverUp") : t("serverDown")}</small>
      </div>
      <nav class="tabs">
        {#each TABS as [section, label]}
          <a href={href(section, chosen[section] ?? null)} aria-current={route.section === section ? "page" : undefined}
             onclick={(event) => follow(event, section, chosen[section] ?? null)}>
            {t(label)}
          </a>
        {/each}
      </nav>
      <details class="account" bind:this={accountMenu}>
        <summary aria-label={t("accountMenu")}><span class="who">{status?.gamertag ?? ""}</span> ▾</summary>
        <div class="menu">
          <div class="label">{status?.role === "controller" ? t("roleController") : t("roleScripter")}</div>
          <a href={href("devices")} onclick={(event) => follow(event, "devices", null)}>{t("tabDevices")}</a>
          <button onclick={signOut}>{t("signOut")}</button>
        </div>
      </details>
    </header>

    <div class="content">
      {#if offline}<p class="offline" role="status">{t("reconnecting")}</p>{/if}
      {#if error}<p class="error flush" role="alert">{error}</p>{/if}
      {#if notice}
        <p class="warning flush spread" role="alert">
          <span>{notice}</span>
          <button class="link" onclick={() => { notice = ""; }}>{t("close")}</button>
        </p>
      {/if}
      {#if visited.bots}
        <div class="view" hidden={route.section !== "bots"}>
          <Bots {connection} {status} selected={chosen.bots ?? null} active={route.section === "bots"} onchanged={refreshStatus} />
        </div>
      {/if}
      {#if visited.scripts}
        <div class="view" hidden={route.section !== "scripts"}>
          <Scripts {connection} {log} runs={status?.runs ?? []} selected={chosen.scripts ?? null}
                   active={route.section === "scripts"} onchanged={refreshStatus} />
        </div>
      {/if}
      {#if visited.groups}
        <div class="view" hidden={route.section !== "groups"}>
          <Groups {connection} {status} selected={chosen.groups ?? null} onchanged={refreshStatus} />
        </div>
      {/if}
      {#if route.section === "history"}
        <div class="view"><History {log} /></div>
      {/if}
      {#if route.section === "devices"}
        <div class="view"><Devices revision={devicesRevision} signedIn={reauthenticated} /></div>
      {/if}
    </div>
  </div>
{/if}
<Dialog />
