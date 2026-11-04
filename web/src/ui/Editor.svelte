<script lang="ts">
  import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
  import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
  import { bracketMatching, HighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
  import { type Diagnostic, lintGutter, setDiagnostics } from "@codemirror/lint";
  import { Compartment, EditorState } from "@codemirror/state";
  import { drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
  import { tags } from "@lezer/highlight";
  import { onMount, untrack } from "svelte";
  import { complete, luau } from "./luau";

  let { value, readOnly = false, problem = null, onchange }: {
    value: string;
    readOnly?: boolean;
    problem?: { line?: number; message: string } | null;
    onchange: (text: string) => void;
  } = $props();

  let host: HTMLDivElement;
  let view = $state.raw<EditorView | undefined>(undefined);
  const editable = new Compartment();

  function editability(locked: boolean) {
    return [EditorState.readOnly.of(locked), EditorView.editable.of(!locked)];
  }

  // Colours are the page's variables, so the editor follows light and dark with it.
  const highlight = HighlightStyle.define([
    { tag: tags.keyword, color: "var(--code-keyword)", fontWeight: "600" },
    { tag: tags.atom, color: "var(--code-atom)" },
    { tag: tags.number, color: "var(--code-atom)" },
    { tag: tags.string, color: "var(--code-string)" },
    { tag: tags.comment, color: "var(--quiet)", fontStyle: "italic" },
    { tag: tags.standard(tags.variableName), color: "var(--code-api)" },
  ]);

  const theme = EditorView.theme({
    "&": { color: "var(--text)", backgroundColor: "var(--surface)", maxHeight: "60vh", fontSize: ".9rem" },
    "&.cm-focused": { outline: "none" },
    ".cm-scroller": { fontFamily: "ui-monospace, Consolas, monospace", overflow: "auto", minHeight: "16rem" },
    ".cm-content": { caretColor: "var(--text)" },
    ".cm-cursor": { borderLeftColor: "var(--text)" },
    ".cm-gutters": { backgroundColor: "var(--bg)", color: "var(--quiet)", borderRight: "1px solid var(--line)" },
    ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--accent) 7%, transparent)" },
    ".cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--accent) 12%, transparent)" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
      backgroundColor: "color-mix(in srgb, var(--accent) 28%, transparent)",
    },
    ".cm-tooltip": { backgroundColor: "var(--surface)", color: "var(--text)", border: "1px solid var(--line)" },
  });

  onMount(() => {
    // The editor adds its styles as a <style> element in a document, which the Content-Security-Policy forbids. In a
    // shadow root it uses a constructed stylesheet instead, so the policy can stay as strict as it is.
    const root = host.attachShadow({ mode: "open" });
    view = new EditorView({
      parent: root,
      root,
      state: EditorState.create({
        doc: untrack(() => value),
        extensions: [
          lineNumbers(), lintGutter(), history(), drawSelection(), highlightActiveLine(), indentOnInput(),
          bracketMatching(), closeBrackets(), luau, syntaxHighlighting(highlight), theme,
          autocompletion({ override: [complete] }), EditorState.tabSize.of(4), EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": "Script source", autocapitalize: "off", spellcheck: "false" }),
          keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...completionKeymap, indentWithTab]),
          editable.of(editability(untrack(() => readOnly))),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) onchange(update.state.doc.toString());
          }),
        ],
      }),
    });
    return () => view?.destroy();
  });

  // A new revision or another script arrives from outside: replace the text, but never while it already matches.
  $effect(() => {
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
    }
  });

  $effect(() => {
    view?.dispatch({ effects: editable.reconfigure(editability(readOnly)) });
  });

  $effect(() => {
    if (!view) return;
    const diagnostics: Diagnostic[] = [];
    if (problem?.line && problem.line <= view.state.doc.lines) {
      const line = view.state.doc.line(problem.line);
      diagnostics.push({ from: line.from, to: line.to, severity: "error", message: problem.message });
    }
    view.dispatch(setDiagnostics(view.state, diagnostics));
  });
</script>

<div class="editor" bind:this={host}></div>

<style>
  .editor { border: 1px solid var(--line); border-radius: .45rem; overflow: hidden; background: var(--surface); }
</style>
