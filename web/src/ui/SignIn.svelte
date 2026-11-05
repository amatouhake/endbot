<script lang="ts">
  import { t } from "./i18n";

  let { display, site, error, busy, onsignin, onlink }: {
    display: { displayName?: string; bds?: string };
    site: { name: string; mode: string } | null;
    error: string;
    busy: boolean;
    onsignin: () => void;
    onlink: (code: string) => void;
  } = $props();

  let code = $state("");

  /** The hyphen is ours to place: letters and digits are all anyone has to type or paste. */
  function formatCode(event: Event): void {
    const field = event.currentTarget as HTMLInputElement;
    const deleting = (event as InputEvent).inputType?.startsWith("delete") ?? false;
    const caret = field.selectionStart ?? field.value.length;
    const clean = (text: string) => text.toUpperCase().replace(/[^0-9A-Z]/g, "");
    const before = clean(field.value.slice(0, caret)).length;
    const symbols = clean(field.value).slice(0, 6);
    // Deleting the hyphen itself must not bring it straight back.
    const hyphen = symbols.length > 3 || (symbols.length === 3 && !deleting);
    code = hyphen ? `${symbols.slice(0, 3)}-${symbols.slice(3)}` : symbols;
    field.value = code;
    const position = Math.min(before, symbols.length);
    const placed = position >= 3 && hyphen ? position + 1 : position;
    field.setSelectionRange(placed, placed);
  }
</script>

<div class="page">
  <main class="narrow">
    <h1>{display.displayName || t("title")}</h1>
    <p class="server">
      <span class="dot" class:up={display.bds === "up"}></span>
      {display.bds === "up" ? t("serverUp") : t("serverDown")}
    </p>
    {#if error}<p class="error" role="alert">{error}</p>{/if}

    <section class="card">
      <div class="row">
        <button class="primary wide-button" disabled={busy} onclick={onsignin}>{t("signIn")}</button>
        {#if site}
          <a class="button wide-button" href="/auth/site/start">{t("signInSite", { name: site.name })}</a>
        {/if}
      </div>
    </section>

    <section class="card">
      <h2>{t("linkTitle")}</h2>
      <p class="quiet">{t("linkHelp")}</p>
      <form onsubmit={(event) => { event.preventDefault(); onlink(code); }}>
        <label for="code">{t("linkCode")}</label>
        <div class="row">
          <input id="code" class="code-field" value={code} oninput={formatCode} autocomplete="one-time-code"
                 autocapitalize="characters" autocorrect="off" spellcheck="false" placeholder="K7Q-4M2" />
          <button class="primary" disabled={busy || code.replace("-", "").length < 6}>{t("linkButton")}</button>
        </div>
      </form>
    </section>
  </main>
</div>
