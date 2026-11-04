// Luau highlighting defined in-house, and completion from the registry and api.luau (docs/DIRECTION.md Web technology).
import { type Completion, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import { StreamLanguage, type StreamParser } from "@codemirror/language";
import api from "../../../runner/api.luau?raw";
import registry from "../../../core/src/endbot_core/operations.json";

const KEYWORDS = new Set(("and break continue do else elseif end for function if in local not or repeat return then "
  + "until while export type").split(" "));
const ATOMS = new Set(["true", "false", "nil"]);

interface State {
  /** The closing `]]` of a long string or comment we are inside, with its kind. */
  long: { close: string; kind: "string" | "comment" } | null;
}

const parser: StreamParser<State> = {
  startState: () => ({ long: null }),
  token(stream, state) {
    if (state.long) {
      const { close, kind } = state.long;
      if (stream.skipTo(close)) {
        stream.pos += close.length;
        state.long = null;
      } else {
        stream.skipToEnd();
      }
      return kind;
    }
    if (stream.eatSpace()) return null;
    if (stream.match("--")) {
      const long = stream.match(/^\[(=*)\[/) as RegExpMatchArray | null;
      if (long) {
        state.long = { close: `]${long[1]}]`, kind: "comment" };
        return "comment";
      }
      stream.skipToEnd();
      return "comment";
    }
    const longString = stream.match(/^\[(=*)\[/) as RegExpMatchArray | null;
    if (longString) {
      state.long = { close: `]${longString[1]}]`, kind: "string" };
      return "string";
    }
    const quote = stream.peek();
    if (quote === '"' || quote === "'" || quote === "`") {
      stream.next();
      let escaped = false;
      while (!stream.eol()) {
        const char = stream.next();
        if (char === quote && !escaped) break;
        escaped = !escaped && char === "\\";
      }
      return "string";
    }
    if (stream.match(/^0[xX][0-9a-fA-F_]+|^\d[\d_]*(\.[\d_]*)?([eE][+-]?\d+)?/)) return "number";
    if (stream.match(/^[A-Za-z_]\w*/)) {
      const word = stream.current();
      if (KEYWORDS.has(word)) return "keyword";
      if (ATOMS.has(word)) return "atom";
      return GLOBALS.has(word) ? "variableName.standard" : "variableName";
    }
    stream.next();
    return null;
  },
  languageData: { commentTokens: { line: "--" } },
};

const GLOBALS = new Set([...api.matchAll(/^function (\w+)\(/gm)].map((match) => match[1]!));
const PARAM_TYPES = [...new Set([...api.matchAll(/param\.(\w+)/g)].map((match) => match[1]!))];
const OPERATIONS = Object.keys((registry as { operations: Record<string, unknown> }).operations)
  .filter((name) => !["list", "ping", "help", "menu"].includes(name));

const GLOBAL_COMPLETIONS: Completion[] = [
  ...[...GLOBALS].map((label) => ({ label, type: "function" })),
  ...[...KEYWORDS].map((label) => ({ label, type: "keyword" })),
];
const METHOD_COMPLETIONS: Completion[] = OPERATIONS.map((label) => ({ label, type: "method" }));
const PARAM_COMPLETIONS: Completion[] = PARAM_TYPES.map((label) => ({ label, type: "function" }));

export function complete(context: CompletionContext): CompletionResult | null {
  const method = context.matchBefore(/:\w*$/);
  if (method) return { from: method.from + 1, options: METHOD_COMPLETIONS, validFor: /^\w*$/ };
  const param = context.matchBefore(/\bparam\.\w*$/);
  if (param) return { from: param.from + "param.".length, options: PARAM_COMPLETIONS, validFor: /^\w*$/ };
  const word = context.matchBefore(/\w+$/);
  if (!word && !context.explicit) return null;
  return { from: word ? word.from : context.pos, options: GLOBAL_COMPLETIONS, validFor: /^\w*$/ };
}

export const luau = StreamLanguage.define(parser);
