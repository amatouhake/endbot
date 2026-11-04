<script lang="ts">
  import { ApiError, Connection, linkWithCode, post, registerWith, signIn } from "./api";
  import Bots from "./Bots.svelte";
  import { errorText, formatDate, t } from "./i18n";
  import Scripts from "./Scripts.svelte";

  type Tab = "status" | "bots" | "scripts" | "log" | "devices";

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
  let connection = $state<Connection | null>(null);

  /** The hyphen is ours to place: letters and digits are all anyone has to type or paste. */
  function formatCode(event: Event): void {
    const field = event.currentTarget as HTMLInputElement;
    const symbols = field.value.toUpperCase().replace(/[^0-9A-Z]/g, "").slice(0, 6);
    code = symbols.length > 3 ? `${symbols.slice(0, 3)}-${symbols.slice(3)}` : symbols;
    field.value = code;
  }

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
    if (next !== "devices" && next !== "log") void refreshStatus();
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
          <input id="code" class="code-field" value={code} oninput={formatCode} autocomplete="one-time-code"
                 autocapitalize="characters" autocorrect="off" spellcheck="false" placeholder="K7Q-4M2" />
          <button disabled={busy || code.replace("-", "").length < 6}>{t("linkButton")}</button>
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
      {#each [["status", t("tabStatus")], ["bots", t("tabBots")], ["scripts", t("tabScripts")], ["log", t("tabLog")],
              ["devices", t("tabDevices")]] as [key, label]}
        <button class:current={tab === key} aria-current={tab === key ? "page" : undefined}
                onclick={() => show(key as Tab)}><span data-text={label}>{label}</span></button>
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
    {:else if tab === "bots" && connection}
      <Bots {connection} {status} onchanged={refreshStatus} />
    {:else if tab === "scripts" && connection}
      <Scripts {connection} runs={status?.runs ?? []} onchanged={refreshStatus} />
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
