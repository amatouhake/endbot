<script lang="ts">
  import { ApiError, Connection, linkWithCode, post, registerWith, signIn } from "./api";
  import { errorText, formatDate, t } from "./i18n";

  type Tab = "status" | "log" | "devices";

  let view = $state<"loading" | "signedOut" | "signedIn">("loading");
  let tab = $state<Tab>("status");
  let display = $state<{ displayName?: string; bds?: string }>({});
  let site = $state<{ name: string; mode: string } | null>(null);
  let code = $state("");
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let reauthNeeded = $state(false);
  let status = $state<any>(null);
  let log = $state<any[]>([]);
  let devices = $state<any>(null);
  let deviceCode = $state("");
  let connection: Connection | null = null;

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
    else if (event.topic === "status" && status) {
      status.bds = event.data.bds;
      void refreshStatus();
    } else if (event.topic === "account" && event.data.kind === "passkey_added") {
      notice = t("passkeyAdded");
      if (tab === "devices") void loadDevices();
    }
  }

  /** Everything that belongs to the account: the next person on this page must not see it. */
  function forgetAccount(): void {
    status = null;
    log = [];
    devices = null;
    deviceCode = "";
    notice = "";
    reauthNeeded = false;
    tab = "status";
  }

  function onClose(signedOut: boolean): void {
    connection = null;
    status = null;
    if (signedOut) {
      forgetAccount();
      view = "signedOut";
      void loadSignInPage();
    } else if (view === "signedIn") {
      error = t("webOffline");
    }
  }

  async function connect(): Promise<boolean> {
    const next = new Connection(onEvent, onClose);
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

  async function loadSignInPage(): Promise<void> {
    display = await fetch("/api/display").then((response) => response.json()).catch(() => ({}));
    site = await post("/auth/info").then((info) => info.site).catch(() => null);
  }

  async function afterSignIn(): Promise<void> {
    code = "";
    forgetAccount();
    connection?.close();
    connection = null;
    if (!(await connect())) error = errorText("unavailable");
  }

  async function loadDevices(): Promise<void> {
    devices = await post("/auth/devices");
  }

  function show(next: Tab): void {
    tab = next;
    if (next === "devices") void act(loadDevices);
    if (next === "status") void refreshStatus();
  }

  function needsReauth(failure: unknown): boolean {
    return failure instanceof ApiError && failure.code === "reauthenticate";
  }

  async function reauthGuard(action: () => Promise<void>): Promise<void> {
    busy = true;
    error = "";
    try {
      await action();
    } catch (failure) {
      if (needsReauth(failure)) reauthNeeded = true;
      else error = explain(failure);
    } finally {
      busy = false;
    }
  }

  async function signOut(): Promise<void> {
    try {
      await post("/auth/logout");
    } catch (failure) {
      // The session may still be valid: saying "signed out" here would be untrue.
      error = explain(failure);
      return;
    }
    connection?.close();
    connection = null;
    forgetAccount();
    view = "signedOut";
    await loadSignInPage();
  }

  async function start(): Promise<void> {
    const params = new URLSearchParams(location.search);
    const siteError = params.get("site_error");
    const ceremony = params.get("site_ceremony");
    if (siteError || ceremony) history.replaceState(null, "", "/");
    if (siteError) error = errorText(siteError);
    if (await connect()) return;
    view = "signedOut";
    await loadSignInPage();
    if (ceremony) {
      await act(async () => {
        await registerWith(await post("/auth/site/options", { ceremony }));
        await afterSignIn();
      });
    }
  }

  void start();
</script>

<main class:wide={view === "signedIn"}>
  {#if view === "loading"}
    <p class="quiet" aria-busy="true">…</p>
  {:else if view === "signedOut"}
    <h1>{display.displayName || t("title")}</h1>
    <p class="server">
      <span class="dot" class:up={display.bds === "up"}></span>
      {display.bds === "up" ? t("serverUp") : t("serverDown")}
    </p>
    {#if error}<p class="error" role="alert">{error}</p>{/if}

    <section class="card">
      <button class="primary" disabled={busy} onclick={() => act(async () => { await signIn(); await afterSignIn(); })}>
        {t("signIn")}
      </button>
      {#if site}
        <a class="button" href="/auth/site/start">{t("signInSite", { name: site.name })}</a>
      {/if}
    </section>

    <section class="card">
      <h2>{t("linkTitle")}</h2>
      <p class="quiet">{t("linkHelp")}</p>
      <form onsubmit={(event) => { event.preventDefault(); void act(async () => { await linkWithCode(code); await afterSignIn(); }); }}>
        <label for="code">{t("linkCode")}</label>
        <div class="row">
          <input id="code" bind:value={code} autocomplete="one-time-code" autocapitalize="characters"
                 spellcheck="false" placeholder="K7Q-4M2" maxlength="16" />
          <button disabled={busy || !code.trim()}>{t("linkButton")}</button>
        </div>
      </form>
    </section>
  {:else}
    <header>
      <div>
        <h1>{status?.displayName || t("title")}</h1>
        {#if status}
          <p class="server">
            <span class="dot" class:up={status.bds === "up"}></span>
            {status.bds === "up" ? t("serverUp") : t("serverDown")}
          </p>
        {/if}
      </div>
      <button class="link" onclick={signOut}>{t("signOut")}</button>
    </header>

    <nav>
      {#each [["status", t("tabStatus")], ["log", t("tabLog")], ["devices", t("tabDevices")]] as [key, label]}
        <button class:current={tab === key} aria-current={tab === key ? "page" : undefined}
                onclick={() => show(key as Tab)}>{label}</button>
      {/each}
    </nav>

    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if notice}<p class="warning" role="alert">{notice}</p>{/if}

    {#if tab === "status"}
      {#if status}
        <p class="quiet">
          {t("signedInAs", { name: status.gamertag })} ·
          {status.role === "controller" ? t("roleController") : t("roleScripter")}
        </p>
        <div class="grid">
          <section class="card">
            <h2>{t("bots")}</h2>
            {#if status.bots === null}
              <p class="quiet">{t("botsUnknown")}</p>
            {:else if status.bots.length === 0}
              <p class="quiet">{t("noBots")}</p>
            {:else}
              <ul>
                {#each status.bots as bot}
                  <li><span>{bot.name}</span><span class="tag" class:good={bot.state === "online"}>{bot.state}</span></li>
                {/each}
              </ul>
            {/if}
          </section>
          <section class="card">
            <h2>{t("groups")}</h2>
            {#if status.groups.length === 0}
              <p class="quiet">{t("noGroups")}</p>
            {:else}
              <ul>
                {#each status.groups as group}
                  <li><span>#{group.name}</span><span class="quiet">{t("members", { count: group.members })}</span></li>
                {/each}
              </ul>
            {/if}
          </section>
          <section class="card">
            <h2>{t("runs")}</h2>
            {#if status.runs.length === 0}
              <p class="quiet">{t("noRuns")}</p>
            {:else}
              <ul>
                {#each status.runs as run}
                  <li><span>{run.name}</span><code>{run.id}</code></li>
                {/each}
              </ul>
            {/if}
          </section>
        </div>
      {:else}
        <button onclick={() => act(async () => { if (!(await connect())) throw new ApiError("unavailable", 0); })}>
          {t("retry")}
        </button>
      {/if}
    {:else if tab === "log"}
      <section class="card">
        <p class="quiet">{t("logNote")}</p>
        {#if log.length === 0}
          <p class="quiet">{t("logEmpty")}</p>
        {:else}
          <ol class="log">
            {#each log as entry}
              <li class:warn={entry.level !== "info"}>
                <time>{formatDate(entry.time)}</time>
                {#each entry.lines as line}<span>{line}</span>{/each}
              </li>
            {/each}
          </ol>
        {/if}
      </section>
    {:else}
      {#if reauthNeeded}
        <section class="card">
          <p>{t("reauth")}</p>
          <button class="primary" disabled={busy} onclick={() => act(async () => { await signIn(); await afterSignIn(); await loadDevices(); })}>
            {t("signIn")}
          </button>
        </section>
      {/if}
      <section class="card">
        <h2>{t("addDevice")}</h2>
        <p class="quiet">{t("addDeviceHelp")}</p>
        {#if deviceCode}
          <p class="code">{t("deviceCode", { code: deviceCode })}</p>
          <p class="quiet">{t("deviceCodeNote")}</p>
        {:else}
          <button disabled={busy} onclick={() => reauthGuard(async () => { deviceCode = (await post("/auth/device-code")).code; })}>
            {t("addDevice")}
          </button>
        {/if}
      </section>
      {#if devices}
        <section class="card">
          <h2>{t("passkeys")}</h2>
          <ul>
            {#each devices.passkeys as passkey}
              <li>
                <span>
                  {passkey.label || t("unnamed")}
                  <small class="quiet">
                    {t("created", { date: formatDate(passkey.created) })} ·
                    {passkey.lastUsed ? t("lastUsed", { date: formatDate(passkey.lastUsed) }) : t("neverUsed")}
                  </small>
                </span>
                <button class="danger" disabled={busy}
                        onclick={() => reauthGuard(async () => { await post("/auth/passkey/remove", { id: passkey.id }); await loadDevices(); })}>
                  {t("remove")}
                </button>
              </li>
            {/each}
          </ul>
        </section>
        <section class="card">
          <h2>{t("sessions")}</h2>
          <ul>
            {#each devices.sessions as session}
              <li>
                <span>
                  {session.label || t("unnamed")}
                  {#if session.current}<span class="tag good">{t("thisDevice")}</span>{/if}
                  {#if session.via === "site"}<span class="tag">{t("viaSite")}</span>{/if}
                  <small class="quiet">{t("lastSeen", { date: formatDate(session.lastSeen) })}</small>
                </span>
                {#if !session.current}
                  <button class="danger" disabled={busy}
                          onclick={() => act(async () => { await post("/auth/session/remove", { id: session.id }); await loadDevices(); })}>
                    {t("remove")}
                  </button>
                {/if}
              </li>
            {/each}
          </ul>
        </section>
      {/if}
    {/if}
  {/if}
</main>

<style>
  :global(:root) {
    color-scheme: light dark;
    --bg: #f6f7f8; --surface: #fff; --text: #1c2024; --quiet: #5c6670; --line: #dde1e5;
    --accent: #1f6feb; --accent-text: #fff; --good: #1a7f37; --bad: #b42318; --warn-bg: #fff4e5; --warn-text: #7a4a00;
    --bad-bg: #fdecea;
  }
  @media (prefers-color-scheme: dark) {
    :global(:root) {
      --bg: #14171a; --surface: #1d2125; --text: #e6e8ea; --quiet: #9aa4ae; --line: #30363c;
      --accent: #4c8dff; --accent-text: #0b1016; --good: #4ac26b; --bad: #ff7b72; --warn-bg: #3a2a10; --warn-text: #f5c56b;
      --bad-bg: #3a1a18;
    }
  }
  :global(body) {
    margin: 0; background: var(--bg); color: var(--text);
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", sans-serif;
  }
  main { max-width: 26rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
  main.wide { max-width: 60rem; }
  h1 { font-size: 1.4rem; margin: 0; }
  h2 { font-size: 1rem; margin: 0 0 .5rem; }
  p { margin: .25rem 0 .75rem; }
  .quiet { color: var(--quiet); }
  small { display: block; font-size: .85rem; }
  .server { display: flex; align-items: center; gap: .45rem; color: var(--quiet); margin: .25rem 0 1rem; }
  .dot { width: .6rem; height: .6rem; border-radius: 50%; background: var(--quiet); flex: none; }
  .dot.up { background: var(--good); }
  header { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
  nav { display: flex; gap: .25rem; border-bottom: 1px solid var(--line); margin-bottom: 1rem; overflow-x: auto; }
  nav button { background: none; border: 0; border-bottom: 2px solid transparent; border-radius: 0; color: var(--quiet); padding: .55rem .9rem; }
  nav button.current { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: .6rem; padding: 1rem; margin-bottom: 1rem; }
  .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); align-items: start; }
  .grid .card { margin: 0; }
  ul, ol { list-style: none; margin: 0; padding: 0; }
  li { display: flex; justify-content: space-between; align-items: center; gap: .75rem; padding: .5rem 0; border-top: 1px solid var(--line); overflow-wrap: anywhere; }
  li:first-child { border-top: 0; }
  button, .button {
    font: inherit; min-height: 2.75rem; padding: .5rem 1rem; border-radius: .45rem; border: 1px solid var(--line);
    background: var(--surface); color: var(--text); cursor: pointer; text-decoration: none; display: inline-flex;
    align-items: center; justify-content: center; box-sizing: border-box;
  }
  button:disabled { opacity: .6; cursor: default; }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); width: 100%; font-weight: 600; }
  .card .button { width: 100%; margin-top: .6rem; }
  button.link { background: none; border: 0; color: var(--accent); padding: .5rem; flex: none; }
  button.danger { color: var(--bad); flex: none; }
  label { display: block; font-size: .9rem; margin-bottom: .25rem; }
  .row { display: flex; gap: .5rem; }
  input {
    font: inherit; flex: 1; min-width: 0; min-height: 2.75rem; padding: .5rem .75rem; border-radius: .45rem;
    border: 1px solid var(--line); background: var(--bg); color: var(--text); letter-spacing: .06em;
  }
  .error { background: var(--bad-bg); color: var(--bad); padding: .6rem .8rem; border-radius: .45rem; }
  .warning { background: var(--warn-bg); color: var(--warn-text); padding: .6rem .8rem; border-radius: .45rem; }
  .tag { font-size: .8rem; padding: .05rem .5rem; border: 1px solid var(--line); border-radius: 1rem; color: var(--quiet); white-space: nowrap; }
  .tag.good { color: var(--good); border-color: var(--good); }
  code { font-size: .85rem; color: var(--quiet); }
  .code { font-size: 1.15rem; font-weight: 600; }
  .log li { display: block; }
  .log li.warn span { color: var(--bad); }
  .log time { display: block; font-size: .8rem; color: var(--quiet); }
  .log span { display: block; }
</style>
