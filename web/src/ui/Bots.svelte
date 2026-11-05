<script lang="ts">
  // What `/bot` can address, as in the game: Bots, then Groups, each with the same operations.
  import type { Connection } from "./api";
  import Controls from "./Controls.svelte";
  import { createDialog } from "./dialogs.svelte";
  import { opLabel, stateLabel, t } from "./i18n";
  import { href, navigate } from "./route.svelte";

  const READ_EVERY_MS = 3000;

  let { connection, status, selected, active, onchanged }: {
    connection: Connection;
    status: any;
    selected: string | null;
    active: boolean;
    onchanged: () => void;
  } = $props();

  let search = $state("");
  let filter = $state<"all" | "online" | "offline" | "groups">("all");
  let sort = $state<"state" | "name">("state");
  let detail = $state<any>(null);
  let detailProblem = $state("");
  let controls = $state<Controls>();

  const bots = $derived<any[]>(status?.bots ?? []);
  const online = $derived(bots.filter((bot) => bot.state === "online").length);
  const groups = $derived<any[]>(status?.groups ?? []);
  const typed = $derived(search.trim().toLowerCase().replace(/^#/, ""));
  const shownGroups = $derived(filter === "all" || filter === "groups"
    ? groups.filter((group) => group.name.toLowerCase().includes(typed)).toSorted((a, b) => a.name.localeCompare(b.name))
    : []);
  const shown = $derived(bots
    .filter((bot) => bot.name.toLowerCase().includes(typed))
    .filter((bot) => filter === "all" || (filter !== "groups" && (filter === "online") === (bot.state === "online")))
    .toSorted((a, b) => (sort === "state" ? Number(b.state === "online") - Number(a.state === "online") : 0)
      || a.name.localeCompare(b.name)));
  const current = $derived(bots.find((bot) => bot.name === selected));
  const isOnline = $derived(current?.state === "online");
  const isGroup = $derived(selected?.startsWith("#") ?? false);
  const currentGroup = $derived(isGroup ? groups.find((group) => `#${group.name}` === selected) : undefined);

  function stateOf(member: string): string | undefined {
    return bots.find((bot) => bot.name === member)?.state;
  }

  async function read(name: string): Promise<void> {
    const answer = await connection.request("query", { what: "bot", name });
    if (selected !== name) return;
    detail = answer.code === "ok" ? answer.data ?? null : null;
    detailProblem = answer.code === "ok" ? "" : answer.messages?.[0] ?? t("opFailed", { code: answer.code });
  }

  // What it is doing changes on its own, so it is read again for as long as anyone is looking at it.
  $effect(() => {
    const name = selected;
    detail = null;
    detailProblem = "";
    if (!name || !active || !isOnline) return;
    void read(name);
    const timer = setInterval(() => {
      if (!document.hidden) void read(name);
    }, READ_EVERY_MS);
    return () => clearInterval(timer);
  });

  async function spawnBot(): Promise<void> {
    const name = await createDialog(t("newBotTitle"), t("newBotName"), t("spawn"), 16, async (text) => {
      const answer = await connection.request("op", { operation: { op: "spawn", target: text, params: {} } });
      return answer.code === "ok" ? null : answer.messages?.[0] ?? t("opFailed", { code: answer.code });
    });
    if (!name) return;
    onchanged();
    navigate("bots", name);
  }

  function after(op: string, fields: Record<string, unknown>): void {
    onchanged();
    if (op === "rename" && typeof fields.name === "string") navigate("bots", fields.name, true);
    if (op === "forget") navigate("bots", null, true);
    if (selected && isOnline) void read(selected);
  }

  function number(value: unknown): string {
    return typeof value === "number" ? String(Math.round(value * 10) / 10) : "—";
  }

  function held(value: any): string {
    if (!value) return t("factIdle");
    const name = String(value.name ?? "—").replace(/^minecraft:/, "");
    return value.count > 1 ? `${name} ×${value.count}` : name;
  }

  function doing(value: any): string {
    const jobs = Object.entries(value?.running ?? {}).filter(([, job]) => job).map(([channel]) => opLabel(channel));
    return jobs.length ? jobs.join(", ") : t("factIdle");
  }
</script>

<div class="split" class:showing={selected !== null}>
  <div class="list-pane">
    <div class="list-tools">
      <div class="row">
        <input class="grow" type="search" bind:value={search} placeholder={t("search")} aria-label={t("search")} />
        <button onclick={spawnBot}>{t("new")}</button>
      </div>
      <div class="row">
        <select bind:value={filter} aria-label={t("filterLabel")}>
          <option value="all">{t("filterAll", { count: bots.length + groups.length })}</option>
          <option value="online">{t("filterOnline", { count: online })}</option>
          <option value="offline">{t("filterOffline", { count: bots.length - online })}</option>
          <option value="groups">{t("filterGroups", { count: groups.length })}</option>
        </select>
        <select bind:value={sort} aria-label={t("sortLabel")}>
          <option value="state">{t("sortState")}</option>
          <option value="name">{t("sortName")}</option>
        </select>
      </div>
    </div>
    <div class="list-scroll">
      {#if status?.bots === null}
        <p class="empty">{t("botsUnknown")}</p>
      {:else if bots.length === 0 && groups.length === 0}
        <p class="empty">{t("noBots")}</p>
      {:else if shown.length === 0 && shownGroups.length === 0}
        <p class="empty">{t("noMatches")}</p>
      {:else}
        {#each shown as bot (bot.name)}
          <a class="item" class:current={selected === bot.name} href={href("bots", bot.name)}
             aria-current={selected === bot.name ? "true" : undefined}
             onclick={(event) => { event.preventDefault(); navigate("bots", bot.name); }}>
            <span class="name">{bot.name}</span>
            <span class="tag" class:online={bot.state === "online"}>{stateLabel(bot.state)}</span>
          </a>
        {/each}
        {#if shownGroups.length}
          <h3 class="list-heading">{t("tabGroups")}</h3>
          {#each shownGroups as group (group.name)}
            <a class="item" class:current={selected === `#${group.name}`} href={href("bots", `#${group.name}`)}
               aria-current={selected === `#${group.name}` ? "true" : undefined}
               onclick={(event) => { event.preventDefault(); navigate("bots", `#${group.name}`); }}>
              <span class="name">#{group.name}</span>
              <small>{t("members", { count: group.members.length })}</small>
            </a>
          {/each}
        {/if}
      {/if}
    </div>
  </div>

  <div class="detail">
    <button class="back" onclick={() => navigate("bots")}>← {t("back")}</button>
    {#if !selected}
      <p class="empty">{t("botPick")}</p>
    {:else if isGroup && !currentGroup}
      <p class="empty">{t("groupMissing")}</p>
    {:else}
      {#if isGroup}
      <section class="card">
        <div class="spread">
          <h2>{selected}</h2>
          <a href={href("groups", currentGroup.name)}
             onclick={(event) => { event.preventDefault(); navigate("groups", currentGroup.name); }}>{t("groupManage")}</a>
        </div>
        <div class="row gap-top">
          <button class="primary" onclick={() => controls?.send("resume")}>{t("startBot")}</button>
          <button onclick={() => controls?.send("despawn")}>{t("stopBot")}</button>
          <button class="danger" onclick={() => controls?.send("stop")}>{t("stopAll")}</button>
        </div>
      </section>
      <section class="card">
        <h2>{t("groupMembers")}</h2>
        {#if currentGroup.members.length === 0}
          <p class="quiet">{t("noMembers")}</p>
        {:else}
          <ul class="members">
            {#each currentGroup.members as member, index (`${member}:${index}`)}
              <li>
                {#if member === null}
                  <span class="quiet">{t("memberGone")}</span>
                {:else}
                  <a href={href("bots", member)} onclick={(event) => { event.preventDefault(); navigate("bots", member); }}>{member}</a>
                  {#if stateOf(member)}
                    <span class="tag" class:online={stateOf(member) === "online"}>{stateLabel(stateOf(member)!)}</span>
                  {/if}
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </section>
      {:else}
      <section class="card">
        <div class="spread">
          <h2>{selected}</h2>
          {#if current}<span class="tag" class:online={isOnline}>{stateLabel(current.state)}</span>{/if}
        </div>
        <div class="row gap-top">
          {#if isOnline}
            <button onclick={() => controls?.send("despawn")}>{t("stopBot")}</button>
            <button class="danger" onclick={() => controls?.send("stop")}>{t("stopAll")}</button>
          {:else}
            <button class="primary" onclick={() => controls?.send("resume")}>{t("startBot")}</button>
          {/if}
        </div>
      </section>
      <section class="card">
        {#if !isOnline}
          <p class="quiet">{t("botOffline")}</p>
        {:else if detailProblem}
          <p class="quiet">{detailProblem}</p>
        {:else if !detail}
          <p class="quiet">{t("loading")}</p>
        {:else}
          <dl class="facts">
            <div><dt>{t("factPosition")}</dt><dd>{#if detail.position}{number(detail.position.x)}, {number(detail.position.y)}, {number(detail.position.z)}{:else}—{/if}</dd></div>
            <div><dt>{t("factDimension")}</dt><dd>{detail.dimension ?? "—"}</dd></div>
            <div><dt>{t("factHealth")}</dt><dd>{number(detail.health)}{detail.maxHealth ? ` / ${number(detail.maxHealth)}` : ""}</dd></div>
            <div><dt>{t("factHunger")}</dt><dd>{number(detail.hunger)}</dd></div>
            <div><dt>{t("factHeld")}</dt><dd>{held(detail.held)}</dd></div>
            <div><dt>{t("factSlot")}</dt><dd>{detail.selectedSlot ?? "—"}</dd></div>
            <div><dt>{t("factLook")}</dt><dd>{#if detail.look}{number(detail.look.yaw)} / {number(detail.look.pitch)}{:else}—{/if}</dd></div>
            <div><dt>{t("factDoing")}</dt><dd>{doing(detail)}</dd></div>
            {#if detail.mining?.active}
              <div><dt>{t("factMining")}</dt><dd>{t("factMiningState", { state: detail.mining.state ?? "", count: detail.mining.blocksBroken ?? 0 })}</dd></div>
            {/if}
          </dl>
        {/if}
      </section>
      {/if}
      {#key selected}
        <Controls bind:this={controls} {connection} target={selected} group={isGroup} online={isGroup || isOnline}
                  gamertag={status?.gamertag ?? ""} onchanged={after} />
      {/key}
    {/if}
  </div>
</div>
