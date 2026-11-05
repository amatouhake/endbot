<script lang="ts">
  // Operating a Bot or a Group. Common things are buttons; everything else is one form built from the registry
  // (docs/WEB.md P11), so an operation added there appears here with its checks.
  import registry from "../../../core/src/endbot_core/operations.json";
  import type { Connection } from "./api";
  import { confirmDialog } from "./dialogs.svelte";
  import { fieldLabel, opLabel, t } from "./i18n";

  let { connection, target, group = false, onchanged }: {
    connection: Connection;
    target: string;
    group?: boolean;
    onchanged: (op: string, fields: Record<string, unknown>) => void;
  } = $props();

  type Spec = { type: string; choices?: string[]; clause?: string };
  type Variant = { positional: [string, Spec][]; options: Record<string, Spec>; prefix: string[] };
  const OPERATIONS = (registry as unknown as { operations: Record<string, Variant[]> }).operations;

  type SectionKey = "catMove" | "catLook" | "catAct" | "catInventory" | "catInfo" | "catBot" | "catOther";
  const SECTIONS: { key: SectionKey; operations: string[] }[] = [
    { key: "catMove", operations: ["move", "jump", "sprint", "sneak", "stop"] },
    { key: "catLook", operations: ["look", "tp"] },
    { key: "catAct", operations: ["attack", "destroy", "use", "interact", "dismount"] },
    { key: "catInventory", operations: ["inventory", "hotbar", "hold", "swap", "drop"] },
    { key: "catInfo", operations: ["status", "target", "block", "blocks", "entities", "entity"] },
    { key: "catBot", operations: ["spawn", "resume", "reconnect", "despawn", "radius", "rename", "visibility", "forget"] },
  ];
  // Whatever the registry gains appears here until someone gives it a better home.
  const PLACED = new Set(SECTIONS.flatMap((item) => item.operations));
  const OFF_THE_WEB = new Set(["menu", "help", "owner", "list", "ping"]);
  const OTHERS = Object.keys(OPERATIONS).filter((name) => !PLACED.has(name) && !OFF_THE_WEB.has(name));
  if (OTHERS.length) SECTIONS.push({ key: "catOther", operations: OTHERS });
  // Asked again before running: they cannot be taken back from here.
  const CONFIRM = new Set(["forget"]);
  // The command refuses these for a Group: they need one Bot.
  const ONE_BOT_ONLY = new Set(["rename", "forget"]);
  const offered = (operations: string[]): string[] => operations.filter((op) => !group || !ONE_BOT_ONLY.has(op));

  let ticks = $state(20);
  let operation = $state("");
  let variant = $state(0);
  let fields = $state<Record<string, any>>({});
  let busy = $state(false);
  let results = $state<{ text: string; failed: boolean }[]>([]);

  const shapes = $derived(OPERATIONS[operation] ?? []);
  const shape = $derived(shapes[Math.min(variant, shapes.length - 1)]);

  function variantLabel(item: Variant): string {
    const words = [...item.prefix, ...item.positional.map(([name]) => fieldLabel(name))];
    return words.length ? words.join(" ") : t("variantPlain");
  }

  function convert(value: any, spec: Spec): any {
    if (spec.type === "boolean") return value === true;
    if (spec.type === "integer" || spec.type === "number") return Number(value);
    const text = String(value).trim();
    const numeric = /^-?\d+(\.\d+)?$/.test(text);
    if (spec.type === "coordinate") return numeric ? Number(text) : text;
    if (spec.type === "stringOrInteger" || spec.type === "integerOrDefault") return numeric ? Number(text) : text;
    return text;
  }

  function parameters(item: Variant): Record<string, unknown> {
    const given: Record<string, unknown> = {};
    for (const [name, spec] of [...item.positional, ...Object.entries(item.options)]) {
      const value = fields[name];
      if (value === undefined || value === "" || (spec.type === "boolean" && value === false)) continue;
      given[name] = convert(value, spec);
    }
    return given;
  }

  export async function send(op: string, params: Record<string, unknown> = {}): Promise<any> {
    busy = true;
    const answer = await connection.request("op", { operation: { op, target, params } });
    busy = false;
    const failed = answer.code !== "ok";
    // Replies already say which Bot they are about; only a reply with no words needs the label.
    const lines: string[] = answer.messages?.length ? answer.messages
      : [`${target} ${opLabel(op)}: ${failed ? t("opFailed", { code: answer.code }) : t("opDone")}`];
    results = [...lines.map((text) => ({ text, failed })), ...results].slice(0, 40);
    if (!failed) onchanged(op, params);
    return answer;
  }

  function step(direction: string): void {
    void send("move", { direction, ticks: Number(ticks) || 20 });
  }

  async function submit(): Promise<void> {
    const op = operation;
    if (CONFIRM.has(op)
        && !(await confirmDialog(`${target}: ${opLabel(op)}`, opLabel(op), { message: t("opCannotUndo"), danger: true }))) return;
    await send(op, parameters(shape!));
  }

  function inputType(spec: Spec): string {
    return spec.type === "integer" || spec.type === "number" ? "number" : "text";
  }
