<script lang="ts">
  import type { Connection } from "./api";
  import { diffLines } from "./diff";
  import Editor from "./Editor.svelte";
  import { formatDate, t } from "./i18n";

  let { connection, runs, onchanged }: {
    connection: Connection;
    runs: { id: string; name: string }[];
    onchanged: () => void;
  } = $props();

  const NAME = /^[A-Za-z0-9_-]{1,64}$/;

  let scripts = $state<any[]>([]);
  let canAuthor = $state(false);
  let unavailable = $state("");
  let templates = $state<Record<string, string>>({});
  // The script open in the editor; `revision` null while it has never been saved.
  let open = $state<any>(null);
  let draft = $state("");
  let name = $state("");
  let creating = $state(false);
  let busy = $state(false);
  let problem = $state<{ line?: number; message: string } | null>(null);
  let notice = $state("");
  let conflict = $state<any>(null);
  let comparing = $state<any>(null);
  let runForm = $state<any>(null);
  let values = $state<Record<string, any>>({});

  const dirty = $derived(open !== null && draft !== open.source);

  async function refresh(): Promise<void> {
    const answer = await connection.request("script.list");
    if (answer.code !== "ok") {
      unavailable = answer.messages?.[0] ?? t("error_generic");
      return;
    }
    unavailable = "";
    scripts = answer.data.scripts;
    canAuthor = answer.data.canAuthor;
    if (canAuthor && Object.keys(templates).length === 0) {
      const found = await connection.request("script.templates");
      if (found.code === "ok") templates = found.data;
    }
  }

  $effect(() => {
    void refresh();
  });

  function clear(): void {
    problem = null;
    notice = "";
    conflict = null;
    comparing = null;
    runForm = null;
  }

  function leaveOk(): boolean {
    return !dirty || confirm(t("unsavedLeave"));
  }

  async function select(script: string): Promise<void> {
    if (!leaveOk()) return;
    const answer = await connection.request("script.get", { name: script });
    clear();
    if (answer.code !== "ok") {
      problem = { message: answer.messages?.[0] ?? t("error_generic") };
      return;
    }
    creating = false;
    open = answer.data;
    draft = open.source;
    name = open.name;
  }

  function startNew(source = "params {}\n\n"): void {
    if (!leaveOk()) return;
    clear();
    creating = true;
    name = "";
    open = { name: "", source, revision: null, canEdit: true, history: [], visibility: "private", file: false };
    draft = source;
  }

  function fail(answer: any): void {
    problem = { line: answer.line, message: answer.messages?.[0] ?? t("error_generic") };
  }

  async function check(): Promise<void> {
    busy = true;
    clear();
    const answer = await connection.request("script.compile", { name: name || "draft", source: draft });
    busy = false;
    if (answer.code === "ok") notice = t("compileOk");
    else fail(answer);
  }

  async function save(): Promise<void> {
    if (!NAME.test(name)) {
      problem = { message: t("scriptNameRule") };
      return;
    }
    busy = true;
    clear();
    const answer = await connection.request("script.save", { name, source: draft, revision: open.revision });
    busy = false;
    if (answer.code === "ok") {
      open = answer.data;
      draft = open.source;
      creating = false;
      notice = t("scriptSaved");
      await refresh();
    } else if (answer.code === "conflict") {
      conflict = answer.current;
    } else {
      fail(answer);
    }
  }

  /** Take what someone else saved, keeping nothing of this draft except what the person copies from the comparison. */
  function takeTheirs(): void {
    open = conflict;
    draft = conflict.source;
    conflict = null;
  }

  /** Keep this text on top of what is there now: the next save is a deliberate overwrite, with history kept. */
  function keepMine(): void {
    open = conflict;
    conflict = null;
  }

  async function action(type: string, fields: Record<string, unknown>, after: (answer: any) => Promise<void>): Promise<void> {
    busy = true;
    clear();
    const answer = await connection.request(type, { name: open.name, revision: open.revision, ...fields });
    busy = false;
    if (answer.code === "ok") await after(answer);
    else fail(answer);
  }

  async function reopen(script: string): Promise<void> {
    draft = open.source;
    await select(script);
    await refresh();
  }

  function rename(): void {
    const next = prompt(t("scriptNewName"), open.name);
    if (next && next !== open.name) void action("script.rename", { new: next }, () => reopen(next));
  }

  function copy(): void {
    const next = prompt(t("scriptCopyName"), `${open.name}_copy`);
    if (next) void action("script.copy", { new: next }, () => reopen(next));
  }

  function remove(): void {
    if (!confirm(t("scriptDeleteConfirm", { name: open.name }))) return;
    void action("script.delete", {}, async () => {
      open = null;
      draft = "";
      await refresh();
    });
  }

  function share(): void {
    const visibility = open.visibility === "shared" ? "private" : "shared";
    void action("script.visibility", { visibility }, () => reopen(open.name));
  }

  function restore(index: number): void {
    void action("script.restore", { index }, () => reopen(open.name));
  }

  async function prepareRun(): Promise<void> {
    if (dirty) {
      problem = { message: t("saveBeforeRun") };
      return;
    }
    busy = true;
    clear();
    const answer = await connection.request("script.declaration", { name: open.name });
    busy = false;
    if (answer.code !== "ok") {
      fail(answer);
      return;
    }
    runForm = answer.data;
    values = Object.fromEntries((answer.data.schema as any[])
      .filter((field) => field.default !== undefined)
      .map((field) => [field.name, field.default]));
  }

  function typed(field: any, value: any): any {
    if (field.type === "number" && value !== "" && value !== undefined) return Number(value);
    return value;
  }

  /** What the form chose, so that the run can refuse a target that changed while it was open. */
  function pins(given: Record<string, any>): Record<string, unknown> {
    const chosen: Record<string, unknown> = {};
    for (const field of runForm.schema as any[]) {
      const value = given[field.name];
      if (field.type === "group") {
        const group = runForm.groups.find((item: any) => `#${item.name}` === value);
        if (group) chosen[field.name] = { value, identity: group.identity, members: group.members };
      } else if (field.type === "bot") {
        const bot = runForm.bots.find((item: any) => item.name === value);
        if (bot) chosen[field.name] = { value, identity: bot.identity };
      }
    }
    return chosen;
  }

  async function run(): Promise<void> {
    busy = true;
    const given = Object.fromEntries((runForm.schema as any[])
      .filter((field) => values[field.name] !== undefined && values[field.name] !== "")
      .map((field) => [field.name, typed(field, values[field.name])]));
    const answer = await connection.request("script.run", {
      name: open.name, revision: runForm.revision, requires: runForm.requires, values: given, targets: pins(given),
    });
    busy = false;
    if (answer.code === "ok") {
      runForm = null;
      notice = answer.messages?.[0] ?? "";
      onchanged();
    } else {
      fail(answer);
    }
  }

  async function stop(id: string): Promise<void> {
    const answer = await connection.request("script.stop", { run: id });
    notice = answer.messages?.[0] ?? "";
    onchanged();
  }
