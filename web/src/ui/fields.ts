// One place that turns registry fields into form inputs (docs/WEB.md P11, docs/EVENTS.md E10).
// The Bot operation forms, the run form and the trigger editor all draw a field with the same component,
// so a registry type gains its input once, and a trigger filter is generated exactly like an operation field.

export type Spec = {
  type: string;
  choices?: string[];
  default?: unknown;
  maxBlocks?: number;
  clause?: string;
};

/** What one input needs beyond the registry spec: where it came from, and what its label says. */
export type Field = {
  name: string;
  spec: Spec;
  required: boolean;
  /** A name to label this input with when it is not the registry name (an area's `except`, for one). */
  label?: string;
  /** A script chooses its own parameter names, so those stay as written rather than through the field table. */
  rawLabel?: boolean;
  hint?: "ticks" | "area" | "duration" | "time" | "cooldown" | "rate" | "queue";
  placeholder?: string;
  options?: { value: string; label: string }[];
  /** A select starts with an empty choice, for a value that may be left out. */
  blank?: boolean;
};

const CHOICES: Record<string, string[]> = { dimension: ["overworld", "nether", "end"] };

/** Event dimension filters are named `the_end` on the wire, as the plugin reports them; people read `end`. */
export const DIMENSION_END = "the_end";

export function choicesOf(name: string, spec: Spec): string[] | undefined {
  return spec.choices ?? CHOICES[name];
}

export function inputType(spec: Spec): "number" | "text" {
  return spec.type === "integer" || spec.type === "number" ? "number" : "text";
}

/** A typed value as the operation or trigger message takes it. */
export function convert(value: any, spec: Spec): any {
  if (spec.type === "boolean") return value === true;
  if (spec.type === "integer" || spec.type === "number") return Number(value);
  const text = String(value).trim();
  const numeric = /^-?\d+(\.\d+)?$/.test(text);
  if (spec.type === "coordinate") return numeric ? Number(text) : text;
  if (spec.type === "stringOrInteger" || spec.type === "integerOrDefault") return numeric ? Number(text) : text;
  return text;
}

/** A value for a field, with a default standing in while it has not been typed. */
export function valueOf(field: Field, values: Record<string, any>): any {
  return values[field.name] === undefined || values[field.name] === "" ? field.spec.default : values[field.name];
}

export function defaultValues(fields: Field[]): Record<string, any> {
  return Object.fromEntries(fields
    .filter((field) => field.spec.default !== undefined)
    .map((field) => [field.name, field.spec.default]));
}

/** The area clause text the server reads: `within <values> [except <values>]`. */
export function areaClause(within: unknown, except: unknown = ""): string {
  const inside = String(within ?? "").trim();
  if (!inside) return "";
  const outside = String(except ?? "").trim();
  return outside ? `within ${inside} except ${outside}` : `within ${inside}`;
}

/** The canonical clause text back into the two inputs the form shows. */
export function splitArea(text: unknown): { within: string; except: string } {
  const full = String(text ?? "").trim().replace(/^within\s+/, "");
  const at = full.search(/\sexcept\s/);
  return at < 0
    ? { within: full, except: "" }
    : { within: full.slice(0, at), except: full.slice(at + " except ".length) };
}

/** The filter inputs of an event kind, in registry order. An area is its `within` values and an optional `except`. */
export function filterFields(filters: Record<string, Spec>, require: string[][] = []): Field[] {
  // A field in every alternative is needed whichever one is chosen; a field in only one is not.
  const required = new Set<string>();
  for (const name of require[0] ?? []) {
    if (require.every((names) => names.includes(name))) required.add(name);
  }
  const fields: Field[] = [];
  for (const [name, spec] of Object.entries(filters)) {
    if (spec.type === "area") {
      fields.push({
        name, spec, required: required.has(name), label: name,
        hint: "area", placeholder: "0 64 0 3 65 3",
      });
      fields.push({
        name: `${name}Except`, spec: { type: "string" }, required: false, label: "except",
        placeholder: "1 64 1",
      });
      continue;
    }
    fields.push({
      name, spec, required: required.has(name), label: name,
      hint: spec.type === "duration" ? "duration" : spec.type === "time" ? "time" : undefined,
      placeholder: spec.type === "block" ? "minecraft:stone" : undefined,
      blank: spec.type === "dimension",
    });
  }
  return fields;
}

