<script lang="ts">
  import { untrack } from "svelte";
  import type { Connection } from "./api";
  import { confirmDialog, promptDialog } from "./dialogs.svelte";
  import { diffLines } from "./diff";
  import Editor from "./Editor.svelte";
  import { formatDate, t } from "./i18n";
  import { href, navigate } from "./route.svelte";

  let { connection, log, runs, selected, active, onchanged }: {
    connection: Connection;
    log: any[];
    runs: { id: string; name: string }[];
    selected: string | null;
    active: boolean;
    onchanged: () => void;
  } = $props();

  const NAME = /^[A-Za-z0-9_-]{1,64}$/;

  let scripts = $state<any[]>([]);
  let canAuthor = $state(false);
  let unavailable = $state("");
  let templates = $state<Record<string, string>>({});
  let search = $state("");
  // The script open in the editor; `revision` null while it has never been saved.
  let open = $state<any>(null);
  let draft = $state("");
  let name = $state("");
  let creating = $state(false);
  let busy = $state(false);
  let problem = $state<{ line?: number; message: string } | null>(null);
  let notice = $state("");
  let conflict = $state<any>(null);
  let revisions = $state(false);
  let comparing = $state<any>(null);
  let runForm = $state<any>(null);
  let values = $state<Record<string, any>>({});
  let moreMenu = $state<HTMLDetailsElement>();
  // Only the answer to the latest choice may fill the editor.
  let latest = 0;

  const dirty = $derived(open !== null && draft !== open.source);
  const shown = $derived(scripts
    .filter((script) => script.name.toLowerCase().includes(search.trim().toLowerCase()))
    .toSorted((a, b) => a.name.localeCompare(b.name)));
  const output = $derived(open && !creating
    ? log.filter((entry) => entry.kind !== "command" && entry.script === open.name) : []);

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

  // On arriving, and again whenever the connection came back.
  $effect(() => {
    if (active && connection) untrack(() => void refresh());
  });

  // Reloading, or closing the tab, would lose what is typed; the browser asks, as this is its own moment.
  $effect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  });

  function clear(): void {
    problem = null;
    notice = "";
    conflict = null;
    comparing = null;
    runForm = null;
    revisions = false;
  }

  function leaveOk(): Promise<boolean> {
    return dirty ? confirmDialog(t("unsavedLeave"), t("leaveAnyway"), { danger: true }) : Promise.resolve(true);
  }

  async function load(script: string): Promise<void> {
    const ticket = ++latest;
    const answer = await connection.request("script.get", { name: script });
    if (ticket !== latest) return;
    clear();
    creating = false;
    if (answer.code !== "ok") {
      open = null;
      draft = "";
      problem = { message: answer.messages?.[0] ?? t("error_generic") };
      return;
    }
    open = answer.data;
    draft = open.source;
    name = open.name;
  }

  /** The address says which script: this also answers Back, Forward and a reload. */
  async function follow(wanted: string | null): Promise<void> {
    const showing = creating || !open ? null : open.name;
    if (wanted === showing) return;
    if (wanted === null && creating) return;
    if (!(await leaveOk())) {
      navigate("scripts", creating ? null : open.name, true);
      return;
    }
    if (wanted === null) {
      latest += 1;
      clear();
      creating = false;
      open = null;
      draft = "";
    } else {
      await load(wanted);
    }
  }

  $effect(() => {
    const wanted = selected;
    if (active) untrack(() => void follow(wanted));
  });

  async function startNew(source = "params {}\n\n"): Promise<void> {
    if (!(await leaveOk())) return;
    latest += 1;
    clear();
    creating = true;
    name = "";
    open = { name: "", source, revision: null, canEdit: true, history: [], visibility: "private", file: false };
    draft = source;
    navigate("scripts");
  }

  async function goBack(): Promise<void> {
    if (!(await leaveOk())) return;
    latest += 1;
    clear();
    creating = false;
    open = null;
    draft = "";
    navigate("scripts");
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
      navigate("scripts", open.name, true);
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
    if (moreMenu) moreMenu.open = false;
    busy = true;
    clear();
    const answer = await connection.request(type, { name: open.name, revision: open.revision, ...fields });
    busy = false;
    if (answer.code === "ok") await after(answer);
    else fail(answer);
  }

  /** A script that now goes by another name, or is another revision: what was typed has gone with the action. */
  async function reopen(script: string): Promise<void> {
    await load(script);
    navigate("scripts", script, true);
    await refresh();
  }

  async function rename(): Promise<void> {
    if (moreMenu) moreMenu.open = false;
    const next = await promptDialog(t("rename"), t("scriptNewName"), open.name, t("ok"));
    if (next && next !== open.name) await action("script.rename", { new: next }, () => reopen(next));
  }

  async function copy(): Promise<void> {
    if (moreMenu) moreMenu.open = false;
    const next = await promptDialog(t("copy"), t("scriptCopyName"), `${open.name}_copy`, t("ok"));
    if (next) await action("script.copy", { new: next }, () => reopen(next));
  }

  async function remove(): Promise<void> {
    if (moreMenu) moreMenu.open = false;
    if (!(await confirmDialog(t("scriptDeleteConfirm", { name: open.name }), t("delete"), { danger: true }))) return;
    await action("script.delete", {}, async () => {
      open = null;
      draft = "";
      navigate("scripts", null, true);
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
    values = Object.fromEntries((answer.data.schema as any[])
      .filter((field) => field.default !== undefined)
      .map((field) => [field.name, field.default]));
    runForm = answer.data;
  }

  function typed(field: any, value: any): any {
    if (field.type === "number" && value !== "" && value !== undefined) return Number(value);
    return value;
  }

  /** What the form chose, so that the run can refuse a target that changed while it was open. */
  function pins(given: Record<string, any>): Record<string, unknown> {
    const pinned: Record<string, unknown> = {};
    for (const field of runForm.schema as any[]) {
      const value = given[field.name];
      if (field.type === "group") {
        const group = runForm.groups.find((item: any) => `#${item.name}` === value);
        if (group) pinned[field.name] = { value, identity: group.identity, members: group.members };
      } else if (field.type === "bot") {
        const bot = runForm.bots.find((item: any) => item.name === value);
        if (bot) pinned[field.name] = { value, identity: bot.identity };
      }
    }
    return pinned;
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
    runForm = null;
    if (answer.code === "ok") {
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

  /** A dialog shown as soon as it exists, so each one only has to be written where it is decided. */
  function modal(node: HTMLDialogElement): void {
    node.showModal();
  }
</script>

{#snippet difference(from: string, to: string)}
  <div class="diff">
    {#each diffLines(from, to) as line}
      <div class={line.kind}>{line.kind === "added" ? "+ " : line.kind === "removed" ? "- " : "  "}{line.text}</div>
    {/each}
  </div>
{/snippet}

<div class="split" class:showing={open !== null}>
  <div class="list-pane">
    <div class="list-tools">
      <div class="row">
        <input class="grow" type="search" bind:value={search} placeholder={t("search")} aria-label={t("search")} />
        {#if canAuthor}<button onclick={() => startNew()}>{t("scriptNew")}</button>{/if}
      </div>
    </div>
    <div class="list-scroll">
      {#if unavailable}
        <p class="empty">{unavailable}</p>
      {:else if scripts.length === 0}
        <p class="empty">{t("noScripts")}</p>
      {:else if shown.length === 0}
        <p class="empty">{t("noMatches")}</p>
      {:else}
        {#each shown as script (script.name)}
          <a class="item" class:current={open?.name === script.name} href={href("scripts", script.name)}
             aria-current={open?.name === script.name ? "true" : undefined}
             onclick={(event) => { event.preventDefault(); navigate("scripts", script.name); }}>
            <span class="name">{script.name}
              <small>
                {script.file ? t("scriptFile") : script.own ? t("scriptOwn") : script.ownerName ?? ""}
                · {script.visibility === "shared" ? t("scriptShared") : t("scriptPrivate")}
              </small>
            </span>
            {#if runs.some((item) => item.name === script.name)}<span class="tag online">{t("stateRunning")}</span>{/if}
          </a>
        {/each}
      {/if}
    </div>
    {#if runs.length || (canAuthor && Object.keys(templates).length)}
      <div class="list-foot">
        {#if runs.length}
          <h3>{t("runs")}</h3>
          <ul class="members">
            {#each runs as item (item.id)}
              <li>
                <span>{item.name} <code>{item.id}</code></span>
                <button class="danger" onclick={() => stop(item.id)}>{t("stop")}</button>
              </li>
            {/each}
          </ul>
        {/if}
        {#if canAuthor && Object.keys(templates).length}
          <details>
            <summary class="quiet">{t("scriptTemplates")}</summary>
            {#each Object.entries(templates) as [title, source]}
              <button class="item" onclick={() => startNew(source)}>{title}</button>
            {/each}
          </details>
        {/if}
      </div>
    {/if}
  </div>

  {#if !open}
    <div class="detail">
      {#if problem}<p class="error" role="alert">{problem.message}</p>{/if}
      <p class="empty">{t("scriptPick")}</p>
    </div>
  {:else}
    <div class="script">
      <div class="script-bar">
        <div class="inline">
          <button class="back" onclick={goBack}>← {t("back")}</button>
          {#if creating}
            <label class="inline">{t("scriptName")}
              <input bind:value={name} spellcheck="false" maxlength="64" placeholder="my_script" />
            </label>
          {:else}
            <h2>{open.name}</h2>
          {/if}
          {#if dirty}<span class="tag">{t("unsaved")}</span>{/if}
        </div>
        <div class="row">
          {#if open.canEdit}
            <button class="primary" disabled={busy || (!dirty && !creating)} onclick={save}>{t("save")}</button>
            <button disabled={busy} onclick={check}>{t("compile")}</button>
          {/if}
          {#if !creating}
            <button disabled={busy} onclick={prepareRun}>{t("run")}</button>
            <details class="more" bind:this={moreMenu}>
              <summary class="button">{t("more")} ▾</summary>
              <div class="menu">
                {#if canAuthor}<button disabled={busy} onclick={copy}>{t("copy")}</button>{/if}
                {#if open.canEdit}
                  <button disabled={busy} onclick={rename}>{t("rename")}</button>
                  <button disabled={busy} onclick={share}>{open.visibility === "shared" ? t("makePrivate") : t("makeShared")}</button>
                {/if}
                <button disabled={!open.history.length}
                        onclick={() => { revisions = true; if (moreMenu) moreMenu.open = false; }}>{t("scriptRevisions")}</button>
                {#if open.canEdit}<button class="danger" disabled={busy} onclick={remove}>{t("remove")}</button>{/if}
              </div>
            </details>
          {/if}
        </div>
      </div>

      <Editor value={draft} readOnly={!open.canEdit} {problem} onchange={(text) => { draft = text; }} />

      <div class="output">
        {#if problem}
          <div class="line error-line" role="alert">{problem.line ? `${t("atLine", { line: problem.line })} ` : ""}{problem.message}</div>
        {/if}
        {#if notice}<div class="line good-line" role="status">{notice}</div>{/if}
        {#if open.file}<div class="line quiet">{t("scriptFileNote")}</div>{/if}
        {#if !open.canEdit && !open.file}<div class="line quiet">{t("scriptReadOnly")}</div>{/if}
        {#each output as entry}
          {#each entry.lines as text}
            <div class="line" class:error-line={entry.level !== "info"}><time>{formatDate(entry.time)}</time>{text}</div>
          {/each}
        {/each}
        {#if !problem && !notice && !output.length && open.canEdit}<div class="line quiet">{t("outputEmpty")}</div>{/if}
      </div>
    </div>
  {/if}
</div>

{#if conflict}
  <dialog class="wide" use:modal oncancel={(event) => { event.preventDefault(); conflict = null; }}>
    <h2>{t("scriptConflict")}</h2>
    {@render difference(conflict.source, draft)}
    <div class="dialog-actions">
      <button onclick={() => { conflict = null; }}>{t("cancel")}</button>
      <button onclick={takeTheirs}>{t("conflictTheirs")}</button>
      <button class="primary" onclick={keepMine}>{t("conflictMine")}</button>
    </div>
  </dialog>
{/if}

{#if revisions}
  <dialog class="wide" use:modal oncancel={(event) => { event.preventDefault(); revisions = false; comparing = null; }}>
    <h2>{t("history")}</h2>
    <ul class="members">
      {#each open.history as row, index}
        <li>
          <span>{t("revisionAt", { date: formatDate(row.savedAt) })}</span>
          <span class="row">
            <button onclick={() => { comparing = row; }}>{t("compare")}</button>
            {#if open.canEdit}<button disabled={busy} onclick={() => restore(index + 1)}>{t("restore")}</button>{/if}
          </span>
        </li>
      {/each}
    </ul>
    {#if comparing}{@render difference(comparing.source, open.source)}{/if}
    <div class="dialog-actions">
      <button onclick={() => { revisions = false; comparing = null; }}>{t("close")}</button>
    </div>
  </dialog>
{/if}

{#if runForm}
  <dialog use:modal oncancel={(event) => { event.preventDefault(); runForm = null; }}>
    <h2>{t("runTitle", { name: open.name })}</h2>
    {#if runForm.requires.length}<p class="quiet">{t("runRequires", { list: runForm.requires.join(", ") })}</p>{/if}
    {#if runForm.schema.length === 0}<p class="quiet">{t("runNoParams")}</p>{/if}
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
            <input bind:value={values[field.name]} spellcheck="false" placeholder={field.type === "pos" ? "x y z" : ""} />
          {/if}
        </label>
      {/each}
      <div class="dialog-actions">
        <button type="button" onclick={() => { runForm = null; }}>{t("cancel")}</button>
        <button class="primary" disabled={busy}>{t("runStart")}</button>
      </div>
    </form>
  </dialog>
{/if}
