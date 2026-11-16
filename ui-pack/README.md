# Endbot UI resource pack (`ui-pack/`)

The source of the optional client-side *Endbot UI* resource pack. This README is for developers: how the pack works,
how it is built and validated, and what to check after a change. What players see and how they install it is in
[docs/UI_PACK.md](../docs/UI_PACK.md).

## Why

Two client limits make in-game script editing hard:

- Server forms are a fixed 225×200 UI-unit window with 32-unit buttons, so menus scroll a lot.
- The form text field is short and **drops line breaks**, so multi-line source has to go through a book and quill.

Neither is fixed by anything a server can put in a form. A resource pack can restyle the form screens on the client.
This pack is the in-game stopgap. The [web editor](../docs/WEB_DEPLOY.md) stays the long-term authoring path: it is
the only one that can give syntax highlighting, completion and diffs.

The pack is client-side only. Players add it as a global resource pack; the server's pack stack stays empty (no
`world_resource_packs.json`, no `texturepacks-required`, no `server.properties` change).

## Files

| File | Purpose |
| --- | --- |
| `manifest.json` | Format 2 manifest: header *Endbot UI* plus exactly one `resources` module, distinct UUIDs, three-part versions |
| `ui/server_form.json` | The overrides, in the `server_form` namespace |
| `pack_icon.png` | 128×128 icon shown in the client's resource list |
| `LICENSE` | Apache-2.0 |
| `NOTICE` | Copyright holder, and that the pack overrides vanilla element ids at runtime and bundles no Mojang assets |

`LICENSE` and `NOTICE` ship inside the `.mcpack`, because players download the pack on its own. The build refuses a
pack directory holding any other file, except this README at the root, which is never packaged.

## What it overrides

The pack redefines one vanilla element, `server_form.main_screen_content`, so that server forms use `endbot_*`
elements built on the vanilla ones. Every other element it defines is named `endbot_*`.

| Change | Applies to | Elements |
| --- | --- | --- |
| Larger window: screen size minus a margin, at most 420×300 (vanilla 225×200) | every server form | `endbot_long_form`, `endbot_custom_form` |
| Compact buttons: 24 units high (vanilla 32), 24-unit icons | every action form | `endbot_dynamic_button` |
| Multi-line field: 150 units high, scrolls, Enter inserts a line break, up to 32768 characters | only inputs whose label starts with the marker | `endbot_input` → `endbot_multiline_input` |

## The marker

The marker is `§e§b§o§t§r` (`MULTILINE_MARKER` in `core/src/endbot_core/forms.py`), formatting codes that draw
nothing. The plugin puts it before the label of a `TextInput(..., multiline=True)`.

- A client without the pack shows the label unchanged and the usual one-line field.
- A client with the pack checks each input's label for the marker (`(#custom_text - '<marker>') = #custom_text`) and
  shows the tall field only for marked inputs.

Marked inputs today: **Edit all at once (UI pack)...** (line editor, behind `[scripting] ui-pack-editor`), the
**New script** template field (also behind `ui-pack-editor`), and **Add a line below** / **New line**. The plugin
already splits a reply on line breaks, so a pasted block keeps its lines. The whole-script and template saves refuse a
reply that lost its line breaks: without the pack the client's field joins every line into one, so a multi-line
script must still have more than one line when it comes back.

## How it works

Read from the vanilla UI in [bedrock-samples](https://github.com/Mojang/bedrock-samples) (`ui/server_form.json`,
`ui/ui_common.json`, `ui/ui_template_buttons.json`, `ui/settings_sections/settings_common.json`), the same in
1.26.50.4 and `v1.26.60.5`:

- `server_form.custom_input` sets **`$max_text_edit_length: 100`** and inherits `common.text_edit_box`, which has no
  `enabled_newline`. That is why a vanilla form input drops line breaks and stops at about 100 characters.
- `common.multiline_text_edit_box` and `common.scrollable_multiline_text_edit_box` set `enabled_newline: true` and
  leave out the `button.menu_ok` mapping, so Enter inserts a line break instead of leaving the field. The book editor
  uses the same mechanism.
- Server forms are built by one factory (`main_screen_content`) whose `long_form` and `custom_form` ids pick the form
  type. The pack redirects those two ids.
- The pack's field, `endbot_multiline_control`, inherits `common.scrollable_multiline_text_edit_box`, sets `size` 150
  and `max_length` from `$max_text_edit_length` (32768), and inherits the content binding (`#custom_input_text` on
  `custom_form`) from vanilla `server_form.custom_input`. It reads `#custom_input_enabled` from the `custom_form`
  collection and binds `#focus_identifier` from `#custom_text`, the pattern vanilla uses for its own multi-line
  inputs. The collection has no dedicated id, so the focus id is the label: two marked inputs on one form need
  distinct labels.
- The scroll panel inside the field consumes pointer input, so the field takes focus only when its frame is clicked.
  The non-scrolling variant takes focus from anywhere but cannot show a long source, so the scrolling one is kept.
- On the server side nothing changes. Endstone passes a modal form reply to the plugin without changing string
  content, and the plugin decodes it with `json.loads`. BDS's own handling of the reply packet is closed source.

## Name and icon

The pack is called **Endbot UI**, and the manifest description says what it changes and that it is optional. The
client's resource list truncates descriptions, so keep it to two short lines.

