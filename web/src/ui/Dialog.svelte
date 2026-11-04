<script lang="ts">
  import { dialogs } from "./dialogs.svelte";
  import { t } from "./i18n";

  let element = $state<HTMLDialogElement>();
  let text = $state("");

  $effect(() => {
    const request = dialogs.current;
    if (!element || !request) return;
    text = request.input?.value ?? "";
    if (!element.open) element.showModal();
  });

  function answer(value: string | boolean | null): void {
    const request = dialogs.current;
    dialogs.current = null;
    element?.close();
    request?.resolve(value);
  }

  function submit(event: Event): void {
    event.preventDefault();
    const request = dialogs.current;
    if (request?.input) {
      if (text.trim()) answer(text.trim());
    } else {
      answer(true);
    }
  }
</script>

<dialog bind:this={element} oncancel={(event) => { event.preventDefault(); answer(null); }}>
  {#if dialogs.current}
    <form onsubmit={submit}>
      <h2>{dialogs.current.title}</h2>
      {#if dialogs.current.message}<p>{dialogs.current.message}</p>{/if}
      {#if dialogs.current.input}
        <label class="field">{dialogs.current.input.label}
          <!-- svelte-ignore a11y_autofocus -->
          <input bind:value={text} spellcheck="false" autofocus />
        </label>
      {/if}
      <div class="dialog-actions">
        <button type="button" onclick={() => answer(null)}>{t("cancel")}</button>
        <button class={dialogs.current.danger ? "primary danger-fill" : "primary"}>{dialogs.current.confirm}</button>
      </div>
    </form>
  {/if}
</dialog>
