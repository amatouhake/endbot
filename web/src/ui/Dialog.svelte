<script lang="ts">
  import { dialogs } from "./dialogs.svelte";
  import { t } from "./i18n";

  let element = $state<HTMLDialogElement>();
  let text = $state("");
  let refusal = $state("");
  let busy = $state(false);

  $effect(() => {
    const request = dialogs.current;
    if (!element) return;
    if (!request) {
      if (element.open) element.close();
      return;
    }
    text = request.input?.value ?? "";
    refusal = "";
    busy = false;
    if (!element.open) element.showModal();
  });

  function answer(value: string | boolean | null): void {
    const request = dialogs.current;
    dialogs.current = null;
    element?.close();
    request?.resolve(value);
  }

  async function submit(event: Event): Promise<void> {
    event.preventDefault();
    const request = dialogs.current;
    if (request?.input) {
      const value = text.trim();
      if (!value || busy) return;
      if (request.attempt) {
        busy = true;
        refusal = "";
        const problem = await request.attempt(value);
        // Cancelled meanwhile, or the account is gone: this answer is nobody's.
        if (dialogs.current !== request) return;
        busy = false;
        if (problem) {
          refusal = problem;
          return;
        }
      }
      answer(value);
    } else {
      answer(true);
    }
  }
</script>

<dialog bind:this={element} oncancel={(event) => { event.preventDefault(); if (!busy) answer(null); }}>
  {#if dialogs.current}
    <form onsubmit={submit}>
      <h2>{dialogs.current.title}</h2>
      {#if dialogs.current.message}<p>{dialogs.current.message}</p>{/if}
      {#if dialogs.current.input}
        <label class="field">{dialogs.current.input.label}
          <!-- svelte-ignore a11y_autofocus -->
          <input bind:value={text} spellcheck="false" autofocus maxlength={dialogs.current.input.maxlength} />
        </label>
      {/if}
      {#if refusal}<p class="error" role="alert">{refusal}</p>{/if}
      <div class="dialog-actions">
        <button type="button" disabled={busy} onclick={() => answer(null)}>{t("cancel")}</button>
        <button class={dialogs.current.danger ? "primary danger-fill" : "primary"} disabled={busy}>{dialogs.current.confirm}</button>
      </div>
    </form>
  {/if}
</dialog>
