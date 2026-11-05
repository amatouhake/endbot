<script lang="ts">
  import type { Connection } from "./api";
  import { confirmDialog, createDialog } from "./dialogs.svelte";
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

  const bare = (text: string): string => text.replace(/^#/, "");

  async function create(): Promise<void> {
    const name = await createDialog(t("groupNewTitle"), t("groupNew"), t("groupCreate"), 17, async (text) => {
      if (!NAME.test(bare(text))) return t("groupNameRule");
      const answer = await connection.request("group.create", { name: bare(text) });
      return answer.code === "ok" ? null : answer.messages?.[0] ?? t("opFailed", { code: answer.code });
    });
    if (!name) return;
    error = "";
    onchanged();
    navigate("groups", bare(name));
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
      <div class="row">
        <input class="grow" type="search" bind:value={search} placeholder={t("search")} aria-label={t("search")} />
        <button onclick={create}>{t("new")}</button>
      </div>
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
        <p class="gap-top">
          <a href={href("bots", `#${selected}`)}
             onclick={(event) => { event.preventDefault(); navigate("bots", `#${selected}`); }}>{t("groupOperate")}</a>
        </p>
      </section>
      <section class="card">
        <h2>{t("groupMembers")}</h2>
        {#if status?.bots === null}
          <p class="quiet">{t("botsUnknown")}</p>
        {:else if !current || current.members.length === 0}
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
    {/if}
  </div>
</div>