</script>

<section class="card">
  <h2>{t("quickTitle")}</h2>
  <div class="quick">
    <div class="pad" role="group" aria-label={opLabel("move")}>
      <span></span>
      <button disabled={busy} aria-label={t("dirForward")} onclick={() => step("forward")}>↑</button>
      <span></span>
      <button disabled={busy} aria-label={t("dirLeft")} onclick={() => step("left")}>←</button>
      <button disabled={busy} aria-label={t("dirBackward")} onclick={() => step("backward")}>↓</button>
      <button disabled={busy} aria-label={t("dirRight")} onclick={() => step("right")}>→</button>
    </div>
    <label class="ticks">{t("forTicks")}
      <input type="number" min="1" bind:value={ticks} />
    </label>
    <div class="row">
      {#each ["jump", "attack", "destroy", "use"] as op}
        <button disabled={busy} onclick={() => send(op)}>{opLabel(op)}</button>
      {/each}
    </div>
  </div>
</section>

<section class="card">
  <h2>{t("opAllTitle")}</h2>
  <label class="field">{t("opChoose")}
    <select bind:value={operation} onchange={() => { variant = 0; fields = {}; }}>
      <option value="">—</option>
      {#each SECTIONS as section}
        <optgroup label={t(section.key)}>
          {#each offered(section.operations) as op}<option value={op}>{opLabel(op)}</option>{/each}
        </optgroup>
      {/each}
    </select>
  </label>
  {#if operation && shape}
    <form onsubmit={(event) => { event.preventDefault(); void submit(); }}>
      {#if shapes.length > 1}
        <label class="field">{t("opForm")}
          <select bind:value={variant} onchange={() => { fields = {}; }}>
            {#each shapes as item, index}<option value={index}>{variantLabel(item)}</option>{/each}
          </select>
        </label>
      {/if}
      {#each shape.positional as [name, spec]}
        <label class="field">{fieldLabel(name)}
          {#if spec.choices}
            <select bind:value={fields[name]} required>
              <option value={undefined}>—</option>
              {#each spec.choices as choice}<option value={choice}>{choice}</option>{/each}
            </select>
          {:else}
            <input type={inputType(spec)} step="any" bind:value={fields[name]} required spellcheck="false" />
          {/if}
        </label>
      {/each}
      {#each Object.entries(shape.options).filter(([, spec]) => spec.type !== "area") as [name, spec]}
        <label class="field">{fieldLabel(name)} <span class="quiet">{t("optional")}</span>
          {#if spec.type === "boolean"}
            <input type="checkbox" bind:checked={fields[name]} />
          {:else if spec.choices}
            <select bind:value={fields[name]}>
              <option value={undefined}>—</option>
              {#each spec.choices as choice}<option value={choice}>{choice}</option>{/each}
            </select>
          {:else}
            <input type={inputType(spec)} step="any" bind:value={fields[name]} spellcheck="false" />
          {/if}
        </label>
      {/each}
      <button class="primary" disabled={busy}>{t("opRun", { op: opLabel(operation) })}</button>
    </form>
  {/if}
</section>

{#if results.length}
  <section class="card">
    <h2>{t("results")}</h2>
    <ul class="results">
      {#each results as item}<li class:failed={item.failed}>{item.text}</li>{/each}
    </ul>
  </section>
{/if}