</script>

{#if unavailable}
  <section class="card"><p class="quiet">{unavailable}</p></section>
{:else}
  <div class="layout">
    <aside>
      <section class="card">
        <div class="heading">
          <h2>{t("scripts")}</h2>
          {#if canAuthor}<button onclick={() => startNew()}>{t("scriptNew")}</button>{/if}
        </div>
        {#if scripts.length === 0}
          <p class="quiet">{t("noScripts")}</p>
        {:else}
          <ul class="pick">
            {#each scripts as script}
              <li>
                <button class:current={open?.name === script.name} onclick={() => select(script.name)}>
                  <span class="name">{script.name}</span>
                  <small class="quiet">
                    {script.file ? t("scriptFile") : script.own ? t("scriptOwn") : script.ownerName ?? ""}
                    · {script.visibility === "shared" ? t("scriptShared") : t("scriptPrivate")}
                  </small>
                </button>
              </li>
            {/each}
          </ul>
        {/if}
        {#if canAuthor && Object.keys(templates).length}
          <details>
            <summary class="quiet">{t("scriptTemplates")}</summary>
            <ul class="pick">
              {#each Object.entries(templates) as [title, source]}
                <li><button onclick={() => startNew(source)}>{title}</button></li>
              {/each}
            </ul>
          </details>
        {/if}
      </section>
      {#if runs.length}
        <section class="card">
          <h2>{t("runs")}</h2>
          <ul>
            {#each runs as item}
              <li>
                <span>{item.name} <code>{item.id}</code></span>
                <button class="danger" onclick={() => stop(item.id)}>{t("stop")}</button>
              </li>
            {/each}
          </ul>
        </section>
      {/if}
    </aside>

    <div class="main">
      {#if !open}
        <section class="card"><p class="quiet">{t("scriptPick")}</p></section>
      {:else}
        <section class="card">
          <div class="heading">
            {#if creating}
              <label class="inline">{t("scriptName")}
                <input bind:value={name} spellcheck="false" maxlength="64" placeholder="my_script" />
              </label>
            {:else}
              <h2>{open.name}{#if dirty}<span class="quiet"> · {t("unsaved")}</span>{/if}</h2>
            {/if}
          </div>
          {#if open.file}<p class="quiet">{t("scriptFileNote")}</p>{/if}
          {#if !open.canEdit && !open.file}<p class="quiet">{t("scriptReadOnly")}</p>{/if}

          {#if problem}
            <p class="error" role="alert">{problem.line ? `${t("atLine", { line: problem.line })} ` : ""}{problem.message}</p>
          {/if}
          {#if notice}<p class="good" role="status">{notice}</p>{/if}

          {#if conflict}
            <div class="warning" role="alert">
              <p>{t("scriptConflict")}</p>
              <div class="diff">
                {#each diffLines(conflict.source, draft) as line}
                  <div class={line.kind}>{line.kind === "added" ? "+ " : line.kind === "removed" ? "- " : "  "}{line.text}</div>
                {/each}
              </div>
              <div class="row">
                <button onclick={takeTheirs}>{t("conflictTheirs")}</button>
                <button onclick={keepMine}>{t("conflictMine")}</button>
              </div>
            </div>
          {/if}

          <Editor value={draft} readOnly={!open.canEdit} {problem} onchange={(text) => { draft = text; }} />

          <div class="row actions">
            {#if open.canEdit}
              <button class="primary" disabled={busy || !dirty && !creating} onclick={save}>{t("save")}</button>
              <button disabled={busy} onclick={check}>{t("compile")}</button>
            {/if}
            {#if !creating}
              <button disabled={busy} onclick={prepareRun}>{t("run")}</button>
            {/if}
            {#if !creating && canAuthor}
              <button disabled={busy} onclick={copy}>{t("copy")}</button>
            {/if}
            {#if !creating && open.canEdit}
              <button disabled={busy} onclick={rename}>{t("rename")}</button>
              <button disabled={busy} onclick={share}>
                {open.visibility === "shared" ? t("makePrivate") : t("makeShared")}
              </button>
              <button class="danger" disabled={busy} onclick={remove}>{t("remove")}</button>
            {/if}
          </div>
        </section>

        {#if runForm}
          <section class="card">
            <h2>{t("runTitle", { name: open.name })}</h2>
            {#if runForm.requires.length}
              <p class="quiet">{t("runRequires", { list: runForm.requires.join(", ") })}</p>
            {/if}
            {#if runForm.schema.length === 0}
              <p class="quiet">{t("runNoParams")}</p>
            {/if}
            <form onsubmit={(event) => { event.preventDefault(); void run(); }}>
              {#each runForm.schema as field}
                <label class="field">
                  {field.name}
                  {#if field.type === "group"}
                    <select bind:value={values[field.name]}>
                      <option value={undefined}>—</option>
                      {#each runForm.groups as group}<option value={`#${group.name}`}>#{group.name}</option>{/each}
                    </select>
                  {:else if field.type === "bot"}
                    <select bind:value={values[field.name]}>
                      <option value={undefined}>—</option>
                      {#each runForm.bots as bot}<option value={bot.name}>{bot.name}</option>{/each}
                    </select>
                  {:else if field.type === "choice"}
                    <select bind:value={values[field.name]}>
                      {#each field.choices ?? [] as choice}<option value={choice}>{choice}</option>{/each}
                    </select>
                  {:else if field.type === "boolean"}
                    <input type="checkbox" bind:checked={values[field.name]} />
                  {:else if field.type === "number"}
                    <input type="number" step="any" bind:value={values[field.name]} />
                  {:else}
                    <input bind:value={values[field.name]} spellcheck="false"
                           placeholder={field.type === "pos" ? "x y z" : ""} />
                  {/if}
                </label>
              {/each}
              <div class="row">
                <button class="primary" disabled={busy}>{t("runStart")}</button>
                <button type="button" onclick={() => { runForm = null; }}>{t("cancel")}</button>
              </div>
            </form>
          </section>
        {/if}

        {#if open.history.length}
          <section class="card">
            <h2>{t("history")}</h2>
            <ul>
              {#each open.history as row, index}
                <li>
                  <span>{t("revisionAt", { date: formatDate(row.savedAt) })}</span>
                  <span class="row">
                    <button onclick={() => { comparing = row; }}>{t("compare")}</button>
                    {#if open.canEdit}
                      <button disabled={busy} onclick={() => restore(index + 1)}>{t("restore")}</button>
                    {/if}
                  </span>
                </li>
              {/each}
            </ul>
            {#if comparing}
              <div class="diff">
                {#each diffLines(comparing.source, open.source) as line}
                  <div class={line.kind}>{line.kind === "added" ? "+ " : line.kind === "removed" ? "- " : "  "}{line.text}</div>
                {/each}
              </div>
            {/if}
          </section>
        {/if}
      {/if}
    </div>
  </div>
{/if}

<style>
  .layout { display: grid; gap: 1rem; grid-template-columns: minmax(14rem, 18rem) minmax(0, 1fr); align-items: start; }
  @media (max-width: 760px) { .layout { grid-template-columns: minmax(0, 1fr); } }
  .main { min-width: 0; }
  .heading { display: flex; justify-content: space-between; align-items: center; gap: .75rem; margin-bottom: .5rem; }
  .heading h2 { margin: 0; overflow-wrap: anywhere; }
  .pick button { width: 100%; display: block; text-align: left; border: 0; background: none; padding: .4rem .5rem; border-radius: .35rem; min-height: 2.75rem; }
  .pick button.current { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .pick .name { display: block; overflow-wrap: anywhere; }
  .pick li { padding: 0; }
  details { margin-top: .75rem; }
  .row { display: flex; flex-wrap: wrap; gap: .5rem; }
  .actions { margin-top: .75rem; }
  .actions .primary { width: auto; }
  .inline { display: flex; align-items: center; gap: .5rem; flex: 1; min-width: 0; margin: 0; white-space: nowrap; }
  .field { margin: 0 0 .6rem; }
  .field input:not([type="checkbox"]), .field select { display: block; width: 100%; box-sizing: border-box; }
  select { font: inherit; min-height: 2.75rem; padding: .4rem .6rem; border-radius: .45rem; border: 1px solid var(--line); background: var(--bg); color: var(--text); }
  .good { color: var(--good); }
  .diff { font: .85rem/1.45 ui-monospace, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg); border: 1px solid var(--line); border-radius: .4rem; padding: .5rem; margin: .5rem 0; max-height: 22rem; overflow: auto; }
  .diff .added { color: var(--good); }
  .diff .removed { color: var(--bad); }
  .diff .same { color: var(--quiet); }
</style>
