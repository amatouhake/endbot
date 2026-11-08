<script lang="ts">
  // One generated input: the Bot operation forms, the run form and the trigger editor all use this,
  // so the registry's types look and behave the same everywhere (docs/WEB.md P11).
  import { choicesOf, inputType, type Field } from "./fields";
  import { fieldLabel, t } from "./i18n";

  let { field, value, onchange, required = undefined, disabled = false, showOptional = true }: {
    field: Field;
    value: any;
    onchange: (value: any) => void;
    required?: boolean;
    disabled?: boolean;
    showOptional?: boolean;
  } = $props();

  const options = $derived(field.options
    ?? (choicesOf(field.name, field.spec) ?? []).map((choice) => ({ value: choice, label: choice })));
  const needed = $derived(required ?? field.required);
  const shown = $derived(value === undefined || value === null ? "" : String(value));
  const label = $derived(field.rawLabel ? field.label ?? field.name : fieldLabel(field.label ?? field.name));

  function hintText(hint: NonNullable<Field["hint"]>): string {
    if (hint === "ticks") return t("ticksHint");
    if (hint === "area") return t("filterAreaHint");
    if (hint === "duration") return t("filterDurationHint");
    if (hint === "time") return t("filterTimeHint");
    if (hint === "cooldown") return t("triggerCooldownHint");
    if (hint === "rate") return t("triggerStartsPerMinuteHint");
    return t("triggerQueueHint");
  }

  function picked(event: Event): void {
    const select = event.currentTarget as HTMLSelectElement;
    onchange(select.value === "" ? undefined : select.value);
  }
</script>

<label class="field">
  {label}
  {#if !needed && showOptional}<span class="quiet">{t("optional")}</span>{/if}
  {#if field.spec.type === "boolean"}
    <input type="checkbox" checked={value === true} {disabled}
           onchange={(event) => onchange((event.currentTarget as HTMLInputElement).checked)} />
  {:else if options.length}
    <select value={shown} {required} {disabled} onchange={picked}>
      {#if field.blank}<option value="">—</option>{/if}
      {#each options as option (option.value)}<option value={option.value}>{option.label}</option>{/each}
    </select>
  {:else}
    <input type={inputType(field.spec)} step="any" value={shown} {required} {disabled} spellcheck="false"
           placeholder={field.placeholder ?? ""}
           oninput={(event) => onchange((event.currentTarget as HTMLInputElement).value)} />
  {/if}
  {#if field.hint}<small class="quiet">{hintText(field.hint)}</small>{/if}
</label>
