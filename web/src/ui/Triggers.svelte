<script lang="ts">
  // Trigger list and editor (BOT-022 step 7b): the registry generates the filter fields, the script declaration
  // generates its parameters, and the same field component draws the Bot operation forms.
  import { untrack } from "svelte";
  import type { Connection } from "./api";
  import { confirmDialog } from "./dialogs.svelte";
  import {
    defaultValues, filterFields, filterValues, scriptFields, shownFilterValue, storedArguments, triggerArguments,
  } from "./fields";
  import FieldInput from "./FieldInput.svelte";
  import { formatDate, refusalText, t } from "./i18n";
  import { href, navigate } from "./route.svelte";

  let { connection, selected, active }: {
    connection: Connection;
    selected: string | null;
    active: boolean;
  } = $props();

  const NEW = "new";
  const POLICY_DEFAULTS = { concurrency: "skip", cooldown: 20, startsPerMinute: 10, queue: 4 };
  const CONCURRENCIES = ["skip", "queue", "parallel"] as const;
  const CONCURRENCY_LABELS = {
    skip: "triggerConcurrencySkip",
    queue: "triggerConcurrencyQueue",
    parallel: "triggerConcurrencyParallel",
  } as const;

  let triggers = $state<any[]>([]);
  let kinds = $state<Record<string, any>>({});
  let scripts = $state<any[]>([]);
  let canArm = $state(false);
  let limit = $state(0);
  let unavailable = $state("");
  let search = $state("");

  let current = $state<any>(null);
  let requires = $state<string[]>([]);
  let declaration = $state<any>(null);
  let paramProblem = $state("");
  let filterShown = $state<Record<string, any>>({});
  let argumentValues = $state<Record<string, any>>({});
  let policy = $state({ ...POLICY_DEFAULTS });
  let visibility = $state("private");
  let policyDirty = $state(false);

  let newScript = $state("");
  let newKind = $state("");
  let newName = $state("");
  let newVisibility = $state("private");
  let newDeclaration = $state<any>(null);
  let newFilters = $state<Record<string, any>>({});
  let newArguments = $state<Record<string, any>>({});
  let newProblem = $state("");

  let problem = $state("");
  let notice = $state("");
  let busy = $state(false);
  let scriptTicket = 0;
  let loadTicket = 0;

  const shown = $derived(triggers
    .filter((trigger) => [trigger.name, trigger.ownerName, trigger.script, trigger.kind]
      .some((text) => String(text ?? "").toLowerCase().includes(search.trim().toLowerCase())))
    .toSorted((a, b) => a.name.localeCompare(b.name) || String(a.owner).localeCompare(String(b.owner))));
  const newFilterFields = $derived(newKind && kinds[newKind]
    ? filterFields(kinds[newKind].filters ?? {}, kinds[newKind].require ?? []) : []);
  const newParamFields = $derived(newDeclaration
    ? scriptFields(newDeclaration.schema, newDeclaration.groups, newDeclaration.bots) : []);
  const currentFields = $derived(current && kinds[current.kind]
    ? filterFields(kinds[current.kind].filters ?? {}, kinds[current.kind].require ?? []) : []);
  const currentParams = $derived(declaration
    ? scriptFields(declaration.schema, declaration.groups, declaration.bots) : []);

  function keyOf(trigger: any): string {
    return `${trigger.owner}/${trigger.name}`;
  }

  function partsOf(key: string): { owner: string; name: string } | null {
    const at = key.lastIndexOf("/");
    return at <= 0 || at === key.length - 1
      ? null : { owner: key.slice(0, at), name: key.slice(at + 1) };
  }

  function declarationText(needs: string[]): string {
    return t("runRequires", { list: needs.length ? needs.join(", ") : t("triggerBasicOnly") });
  }

  function concurrencyLabel(value: string): string {
    return value in CONCURRENCY_LABELS ? t(CONCURRENCY_LABELS[value as keyof typeof CONCURRENCY_LABELS]) : value;
  }

  function statusLabel(trigger: any): string {
    if (trigger.status === "armed") return t("triggerArmed");
    if (trigger.status === "paused") return t("triggerPaused");
    return t("triggerDisabled");
  }

  function statusText(trigger: any): string {
    return trigger.reason ? `${statusLabel(trigger)}: ${trigger.reason}` : statusLabel(trigger);
  }

  function statsLine(trigger: any): string {
    const stats = trigger.stats ?? {};
    return t("triggerStatsLine", {
      starts: stats.starts ?? 0,
      missed: stats.missed ?? 0,
      skipped: stats.skipped ?? 0,
      last: stats.lastStart ? formatDate(stats.lastStart) : t("triggerNever"),
    });
  }

  function upsert(trigger: any): void {
    const at = triggers.findIndex((item) => item.owner === trigger.owner && item.name === trigger.name);
    triggers = at < 0 ? [...triggers, trigger] : triggers.map((item, index) => index === at ? trigger : item);
  }

  /** A stored change from an action or a push: the list always hears it, the open page only about its own trigger. */
  function applyChanged(trigger: any): void {
    upsert(trigger);
    if (selected === keyOf(trigger)) {
      current = trigger;
      setPolicy(trigger);
    }
  }

  async function loadKinds(): Promise<void> {
    if (Object.keys(kinds).length) return;
    const answer = await connection.request("event.kinds");
    if (answer.code === "ok") kinds = answer.data.kinds;
  }

  async function loadScripts(): Promise<void> {
    const answer = await connection.request("script.list");
    if (answer.code === "ok") scripts = answer.data.scripts;
  }

  async function refresh(): Promise<void> {
    const answer = await connection.request("trigger.list");
    if (answer.code !== "ok") {
      unavailable = answer.messages?.[0] ?? refusalText(answer.code);
      triggers = [];
      canArm = false;
      return;
    }
    unavailable = "";
    triggers = answer.data.triggers;
    canArm = answer.data.canArm && answer.data.limit > 0;
    limit = answer.data.limit;
    if (canArm) await loadKinds();
  }

  function setPolicy(trigger: any): void {
    policy = {
      concurrency: trigger.policy?.concurrency ?? POLICY_DEFAULTS.concurrency,
      cooldown: trigger.policy?.cooldown ?? POLICY_DEFAULTS.cooldown,
      startsPerMinute: trigger.policy?.startsPerMinute ?? POLICY_DEFAULTS.startsPerMinute,
      queue: trigger.policy?.queue ?? POLICY_DEFAULTS.queue,
    };
    visibility = trigger.visibility === "shared" ? "shared" : "private";
    policyDirty = false;
  }

  function shownFilter(trigger: any): Record<string, any> {
    const values: Record<string, any> = {};
    const spec = kinds[trigger.kind];
    if (!spec) return values;
    for (const field of filterFields(spec.filters ?? {}, spec.require ?? [])) {
      values[field.name] = shownFilterValue(field, trigger.filter ?? {});
    }
    return values;
  }

  async function loadDeclaration(script: string, ticket: number): Promise<void> {
    const answer = await connection.request("script.declaration", { name: script });
    if (ticket !== loadTicket) return;
    if (answer.code !== "ok") {
      declaration = null;
      paramProblem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    declaration = answer.data;
    argumentValues = storedArguments(answer.data.schema, current?.arguments ?? []);
  }

  async function show(key: string | null): Promise<void> {
    const ticket = ++loadTicket;
    current = null;
    requires = [];
    declaration = null;
    paramProblem = "";
    filterShown = {};
    argumentValues = {};
    policyDirty = false;
    problem = "";
    notice = "";
    if (key === NEW) {
      newScript = "";
      newKind = "";
      newName = "";
      newVisibility = "private";
      newDeclaration = null;
      newFilters = {};
      newArguments = {};
      newProblem = "";
      await loadScripts();
      await loadKinds();
      return;
    }
    if (key === null) return;
    const parts = partsOf(key);
    if (!parts) {
      problem = t("triggerMissing");
      return;
    }
    const answer = await connection.request("trigger.get", { name: parts.name, owner: parts.owner });
    if (ticket !== loadTicket) return;
    if (answer.code !== "ok") {
      problem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    current = answer.data.trigger;
    requires = answer.data.requires ?? [];
    setPolicy(current);
    await loadKinds();
    filterShown = shownFilter(current);
    await loadDeclaration(current.script, ticket);
  }

  async function chooseScript(script: string): Promise<void> {
    const ticket = ++scriptTicket;
    newScript = script;
    newDeclaration = null;
    newArguments = {};
    paramProblem = "";
    newProblem = "";
    if (!script) return;
    const answer = await connection.request("script.declaration", { name: script });
    if (ticket !== scriptTicket) return;
    if (answer.code !== "ok") {
      paramProblem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    newDeclaration = answer.data;
    newArguments = defaultValues(scriptFields(answer.data.schema, answer.data.groups, answer.data.bots));
  }

  function chooseKind(kind: string): void {
    newKind = kind;
    newFilters = {};
    newProblem = "";
    const spec = kinds[kind];
    if (spec) newFilters = defaultValues(filterFields(spec.filters ?? {}, spec.require ?? []));
  }

  async function create(): Promise<void> {
    newProblem = "";
    if (!newScript || !newKind) {
      newProblem = t("triggerChooseScriptKind");
      return;
    }
    if (!newDeclaration) {
      newProblem = paramProblem || t("triggerNoDeclaration");
      return;
    }
    const needs = newDeclaration.requires ?? [];
    if (!(await confirmDialog(t("triggerArmTitle", { name: newName.trim() || newScript }), t("triggerArm"),
      { message: declarationText(needs) }))) return;
    busy = true;
    const answer = await connection.request("trigger.create", {
      script: newScript,
      kind: newKind,
      filter: filterValues(newFilterFields, newFilters),
      arguments: triggerArguments(newDeclaration.schema, newArguments),
      ...(newName.trim() ? { name: newName.trim() } : {}),
      visibility: newVisibility,
    });
    busy = false;
    if (answer.code !== "ok") {
      newProblem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    const created = answer.data.trigger;
    upsert(created);
    notice = answer.messages?.[0] ?? t("triggerCreated");
    navigate("triggers", keyOf(created), true);
  }

  async function disable(): Promise<void> {
    const target = current;
    if (!target) return;
    busy = true;
    problem = "";
    notice = "";
    const answer = await connection.request("trigger.disable", { name: target.name, owner: target.owner });
    busy = false;
    if (answer.code !== "ok") {
      problem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    applyChanged(answer.data.trigger);
    notice = t("triggerDisabledDone");
  }

  async function enable(): Promise<void> {
    const target = current;
    if (!target) return;
    busy = true;
    problem = "";
    notice = "";
    const found = await connection.request("trigger.get", { name: target.name, owner: target.owner });
    busy = false;
    if (found.code !== "ok") {
      problem = found.messages?.[0] ?? refusalText(found.code);
      return;
    }
    requires = found.data.requires ?? [];
    if (!(await confirmDialog(t("triggerEnableTitle", { name: target.name }), t("triggerEnable"),
      { message: declarationText(requires) }))) return;
    busy = true;
    const answer = await connection.request("trigger.enable", { name: target.name, owner: target.owner });
    busy = false;
    if (answer.code !== "ok") {
      problem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    applyChanged(answer.data.trigger);
    notice = t("triggerEnabledDone");
  }

  async function remove(): Promise<void> {
    const target = current;
    if (!target) return;
    if (!(await confirmDialog(t("triggerRemoveConfirm", { name: target.name }), t("delete"), { danger: true }))) return;
    busy = true;
    problem = "";
    notice = "";
    const answer = await connection.request("trigger.remove", { name: target.name, owner: target.owner });
    busy = false;
    if (answer.code !== "ok") {
      problem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    triggers = triggers.filter((item) => item.owner !== target.owner || item.name !== target.name);
    navigate("triggers", null, true);
    await refresh();
    notice = t("triggerRemoved");
  }

  async function savePolicy(): Promise<void> {
    const target = current;
    if (!target?.own) return;
    busy = true;
    problem = "";
    notice = "";
    const answer = await connection.request("trigger.update", {
      name: target.name,
      owner: target.owner,
      policy: {
        concurrency: policy.concurrency,
        cooldown: Number(policy.cooldown),
        startsPerMinute: Number(policy.startsPerMinute),
        queue: Number(policy.queue),
      },
      visibility,
    });
    busy = false;
    if (answer.code !== "ok") {
      problem = answer.messages?.[0] ?? refusalText(answer.code);
      return;
    }
    applyChanged(answer.data.trigger);
    notice = t("triggerPolicySaved");
  }

  // On arriving, and again whenever the connection came back.
  $effect(() => {
    if (active && connection) untrack(() => void refresh());
  });

  // The address says which trigger: this also answers Back, Forward and a reload.
  $effect(() => {
    const wanted = selected;
    const live = connection;
    if (active && live) untrack(() => void show(wanted));
  });

  // Live state and stats for the open trigger, and nothing left subscribed after leaving it.
  $effect(() => {
    const wanted = selected;
    if (!active || !wanted || wanted === NEW) return;
    const parts = partsOf(wanted);
    if (!parts) return;
    const topic = `trigger:${parts.owner}/${parts.name}`;
    const stop = connection.listen((event) => {
      if (event.topic !== topic) return;
      if (event.data?.removed) {
        // Removed elsewhere (another page, the game): leave its page and drop it from the list.
        triggers = triggers.filter((item) => item.owner !== parts.owner || item.name !== parts.name);
        if (selected === wanted) {
          navigate("triggers", null, true);
          notice = t("triggerRemoved");
        }
        return;
      }
      if (!event.data?.trigger) return;
      const trigger = event.data.trigger;
      if (trigger.owner !== parts.owner || trigger.name !== parts.name) return;
      upsert(trigger);
      if (selected === keyOf(trigger)) {
        current = trigger;
        if (!policyDirty) setPolicy(trigger);
      }
    });
    void connection.request("subscribe", { topic });
    return () => {
      stop();
      void connection.request("unsubscribe", { topic });
    };
  });

  function statusClass(trigger: any): string | undefined {
    return trigger.status === "armed" ? "online" : undefined;
  }

  function badStatus(trigger: any): boolean {
    return trigger.status === "disabled";
  }
</script>

<div class="split" class:showing={selected !== null}>
  <div class="list-pane">
    <div class="list-tools">
      <h2>{t("tabTriggers")}</h2>
      <div class="row">
        <input class="grow" type="search" bind:value={search} placeholder={t("search")} aria-label={t("search")} />
        {#if canArm}<button disabled={busy} onclick={() => navigate("triggers", NEW)}>{t("new")}</button>{/if}
      </div>
    </div>
    <div class="list-scroll">
      {#if unavailable}
        <p class="empty">{unavailable}</p>
      {:else if triggers.length === 0}
        <p class="empty">{t("noTriggers")}</p>
      {:else if shown.length === 0}
        <p class="empty">{t("noMatches")}</p>
      {:else}
        {#each shown as trigger (keyOf(trigger))}
          <a class="item trigger" class:current={selected === keyOf(trigger)} href={href("triggers", keyOf(trigger))}
             aria-current={selected === keyOf(trigger) ? "true" : undefined}
             onclick={(event) => { event.preventDefault(); problem = ""; notice = ""; navigate("triggers", keyOf(trigger)); }}>
            <span class="name">{trigger.name}
              <small>{trigger.ownerName ?? trigger.owner} · {trigger.script} · {trigger.kind}
                {#if trigger.visibility === "shared"}· {t("scriptShared")}{/if}</small>
              <small>{statsLine(trigger)}</small>
            </span>
            <span class="tag" class:online={statusClass(trigger) === "online"} class:bad={badStatus(trigger)}>
              {statusText(trigger)}
            </span>
          </a>
        {/each}
      {/if}
    </div>
    {#if canArm}
      <div class="list-foot">
        <p class="quiet">{t("triggerLimit", { count: limit })}</p>
      </div>
    {:else if !unavailable && triggers.length > 0}
      <div class="list-foot">
        <p class="quiet">{t("triggerOff")}</p>
      </div>
    {/if}
  </div>

  <div class="detail">
    <button class="back" onclick={() => navigate("triggers")}>← {t("back")}</button>
    {#if selected === NEW}
      <section class="card">
        <h2>{t("triggerNewTitle")}</h2>
        {#if !canArm}
          <p class="empty">{unavailable || t("triggerUnavailable")}</p>
        {:else if scripts.length === 0}
          <p class="empty">{t("triggerNoScripts")}</p>
        {:else if Object.keys(kinds).length === 0}
          <p class="empty">{t("triggerNoKinds")}</p>
        {:else}
          {#if newProblem}<p class="error" role="alert">{newProblem}</p>{/if}
          <form onsubmit={(event) => { event.preventDefault(); void create(); }}>
            <div class="form-grid">
              <label class="field">{t("triggerScript")}
                <select value={newScript} required onchange={(event) => void chooseScript(event.currentTarget.value)}>
                  <option value="">—</option>
                  {#each scripts as script (script.name)}<option value={script.name}>{script.name}</option>{/each}
                </select>
              </label>
              <label class="field">{t("triggerKind")}
                <select value={newKind} required onchange={(event) => chooseKind(event.currentTarget.value)}>
                  <option value="">—</option>
                  {#each Object.keys(kinds).toSorted() as kind (kind)}<option value={kind}>{kind}</option>{/each}
                </select>
              </label>
              <label class="field">{t("triggerName")}
                <input bind:value={newName} maxlength="32" spellcheck="false"
                       placeholder={newScript || t("triggerNameHint")} />
              </label>
              <label class="field">{t("triggerVisibility")}
                <select bind:value={newVisibility}>
                  <option value="private">{t("scriptPrivate")}</option>
                  <option value="shared">{t("scriptShared")}</option>
                </select>
              </label>
            </div>
            {#if newScript && paramProblem}<p class="error" role="alert">{paramProblem}</p>{/if}
            {#if newDeclaration}
              {#if newKind && newFilterFields.length}
                <h3 class="gap-top">{t("triggerFilter")}</h3>
                <div class="form-grid">
                  {#each newFilterFields as field (field.name)}
                    <FieldInput {field} value={newFilters[field.name]}
                                onchange={(next) => { newFilters[field.name] = next; }} />
                  {/each}
                </div>
              {/if}
              <h3 class="gap-top">{t("triggerArguments")}</h3>
              {#if newParamFields.length}
                <div class="form-grid">
                  {#each newParamFields as field (field.name)}
                    <FieldInput {field} value={newArguments[field.name]} showOptional={false}
                                onchange={(next) => { newArguments[field.name] = next; }} />
                  {/each}
                </div>
              {:else}
                <p class="quiet">{t("triggerNoArguments")}</p>
              {/if}
              <p class="quiet gap-top">{declarationText(newDeclaration.requires ?? [])}</p>
              <div class="dialog-actions">
                <button type="button" onclick={() => navigate("triggers")}>{t("cancel")}</button>
                <button class="primary" disabled={busy || !newKind}>{t("triggerArm")}</button>
              </div>
            {/if}
          </form>
        {/if}
      </section>
    {:else if selected}
      {#if problem}<p class="error" role="alert">{problem}</p>{/if}
      {#if notice}<p class="good" role="status">{notice}</p>{/if}
      {#if !current}
        {#if !problem}<p class="empty">{t("loading")}</p>{/if}
      {:else}
        <section class="card">
          <div class="spread">
            <h2>{current.name}</h2>
            <span class="tag" class:online={statusClass(current) === "online"} class:bad={badStatus(current)}>
              {statusLabel(current)}
            </span>
          </div>
          <p class="quiet gap-top">{current.ownerName ?? current.owner} · {current.script} · {current.kind}</p>
          {#if current.reason}<p class="warning gap-top">{current.reason}</p>{/if}
          {#if !current.own}
            <p class="quiet gap-top">{t("triggerSharedReadOnly", { owner: current.ownerName ?? current.owner })}</p>
          {:else}
            <div class="row gap-top">
              {#if current.status === "armed"}
                <button disabled={busy} onclick={disable}>{t("triggerDisable")}</button>
              {:else}
                <button class="primary" disabled={busy} onclick={enable}>{t("triggerEnable")}</button>
              {/if}
              <button class="danger" disabled={busy} onclick={remove}>{t("triggerRemove")}</button>
            </div>
          {/if}
        </section>

        <section class="card">
          <h2>{t("triggerFilter")}</h2>
          {#if currentFields.length}
            <div class="form-grid">
              {#each currentFields as field (field.name)}
                <FieldInput {field} disabled value={filterShown[field.name]} onchange={() => {}} />
              {/each}
            </div>
          {:else}
            <p class="empty">{t("triggerKindUnknown")}</p>
          {/if}
        </section>

        <section class="card">
          <h2>{t("triggerArguments")}</h2>
          {#if currentParams.length}
            <div class="form-grid">
              {#each currentParams as field (field.name)}
                <FieldInput {field} disabled value={argumentValues[field.name]} onchange={() => {}} />
              {/each}
            </div>
          {:else if paramProblem}
            <p class="quiet">{paramProblem}</p>
          {:else}
            <p class="quiet">{t("triggerNoArguments")}</p>
          {/if}
          <p class="quiet">{t("triggerApproved", { list: (current.approved?.declaration ?? []).join(", ") || t("triggerBasicOnly") })}</p>
        </section>

        <section class="card">
          <h2>{t("triggerStats")}</h2>
          <dl class="facts">
            <div><dt>{t("triggerStarts")}</dt><dd>{current.stats?.starts ?? 0}</dd></div>
            <div><dt>{t("triggerMissed")}</dt><dd>{current.stats?.missed ?? 0}</dd></div>
            <div><dt>{t("triggerSkipped")}</dt><dd>{current.stats?.skipped ?? 0}</dd></div>
            <div><dt>{t("triggerLastStart")}</dt><dd>{current.stats?.lastStart ? formatDate(current.stats.lastStart) : t("triggerNever")}</dd></div>
            {#if current.stats?.lastError}<div><dt>{t("triggerLastError")}</dt><dd>{current.stats.lastError}</dd></div>{/if}
          </dl>
        </section>

        <section class="card">
          <h2>{t("triggerPolicy")}</h2>
          {#if current.own}
            <form onsubmit={(event) => { event.preventDefault(); void savePolicy(); }}>
              <div class="form-grid">
                <label class="field">{t("triggerConcurrency")}
                  <select bind:value={policy.concurrency} onchange={() => { policyDirty = true; }}>
                    {#each CONCURRENCIES as name (name)}
                      <option value={name}>{t(CONCURRENCY_LABELS[name])}</option>
                    {/each}
                  </select>
                </label>
                <label class="field">{t("triggerCooldown")}
                  <input type="number" min="0" max="72000" step="1" bind:value={policy.cooldown}
                         oninput={() => { policyDirty = true; }} />
                  <small class="quiet">{t("triggerCooldownHint")}</small>
                </label>
                <label class="field">{t("triggerStartsPerMinute")}
                  <input type="number" min="1" max="60" step="1" bind:value={policy.startsPerMinute}
                         oninput={() => { policyDirty = true; }} />
                  <small class="quiet">{t("triggerStartsPerMinuteHint")}</small>
                </label>
                <label class="field">{t("triggerQueue")}
                  <input type="number" min="1" max="16" step="1" bind:value={policy.queue}
                         oninput={() => { policyDirty = true; }} />
                  <small class="quiet">{t("triggerQueueHint")}</small>
                </label>
                <label class="field">{t("triggerVisibility")}
                  <select bind:value={visibility} onchange={() => { policyDirty = true; }}>
                    <option value="private">{t("scriptPrivate")}</option>
                    <option value="shared">{t("scriptShared")}</option>
                  </select>
                </label>
              </div>
              <div class="row gap-top">
                <button class="primary" disabled={busy}>{t("save")}</button>
              </div>
            </form>
          {:else}
            <dl class="facts">
              <div><dt>{t("triggerConcurrency")}</dt><dd>{concurrencyLabel(policy.concurrency)}</dd></div>
              <div><dt>{t("triggerCooldown")}</dt><dd>{policy.cooldown}</dd></div>
              <div><dt>{t("triggerStartsPerMinute")}</dt><dd>{policy.startsPerMinute}</dd></div>
              <div><dt>{t("triggerQueue")}</dt><dd>{policy.queue}</dd></div>
              <div><dt>{t("triggerVisibility")}</dt><dd>{current.visibility === "shared" ? t("scriptShared") : t("scriptPrivate")}</dd></div>
            </dl>
          {/if}
        </section>
      {/if}
    {:else}
      <p class="empty">{t("triggerPick")}</p>
    {/if}
  </div>
</div>