/** The values a filter form sends; blank filters are left out so the kind's defaults apply. */
export function filterValues(fields: Field[], values: Record<string, any>): Record<string, string | number> {
  const given: Record<string, string | number> = {};
  const areas = new Set(fields.filter((field) => field.spec.type === "area").map((field) => field.name));
  for (const field of fields) {
    if (field.spec.type === "area") {
      const text = areaClause(values[field.name], values[`${field.name}Except`]);
      if (text) given[field.name] = text;
      continue;
    }
    if (field.label === "except" && areas.has(field.name.slice(0, -"Except".length))) continue;
    const value = values[field.name];
    if (value === undefined || value === "" || (typeof value === "string" && value.trim() === "")) continue;
    if (field.spec.type === "dimension") {
      given[field.name] = value === "end" ? DIMENSION_END : String(value);
    } else if (field.spec.type === "integer") {
      given[field.name] = Number(value);
    } else {
      given[field.name] = String(value).trim();
    }
  }
  return given;
}

/** The run form's inputs for a declared schema, without the trigger's `event` parameter. */
export function scriptFields(schema: any[], groups: any[] = [], bots: any[] = []): Field[] {
  return (schema ?? []).filter((spec) => spec?.type !== "event").map((spec) => {
    const field: Field = { name: spec.name, spec, required: false, label: spec.name, rawLabel: true };
    if (spec.type === "group") {
      field.options = groups.map((group) => ({ value: `#${group.name}`, label: `#${group.name}` }));
      field.blank = true;
    } else if (spec.type === "bot") {
      field.options = bots.map((bot) => ({ value: bot.name, label: bot.name }));
      field.blank = true;
    } else if (spec.type === "choice") {
      field.options = (spec.choices ?? []).map((choice: string) => ({ value: choice, label: choice }));
    } else if (spec.type === "pos") {
      field.placeholder = "x y z";
    }
    return field;
  });
}

/** A stored argument list as the values the read-only script parameter fields show. */
export function storedArguments(schema: any[], arguments_: string[]): Record<string, any> {
  const values: Record<string, any> = {};
  scriptFields(schema).forEach((field, index) => {
    const text = arguments_[index];
    if (text === undefined) {
      if (field.spec.default !== undefined) values[field.name] = field.spec.default;
      return;
    }
    if (field.spec.type === "number") values[field.name] = text === "" ? "" : Number(text);
    else if (field.spec.type === "boolean") values[field.name] = text === "true";
    else values[field.name] = text;
  });
  return values;
}

/** The positional arguments a trigger stores: one per run parameter, in order, defaults filling the gaps. */
export function triggerArguments(schema: any[], values: Record<string, any>): string[] {
  return scriptFields(schema).map((field) => {
    const value = valueOf(field, values);
    if (field.spec.type === "boolean") return value === true ? "true" : "false";
    return value === undefined || value === null ? "" : String(value).trim();
  });
}

/** A filter value as a read-only field shows it, mapping the wire's dimension name back to the page's. */
export function shownFilterValue(field: Field, filter: Record<string, any>): any {
  if (field.spec.type === "area") return splitArea(filter[field.name]).within;
  if (field.label === "except") return splitArea(filter[field.name.slice(0, -"Except".length)]).except;
  const value = filter[field.name];
  if (field.spec.type === "dimension" && value === DIMENSION_END) return "end";
  return value === undefined ? field.spec.default : value;
}
