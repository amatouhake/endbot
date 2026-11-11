<script lang="ts">
  import { formatDate, t } from "./i18n";

  let { log }: { log: any[] } = $props();

  let show = $state<"all" | "commands" | "scripts">("all");
  const shown = $derived(log.filter((entry) => show === "all"
    || (show === "commands" ? entry.kind === "command" : entry.kind !== "command")));
  // The log arrives newest first; it reads like the game's chat, oldest at the top and the newest at the bottom
  // (asked for in the 0.7.0 release pass).
  const ordered = $derived([...shown].reverse());

  let list = $state<HTMLDivElement>();
  // Follow new entries while the reader is at the bottom; leave them where they are while they read further up.
  let following = true;

  function scrolled(): void {
    if (list) following = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
  }

  $effect(() => {
    void ordered.length;
    if (list && following) list.scrollTop = list.scrollHeight;
  });
</script>

<div class="history">
  <div class="history-tools">
    <small class="quiet">{t("historyNote")}</small>
    <label class="row">{t("historyShow")}
      <select bind:value={show} onchange={() => { following = true; }}>
        <option value="all">{t("historyAll")}</option>
        <option value="commands">{t("historyCommands")}</option>
        <option value="scripts">{t("historyScripts")}</option>
      </select>
    </label>
  </div>
  <div class="history-list" bind:this={list} onscroll={scrolled}>
    {#if ordered.length === 0}
      <p class="empty">{t("historyEmpty")}</p>
    {:else}
      <ol>
        {#each ordered as entry}
          <li class:warn={entry.level !== "info"}>
            <time>{formatDate(entry.time)}{entry.script ? ` · ${entry.script}` : ""}</time>
            {#if entry.command}<span class="command">{entry.command}</span>{/if}
            {#each entry.lines as line}<span class="line">{line}</span>{/each}
          </li>
        {/each}
      </ol>
    {/if}
  </div>
</div>