`pack_icon.png` is a 128×128 export of `assets/icon/endbot-icon.svg`, which the repository README also uses at
512×512. The light variant `assets/icon/endbot-icon-light.svg` is for light backgrounds and has no PNG export yet.
Both are rounded rectangles on a 1024 canvas. `scripts/render_icons.ps1` rasterises the dark SVG once per size with
headless Chrome, so the PNGs are exports of the SVG rather than files to hand-edit.

## Build and validate

```text
python scripts/build_ui_pack.py                               build dist/ui-pack/endbot-ui-<version>.mcpack
python scripts/build_ui_pack.py --out <dir>                   build into another directory
python scripts/build_ui_pack.py --check                       validate only; write nothing
python scripts/build_ui_pack.py --check --vanilla <dir>       also confirm the vanilla elements
```

`<version>` is `header.version` from `manifest.json`: the pack's own version, independent of the Endbot version.
The `.mcpack` is a zip with `manifest.json` at its root, written reproducibly (sorted entries, fixed timestamps and
permissions), so the same input gives the same bytes.

The offline checks, run before every build:

- apart from this README, the directory holds exactly `LICENSE`, `NOTICE`, `manifest.json`, `pack_icon.png` and `ui/server_form.json`;
- every file is strict JSON where it should be, and the manifest is format 2 with valid, distinct UUIDs, three-part
  versions and a single `resources` module (no behavior or script modules);
- `ui/server_form.json` is in the `server_form` namespace, redefines no vanilla element other than
  `main_screen_content`, and every element it inherits from or points at is either its own or in the build script's
  list of vanilla elements (`VANILLA_ELEMENTS`);
- the marker selects exactly the two input variants;
- `pack_icon.png` is a 128×128 PNG.

`--vanilla <dir>` takes a bedrock-samples `resource_pack` directory (fetched by the operator; vanilla UI files may
contain comments) and confirms that every element in `VANILLA_ELEMENTS` exists in that version. The list was last
verified against 1.26.60.5.

`tests/test_ui_pack.py` runs the offline part in CI: the tracked pack is valid, its marker equals the plugin's, the
multi-line control binds the form collection, the build is reproducible and named `endbot-ui-<version>.mcpack`,
broken packs and icons are refused, and the vanilla check handles commented files and reports missing elements.

Whether the client renders and submits the forms as intended is a manual gate (below).

## Release

The release-candidate workflow runs `python scripts/build_ui_pack.py --out dist`, so the `.mcpack` sits beside the
candidate and `SHA256SUMS` covers it. It is published as a release asset only, with no external site
([release/README.md](../release/README.md)). The file name is part of the contract with
[docs/INSTALL.md](../docs/INSTALL.md#optional-ui-pack); the test above pins it.

## Checking the pack after a change

The pack relies on unofficial JSON UI internals, which may change with a client update. Mojang is also moving screens
to Ore UI; if server forms move, the pack stops applying and forms fall back to vanilla. Nothing breaks on the server
side.

After a client update or a pack change:

1. Run `python scripts/build_ui_pack.py --check --vanilla <dir>` against that version's
   `bedrock-samples/resource_pack`.
2. When the pack changes, bump `header.version` and the module version in `manifest.json` (clients cache packs by
   version).
3. Repeat client checks 1–5 below, and check 11 after any change to the name, the description or the icon: the
   resource list is the only place those show.

Client checks, on the locked pair (Endstone `0.11.15+endbot.1` / BDS `1.26.60.6`, protocol 2225), a normally
authenticated Windows client (1.26.60.5) at default GUI scale, 1080p, with the pack active as a global resource:

1. The pack imports and activates without a content-log error. *Fails* → note the error. A changed vanilla element
   name usually shows here.
2. `/bot` and `/botscript` menus open in the larger window. Buttons are compact and their text is not cut off. Other
   forms (Bot actions, Group screens) still work.
3. **Edit lines → Add a line below** shows a tall field. Typing Enter inserts a line break; Save still submits.
4. Paste a 3-line block into **Add a line below** and save. All three lines appear below the line, indented as
   documented.
5. With `ui-pack-editor = true`: **Edit all at once** shows the whole script with line breaks. Change two lines, save,
   and check that **Earlier revisions** has one new revision with exactly those changes.
6. Paste a script longer than 100 characters, and one longer than 2000. Is it kept in full or cut, and where? Without
   the pack, does the line editor keep a line longer than 100 characters when it is saved unchanged? Record both
   answers. A cut there would be an existing line-editor bug.
7. Japanese text and `§` characters in the source survive a round trip through the field.
8. Deactivate the pack: **Edit all at once** on a multi-line script refuses to save ("line breaks were lost") and the
   script is unchanged.
9. Mobile client (optional): steps 3 and 5.
10. Achievement gate with the pack active: as in [docs/ACHIEVEMENTS.md](../docs/ACHIEVEMENTS.md). Until that unlock is
    observed, the pack is not claimed to be achievement-safe.
11. **Settings → Global Resources** lists *Endbot UI*: the name, a two-line description that is not clipped, and the
    abstract-E icon. The list truncates descriptions, so a longer one would show an ellipsis.

Record results in [#277](https://github.com/amatouhake/endbot/issues/277) and the validation status in
[docs/COMPATIBILITY.md](../docs/COMPATIBILITY.md#validation-status). If 3–5 fail because the client does not use the
pack's input element, keep the window and button changes and leave `ui-pack-editor` off.
