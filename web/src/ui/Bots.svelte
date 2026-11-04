<script lang="ts">
  // Every form comes from the registry (docs/WEB.md P11), so an operation added there appears here with its checks.
  import registry from "../../../core/src/endbot_core/operations.json";
  import type { Connection } from "./api";
  import { t } from "./i18n";

  let { connection, status, onchanged }: { connection: Connection; status: any; onchanged: () => void } = $props();

  type Spec = { type: string; choices?: string[]; clause?: string };
  type Variant = { positional: [string, Spec][]; options: Record<string, Spec>; prefix: string[] };
  const OPERATIONS = (registry as unknown as { operations: Record<string, Variant[]> }).operations;

  type CategoryKey = "catMove" | "catLook" | "catAct" | "catInventory" | "catInfo" | "catBot" | "catOther";
  const CATEGORIES: { key: CategoryKey; operations: string[] }[] = [
    { key: "catMove", operations: ["move", "jump", "sprint", "sneak", "stop"] },
    { key: "catLook", operations: ["look", "tp"] },
    { key: "catAct", operations: ["attack", "destroy", "use", "interact", "dismount"] },
    { key: "catInventory", operations: ["inventory", "hotbar", "hold", "swap", "drop"] },
    { key: "catInfo", operations: ["status", "target", "block", "blocks", "entities", "entity"] },
    { key: "catBot", operations: ["spawn", "resume", "reconnect", "despawn", "radius", "rename", "visibility", "forget"] },
  ];
  // Whatever the registry gains appears here until someone gives it a better home.
  const PLACED = new Set(CATEGORIES.flatMap((item) => item.operations));
  const OFF_THE_WEB = new Set(["menu", "help", "owner", "list", "ping"]);
  const OTHERS = Object.keys(OPERATIONS).filter((name) => !PLACED.has(name) && !OFF_THE_WEB.has(name));
  if (OTHERS.length) CATEGORIES.push({ key: "catOther", operations: OTHERS });
  // Asked again before running: they cannot be taken back from here.
  const CONFIRM = new Set(["forget"]);

  let target = $state("");
  let category = $state<CategoryKey>("catMove");
  let operation = $state("move");
  let variant = $state(0);
  let fields = $state<Record<string, any>>({});
  let busy = $state(false);
  let results = $state<{ text: string; failed: boolean }[]>([]);
  let info = $state<string[]>([]);
  let newBot = $state("");

  const targets = $derived([
    ...(status?.bots ?? []).map((bot: any) => ({ value: bot.name, label: bot.name, state: bot.state })),
    ...(status?.groups ?? []).map((group: any) => ({ value: `#${group.name}`, label: `#${group.name}`, state: null })),
  ]);
  const shapes = $derived(OPERATIONS[operation] ?? []);
  const shape = $derived(shapes[Math.min(variant, shapes.length - 1)]);

  function choose(next: string): void {
    operation = next;
    variant = 0;
    fields = {};
  }

  function pickCategory(key: CategoryKey): void {
    category = key;
    choose(CATEGORIES.find((item) => item.key === key)!.operations[0]!);
  }

  function variantLabel(item: Variant): string {
    const words = [...item.prefix, ...item.positional.map(([name]) => name)];
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

  function report(answer: any, label: string): void {
    const failed = answer.code !== "ok";
    // Replies already say which Bot they are about; only a reply with no words needs the label.
    const lines: string[] = answer.messages?.length ? answer.messages
      : [`${label}: ${failed ? t("opFailed", { code: answer.code }) : t("opDone")}`];
    results = [...lines.map((text) => ({ text, failed })), ...results].slice(0, 40);
  }

  async function send(op: string, name: string, params: Record<string, unknown>): Promise<any> {
    busy = true;
    const answer = await connection.request("op", { operation: { op, target: name, params } });
    busy = false;
    report(answer, `${name} ${op}`);
    onchanged();
    return answer;
  }

  async function submit(): Promise<void> {
    if (CONFIRM.has(operation) && !confirm(t("opConfirm", { op: operation, target }))) return;
    await send(operation, target, parameters(shape!));
    if (operation === "rename" && fields.name) target = fields.name;
  }

  async function loadInfo(name: string): Promise<void> {
    const answer = await connection.request("op", { operation: { op: "status", target: name, params: {} } });
    info = answer.messages ?? [];
  }

  function pick(value: string): void {
    target = value;
    info = [];
    void loadInfo(value);
  }

  async function spawnBot(): Promise<void> {
    const name = newBot.trim();
    if (!name) return;
    const answer = await send("spawn", name, {});
    if (answer.code === "ok") {
      newBot = "";
      target = name;
    }
  }

  function inputType(spec: Spec): string {
    return spec.type === "integer" || spec.type === "number" ? "number" : "text";
  }
</script>

<div class="layout">
  <aside>
    <section class="card">
      <h2>{t("targets")}</h2>
      {#if status?.bots === null}
        <p class="quiet">{t("botsUnknown")}</p>
      {:else if targets.length === 0}
        <p class="quiet">{t("noBots")}</p>
      {:else}
        <ul class="pick">
          {#each targets as item}
            <li>
              <button class:current={target === item.value} onclick={() => pick(item.value)}>
                <span>{item.label}</span>
                {#if item.state}<span class="tag" class:good={item.state === "online"}>{item.state}</span>{/if}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
      <form class="row spawn" onsubmit={(event) => { event.preventDefault(); void spawnBot(); }}>
        <input bind:value={newBot} aria-label={t("newBotName")} placeholder={t("newBotName")} spellcheck="false" maxlength="16" />
        <button disabled={busy || !newBot.trim()}>{t("spawn")}</button>
      </form>
    </section>
  </aside>

  <div class="main">
    {#if !target}
      <section class="card"><p class="quiet">{t("targetPick")}</p></section>
    {:else}
      <section class="card">
        <div class="heading">
          <h2>{target}</h2>
          <button class="danger" disabled={busy} onclick={() => send("stop", target, {})}>{t("stopAll")}</button>
        </div>
        {#each info as line}<p class="quiet info">{line}</p>{/each}
      </section>
      <section class="card">
        <div class="chips" role="group" aria-label={t("catGroup")}>
          {#each CATEGORIES as item}
            <button class:current={category === item.key} aria-pressed={category === item.key}
                    onclick={() => pickCategory(item.key)}>{t(item.key)}</button>
          {/each}
        </div>
        <div class="chips ops" role="group" aria-label={t("opGroup")}>
          {#each CATEGORIES.find((item) => item.key === category)!.operations as op}
            <button class:current={operation === op} aria-pressed={operation === op} onclick={() => choose(op)}>{op}</button>
          {/each}
        </div>
        <form onsubmit={(event) => { event.preventDefault(); void submit(); }}>
          {#if shapes.length > 1}
            <label class="field">{t("opForm")}
              <select bind:value={variant} onchange={() => { fields = {}; }}>
                {#each shapes as item, index}<option value={index}>{variantLabel(item)}</option>{/each}
              </select>
            </label>
          {/if}
          {#if shape}
            {#each shape.positional as [name, spec]}
              <label class="field">{name}
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
              <label class="field">{name} <span class="quiet">{t("optional")}</span>
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
          {/if}
          <button class="primary" disabled={busy}>{t("opRun", { op: operation })}</button>
        </form>
      </section>
    {/if}
    {#if results.length}
      <section class="card">
        <h2>{t("results")}</h2>
        <ul class="results">
          {#each results as item}<li class:failed={item.failed}>{item.text}</li>{/each}
        </ul>
      </section>
    {/if}
  </div>
</div>

<style>
  .layout { display: grid; gap: 1rem; grid-template-columns: minmax(14rem, 18rem) minmax(0, 1fr); align-items: start; }
  @media (max-width: 760px) { .layout { grid-template-columns: minmax(0, 1fr); } }
  .main { min-width: 0; }
  .heading { display: flex; justify-content: space-between; align-items: center; gap: .75rem; }
  .heading h2 { margin: 0; overflow-wrap: anywhere; }
  .info { margin: .4rem 0 0; overflow-wrap: anywhere; }
  .pick li { padding: 0; }
  .pick button { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: .5rem; border: 0; background: none; padding: .4rem .5rem; border-radius: .35rem; text-align: left; min-height: 2.75rem; }
  .pick button.current { background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .row { display: flex; gap: .5rem; }
  .spawn { margin-top: .75rem; }
  .spawn input { min-width: 0; flex: 1; }
  .chips { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .6rem; }
  .chips button { min-height: 2.5rem; padding: .3rem .8rem; }
  .chips button.current { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .chips.ops { padding-bottom: .6rem; border-bottom: 1px solid var(--line); }
  .field { margin: 0 0 .6rem; }
  .field input:not([type="checkbox"]), .field select { display: block; width: 100%; box-sizing: border-box; }
  select { font: inherit; min-height: 2.75rem; padding: .4rem .6rem; border-radius: .45rem; border: 1px solid var(--line); background: var(--bg); color: var(--text); }
  .results li { display: block; overflow-wrap: anywhere; }
  .results li.failed { color: var(--bad); }
</style>
