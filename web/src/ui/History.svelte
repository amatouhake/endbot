<script lang="ts">
  import { formatDate, t } from "./i18n";

  let { log }: { log: any[] } = $props();

  let show = $state<"all" | "commands" | "scripts">("all");
  const shown = $derived(log.filter((entry) => show === "all"
    || (show === "commands" ? entry.kind === "command" : entry.kind !== "command")));
</script>

<div class="history">
  <div class="history-tools">
    <small class="quiet">{t("historyNote")}</small>
    <label class="row">{t("historyShow")}
      <select bind:value={show}>
        <option value="all">{t("historyAll")}</option>
        <option value="commands">{t("historyCommands")}</option>
        <option value="scripts">{t("historyScripts")}</option>
      </select>
    </label>
  </div>
  <div class="history-list">
    {#if shown.length === 0}
      <p class="empty">{t("historyEmpty")}</p>
    {:else}
      <ol>
        {#each shown as entry}
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
