<script lang="ts">
  import type { Connection } from "./api";
  import Controls from "./Controls.svelte";
  import { confirmDialog } from "./dialogs.svelte";
  import { t } from "./i18n";
  import { href, navigate } from "./route.svelte";

  const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,15}$/;

  let { connection, status, selected, onchanged }: {
    connection: Connection;
    status: any;
    selected: string | null;
    onchanged: () => void;
  } = $props();

  let search = $state("");
  let newGroup = $state("");
  let addition = $state("");
  let busy = $state(false);
  let error = $state("");

  const groups = $derived<any[]>(status?.groups ?? []);
  const shown = $derived(groups
    .filter((group) => group.name.toLowerCase().includes(search.trim().toLowerCase()))
    .toSorted((a, b) => a.name.localeCompare(b.name)));
  const current = $derived(groups.find((group) => group.name === selected));
  const outside = $derived(((status?.bots ?? []) as any[])
    .map((bot) => bot.name as string)
    .filter((name) => !current?.members.includes(name)));

  async function ask(type: string, fields: Record<string, unknown>): Promise<boolean> {
    busy = true;
    error = "";
    const answer = await connection.request(type, fields);
    busy = false;
    if (answer.code !== "ok") {
      error = answer.messages?.[0] ?? t("opFailed", { code: answer.code });
      return false;
    }
    onchanged();
    return true;
  }

  async function create(): Promise<void> {
    const name = newGroup.trim().replace(/^#/, "");
    if (!NAME.test(name)) {
      error = t("groupNameRule");
      return;
    }
    if (await ask("group.create", { name })) {
      newGroup = "";
      navigate("groups", name);
    }
  }

  async function remove(): Promise<void> {
    const name = selected!;
    if (!(await confirmDialog(t("groupDeleteConfirm", { name }), t("delete"), { danger: true }))) return;
    if (await ask("group.delete", { name })) navigate("groups", null, true);
  }

  async function add(): Promise<void> {
    if (await ask("group.add", { name: selected, bot: addition })) addition = "";
  }
</script>

<div class="split" class:showing={selected !== null}>
  <div class="list-pane">
    <div class="list-tools">
      <input type="search" bind:value={search} placeholder={t("search")} aria-label={t("search")} />
    </div>
    <div class="list-scroll">
      {#if groups.length === 0}
        <p class="empty">{t("noGroups")}</p>
      {:else if shown.length === 0}
        <p class="empty">{t("noMatches")}</p>
      {:else}
        {#each shown as group (group.name)}
          <a class="item" class:current={selected === group.name} href={href("groups", group.name)}
             aria-current={selected === group.name ? "true" : undefined}
             onclick={(event) => { event.preventDefault(); error = ""; navigate("groups", group.name); }}>
            <span class="name">#{group.name}</span>
            <small>{t("members", { count: group.members.length })}</small>
          </a>
        {/each}
      {/if}
    </div>
    <div class="list-foot">
      {#if error && !selected}<p class="error" role="alert">{error}</p>{/if}
      <form onsubmit={(event) => { event.preventDefault(); void create(); }}>
        <input bind:value={newGroup} aria-label={t("groupNew")} placeholder={t("groupNew")} spellcheck="false" maxlength="17" />
        <button disabled={busy || !newGroup.trim()}>{t("groupCreate")}</button>
      </form>
    </div>
  </div>

  <div class="detail">
    <button class="back" onclick={() => navigate("groups")}>← {t("back")}</button>
    {#if !selected}
      <p class="empty">{t("groupPick")}</p>
    {:else}
      {#if error}<p class="error" role="alert">{error}</p>{/if}
      <section class="card">
        <div class="spread">
          <h2>#{selected}</h2>
          <button class="danger" disabled={busy} onclick={remove}>{t("groupDelete")}</button>
        </div>
      </section>
      <section class="card">
        <h2>{t("groupMembers")}</h2>
        {#if !current || current.members.length === 0}
          <p class="quiet">{t("noMembers")}</p>
        {:else}
          <ul class="members">
            {#each current.members as member, index (`${member}:${index}`)}
              <li>
                {#if member === null}
                  <span class="quiet">{t("memberGone")}</span>
                {:else}
                  <a href={href("bots", member)} onclick={(event) => { event.preventDefault(); navigate("bots", member); }}>{member}</a>
                  <button disabled={busy} onclick={() => ask("group.remove", { name: selected, bot: member })}>{t("groupRemove")}</button>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
        <form class="row gap-top" onsubmit={(event) => { event.preventDefault(); void add(); }}>
          <select bind:value={addition} aria-label={t("groupAddLabel")}>
            <option value="">{t("groupAddLabel")}</option>
            {#each outside as name}<option value={name}>{name}</option>{/each}
          </select>
          <button disabled={busy || !addition}>{t("groupAdd")}</button>
        </form>
      </section>
      {#key selected}
        <h3>{t("groupOperate")}</h3>
        <Controls {connection} target={`#${selected}`} onchanged={() => onchanged()} />
      {/key}
    {/if}
  </div>
</div>
