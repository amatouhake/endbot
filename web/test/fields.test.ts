// The registry-driven form fields, shared by the Bot operation forms, the run form and the trigger editor.
import { describe, expect, it } from "vitest";
import registry from "../../core/src/endbot_core/operations.json";
import {
  areaClause, choicesOf, filterFields, filterValues, scriptFields, splitArea, triggerArguments,
} from "../src/ui/fields";

const EVENTS = (registry as any).events as Record<string, { filters: Record<string, any>; require: string[][] }>;
const BLOCK_CHANGE = EVENTS["block.change"]!;

describe("the shared field generator", () => {
  it("makes one input per block.change filter, with the area's except beside it", () => {
    const fields = filterFields(BLOCK_CHANGE.filters, BLOCK_CHANGE.require);
    expect(fields.map((field) => [field.name, field.spec.type])).toEqual([
      ["area", "area"],
      ["areaExcept", "string"],
      ["dimension", "dimension"],
      ["from", "block"],
      ["to", "block"],
    ]);
    expect(fields.find((field) => field.name === "area")?.required).toBe(true);
    expect(fields.find((field) => field.name === "areaExcept")?.required).toBe(false);
    const dimension = fields.find((field) => field.name === "dimension")!;
    expect(choicesOf(dimension.name, dimension.spec)).toEqual(["overworld", "nether", "end"]);
  });

  it("sends an area as the clause text the server takes", () => {
    const fields = filterFields(BLOCK_CHANGE.filters, BLOCK_CHANGE.require);
    expect(filterValues(fields, { area: "0 64 0 3 65 3", dimension: "overworld" }))
      .toEqual({ area: "within 0 64 0 3 65 3", dimension: "overworld" });
    expect(filterValues(fields, {
      area: " 0 64 0 3 65 3 ", areaExcept: " 1 64 1 ", dimension: "end", from: "stone", to: "air",
    })).toEqual({
      area: "within 0 64 0 3 65 3 except 1 64 1",
      // The plugin names the end `the_end`; the page offers the shorter word.
      dimension: "the_end",
      from: "stone",
      to: "air",
    });
    expect(filterValues(fields, { area: "  ", dimension: "nether" })).toEqual({ dimension: "nether" });
  });

  it("turns the clause text back into its within and except inputs", () => {
    expect(areaClause("0 64 0", "")).toBe("within 0 64 0");
    expect(areaClause("0 64 0 3 65 3", "1 64 1")).toBe("within 0 64 0 3 65 3 except 1 64 1");
    expect(splitArea("within 0 64 0 3 65 3 except 1 64 1"))
      .toEqual({ within: "0 64 0 3 65 3", except: "1 64 1" });
    expect(splitArea("within 0 64 0")).toEqual({ within: "0 64 0", except: "" });
  });

  it("takes the run form's script parameters without the trigger's event", () => {
    const schema = [
      { name: "count", type: "number", default: 2 },
      { name: "event", type: "event" },
      { name: "loud", type: "boolean" },
    ];
    expect(scriptFields(schema).map((field) => field.name)).toEqual(["count", "loud"]);
    expect(triggerArguments(schema, { count: 7, loud: false })).toEqual(["7", "false"]);
    expect(triggerArguments(schema, {})).toEqual(["2", "false"]);
  });
});
