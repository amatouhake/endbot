<script lang="ts">
  // Operating a Bot or a Group, with the in-game menu's categories: anything is two clicks away and needs no scrolling.
  // The fields come from the registry (docs/WEB.md P11), so an operation added there appears here with its checks.
  import registry from "../../../core/src/endbot_core/operations.json";
  import type { Connection } from "./api";
  import { confirmDialog } from "./dialogs.svelte";
  import { fieldLabel, opLabel, t } from "./i18n";

  let { connection, target, group = false, botState = "online", onchanged }: {
    connection: Connection;
    target: string;
    group?: boolean;
    botState?: string;
    onchanged: (op: string, fields: Record<string, unknown>) => void;
  } = $props();

  type Spec = { type: string; choices?: string[]; clause?: string };
  type Variant = { positional: [string, Spec][]; options: Record<string, Spec>; prefix: string[] };
  type Mode = "once" | "continuous" | "interval" | "stop";
  const OPERATIONS = (registry as unknown as { operations: Record<string, Variant[]> }).operations;

  type SectionKey = "catMove" | "catLook" | "catTp" | "catAct" | "catInventory" | "catInfo" | "catBot" | "catOther";
  const SECTIONS: { key: SectionKey; operations: string[] }[] = [
    { key: "catMove", operations: ["jump", "sprint", "sneak"] },
    { key: "catLook", operations: ["look"] },
    { key: "catTp", operations: ["tp"] },
    { key: "catAct", operations: ["attack", "destroy", "use", "interact", "dismount"] },
    { key: "catInventory", operations: ["inventory", "hotbar", "hold", "swap", "drop"] },
    { key: "catInfo", operations: ["status", "target", "block", "blocks", "entities", "entity"] },
    { key: "catBot", operations: ["spawn", "resume", "reconnect", "despawn", "radius", "rename", "visibility", "forget"] },
  ];
  // Whatever the registry gains appears here until someone gives it a better home.
  const PLACED = new Set(SECTIONS.flatMap((item) => item.operations));
  // Move is the pad on the movement section, and stop is always on show above this; the rest are not for the web.
  const NOT_LISTED = new Set(["move", "stop", "menu", "help", "owner", "list", "ping"]);
  const OTHERS = Object.keys(OPERATIONS).filter((name) => !PLACED.has(name) && !NOT_LISTED.has(name));
  if (OTHERS.length) SECTIONS.push({ key: "catOther", operations: OTHERS });
  // Asked again before running: they cannot be taken back from here.
  const CONFIRM = new Set(["forget"]);
  // The command refuses these for a Group: they need one Bot.
  const ONE_BOT_ONLY = new Set(["rename", "forget"]);
  // What the in-game menu offers a Bot that is not online; one on its way in or out can also be taken out of that.
  const WHEN_OFFLINE = new Set(["spawn", "resume", "radius", "rename", "visibility", "forget"]);
  const WHEN_BETWEEN = new Set([...WHEN_OFFLINE, "reconnect", "despawn", "status"]);
  // The grammar takes these two together.
  const PAIRED: Record<string, string> = { yaw: "pitch", pitch: "yaw" };
  // Left to the typed command, as in the in-game forms; an area has no input here yet.
  const COMMAND_ONLY = new Set(["facing", "facingX", "facingY", "facingZ"]);
  const CHOICES: Record<string, string[]> = { dimension: ["overworld", "nether", "end"] };
  const TICK_FIELDS = new Set(["ticks", "hold", "interval"]);
  // Each mode takes only its own clauses: `interval` replaces once and continuous, and `once` has no duration.
  const MODE_FIELDS: Record<Mode, string[]> = {
    once: ["hold"], continuous: ["hold", "ticks", "count"], interval: ["interval", "hold", "ticks"], stop: [],
  };

  const RECENT_KEY = "endbot.recentOps";
  const CATEGORY_KEY = "endbot.opCategory";
  const RECENT_COUNT = 5;

  function remembered<T>(key: string, fallback: T, valid: (value: unknown) => boolean): T {
    try {
      const value = JSON.parse(localStorage.getItem(key) ?? "null");
      return valid(value) ? value : fallback;
    } catch {
      return fallback;
    }
  }

  function keep(key: string, value: unknown): void {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // A convenience only: the page works the same without it.
    }
  }

  let ticks = $state(20);
  let category = $state<SectionKey>(remembered(CATEGORY_KEY, "catMove", (value) => typeof value === "string"));
  let recent = $state<string[]>(remembered(RECENT_KEY, [],
    (value) => Array.isArray(value) && value.every((item) => typeof item === "string")));
  let operation = $state("");
  let variant = $state<number | null>(null);
  let mode = $state<Mode | null>(null);
  let fields = $state<Record<string, any>>({});
  let busy = $state(false);
  let results = $state<{ text: string; failed: boolean }[]>([]);

  const sections = $derived(SECTIONS
    .map((section) => ({ key: section.key, operations: section.operations.filter(available) }))
    .filter((section) => section.operations.length > 0));
  const section = $derived(sections.find((item) => item.key === category) ?? sections[0]);
  // A section of one operation has nothing to choose between.
  const active = $derived(section?.operations.includes(operation) ? operation
    : section?.operations.length === 1 ? section.operations[0] : "");
  const shapes = $derived(OPERATIONS[active] ?? []);
  const shape = $derived(variant !== null ? shapes[variant] : shapes[0] && hasFields(shapes[0]) ? shapes[0] : undefined);
  const toggle = $derived(shapes.length === 1 && shapes[0].positional.length === 1 && shapes[0].positional[0][1].type === "boolean");
  const inputs = $derived(toggle || !shape ? [] : inputsOf(shape));
  const recentShown = $derived(recent.filter((op) => (op === "move" ? botState === "online" : available(op))));

  function available(op: string): boolean {
    if (group) return !ONE_BOT_ONLY.has(op);
    return botState === "online" || (botState === "offline" ? WHEN_OFFLINE : WHEN_BETWEEN).has(op);
  }

  function sectionOf(op: string): SectionKey | undefined {
    return op === "move" ? "catMove" : sections.find((item) => item.operations.includes(op))?.key;
  }

  function shownOptions(item: Variant): [string, Spec][] {
    return Object.entries(item.options).filter(([name, spec]) => spec.type !== "area" && !COMMAND_ONLY.has(name));
  }

  function hasFields(item: Variant): boolean {
    return item.positional.length > 0 || shownOptions(item).length > 0;
  }

  /** The command's modes as buttons, as on the in-game menu's first screen. */
  function modesOf(item: Variant | undefined): Mode[] {
    if (!item || item.positional.length || !item.options.mode?.choices) return [];
    const named = item.options.mode.choices as Mode[];
    return item.options.interval ? [...named.filter((name) => name !== "stop"), "interval", "stop"] : named;
  }

  function inputsOf(item: Variant): { name: string; spec: Spec; required: boolean }[] {
    if (modesOf(item).length) {
      return mode === null ? [] : MODE_FIELDS[mode]
        .filter((name) => item.options[name])
        .map((name) => ({ name, spec: item.options[name], required: name === "interval" }));
    }
    return [
      ...item.positional.map(([name, spec]): { name: string; spec: Spec; required: boolean } => ({ name, spec, required: true })),
      ...shownOptions(item).map(([name, spec]) => ({ name, spec, required: false })),
    ];
  }

  function variantLabel(item: Variant): string {
    const words = [...item.prefix, ...item.positional.map(([name]) => fieldLabel(name))];
    return words.length ? words.join(" ") : t("variantPlain");
  }

  function modeLabel(name: Mode): string {
    return t(({ once: "modeOnce", continuous: "modeContinuous", interval: "modeInterval", stop: "modeStop" } as const)[name]);
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

  function parameters(): Record<string, unknown> {
    const given: Record<string, unknown> = mode && mode !== "interval" ? { mode } : {};
    for (const { name, spec } of inputs) {
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

  /** From this panel: asks first where it must, and what worked is offered again under Recent. */
  async function run(op: string, params: Record<string, unknown> = {}): Promise<void> {
    if (CONFIRM.has(op)
        && !(await confirmDialog(`${target}: ${opLabel(op)}`, opLabel(op), { message: t("opCannotUndo"), danger: true }))) return;
    const answer = await send(op, params);
    if (answer.code !== "ok") return;
    recent = [op, ...recent.filter((item) => item !== op)].slice(0, RECENT_COUNT);
    keep(RECENT_KEY, recent);
  }

  function reset(): void {
    variant = null;
    mode = null;
    fields = {};
  }

  function chooseCategory(key: SectionKey): void {
    category = key;
    keep(CATEGORY_KEY, key);
    operation = "";
    reset();
  }

  function choose(op: string): void {
    operation = op;
    reset();
    const only = OPERATIONS[op];
    if (only.length === 1 && !hasFields(only[0])) void run(op);
  }

  function chooseRecent(op: string): void {
    const key = sectionOf(op);
    if (!key) return;
    chooseCategory(key);
    if (op !== "move") choose(op);
  }

  function chooseVariant(index: number): void {
    variant = index;
    mode = null;
    fields = {};
    if (!hasFields(shapes[index])) void run(active);
  }

  function chooseMode(name: Mode): void {
    mode = name;
    fields = {};
    // A mode with no clauses of its own has nothing left to ask.
    if (shape && inputsOf(shape).length === 0) void run(active, { mode: name });
  }

  function step(direction: string): void {
    void run("move", { direction, ticks: Number(ticks) || 20 });
  }

  function inputType(spec: Spec): string {
    return spec.type === "integer" || spec.type === "number" ? "number" : "text";
  }
</script>

<section class="card">
  <h2>{t("opsTitle")}</h2>
  {#if recentShown.length}
    <div class="choices recent" role="group" aria-label={t("opsRecent")}>
      <span class="quiet">{t("opsRecent")}</span>
      {#each recentShown as op (op)}
        <button disabled={busy} onclick={() => chooseRecent(op)}>{opLabel(op)}</button>
      {/each}
    </div>
  {/if}
  <div class="choices categories" role="group" aria-label={t("opsCategories")}>
    {#each sections as item (item.key)}
      <button aria-pressed={section?.key === item.key} onclick={() => chooseCategory(item.key)}>{t(item.key)}</button>
    {/each}
  </div>

  {#if section?.key === "catMove"}
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
        <small class="quiet">{t("ticksHint")}</small>
      </label>
      <button disabled={busy} onclick={() => run("move", { direction: "stop" })}>{t("moveStop")}</button>
    </div>
  {/if}

  {#if section && section.operations.length > 1}
    <div class="choices operations" role="group" aria-label={t(section.key)}>
      {#each section.operations as op (op)}
        <button aria-pressed={active === op} disabled={busy} onclick={() => choose(op)}>{opLabel(op)}</button>
      {/each}
    </div>
  {/if}

  {#if active}
    <div class="op-panel">
      {#if active === "tp"}
        <div class="choices">
          <button disabled={busy} onclick={() => run("tp", { target: "me" })}>{t("tpToMe")}</button>
        </div>
      {/if}
      {#if toggle}
        <div class="choices" role="group" aria-label={opLabel(active)}>
          <button disabled={busy} onclick={() => run(active, { value: true })}>{t("switchOn")}</button>
          <button disabled={busy} onclick={() => run(active, { value: false })}>{t("switchOff")}</button>
        </div>
      {:else}
        {#if shapes.length > 1}
          <div class="choices" role="group" aria-label={t("opForm")}>
            {#each shapes as item, index}
              <button aria-pressed={shape === item} disabled={busy} onclick={() => chooseVariant(index)}>{variantLabel(item)}</button>
            {/each}
          </div>
        {/if}
        {#if modesOf(shape).length}
          <div class="choices" role="group" aria-label={fieldLabel("mode")}>
            {#each modesOf(shape) as name (name)}
              <button aria-pressed={mode === name} disabled={busy} onclick={() => chooseMode(name)}>{modeLabel(name)}</button>
            {/each}
          </div>
        {/if}
        {#if inputs.length}
          <form onsubmit={(event) => { event.preventDefault(); void run(active, parameters()); }}>
            {#each inputs as { name, spec, required: always } (name)}
              {@const choices = spec.choices ?? CHOICES[name]}
              {@const required = always || (name in PAIRED && !!fields[PAIRED[name]])}
              <label class="field">{fieldLabel(name)}
                {#if !required}<span class="quiet">{t("optional")}</span>{/if}
                {#if spec.type === "boolean"}
                  <input type="checkbox" bind:checked={fields[name]} />
                {:else if choices}
                  <select bind:value={fields[name]} {required}>
                    <option value={undefined}>—</option>
                    {#each choices as choice}<option value={choice}>{choice}</option>{/each}
                  </select>
                {:else}
                  <input type={inputType(spec)} step="any" bind:value={fields[name]} {required} spellcheck="false" />
                {/if}
              </label>
            {/each}
            {#if inputs.some(({ name }) => TICK_FIELDS.has(name))}<p class="quiet">{t("ticksHint")}</p>{/if}
            <button class="primary" disabled={busy}>{t("opRun", { op: opLabel(active) })}</button>
          </form>
        {/if}
      {/if}
    </div>
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
