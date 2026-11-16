# Optional Endbot UI resource pack

An optional resource pack, *Endbot UI*, that players install on their own client. It makes the in-game menus larger
and gives the script editor a multi-line field. Without it everything still works except whole-script editing in one
field; the server feature that relies on it, `[scripting] ui-pack-editor`, is off by default. The field on a mobile
client and a fresh achievement unlock with the pack active are not verified ([#277](https://github.com/amatouhake/endbot/issues/277)).

## Why

Two client limits make in-game script editing hard:

- Server forms are a fixed 225×200 UI-unit window with 32-unit buttons, so menus scroll a lot.
- The form text field is short and **drops line breaks**, so multi-line source has to go through a book and quill.

Neither is fixed by anything a server can put in a form. A resource pack can restyle the form screens on the client.
This pack is the in-game stopgap. The [web editor](WEB_DEPLOY.md) stays the
long-term authoring path: it is the only one that can give syntax highlighting, completion and diffs.

## What it changes

`ui-pack/` overrides one vanilla element, `server_form.main_screen_content`, so that server forms use `endbot_*`
elements built on the vanilla ones:

| Change | Applies to | How |
|---|---|---|
| Larger window: screen size minus a margin, at most 420×300 (vanilla 225×200) | every server form | `endbot_long_form`, `endbot_custom_form` |
| Compact buttons: 24 units high (vanilla 32), 24-unit icons | every action form | `endbot_dynamic_button` |
| Multi-line field: 150 units high, scrolls, Enter inserts a line break, up to 32768 characters | only inputs whose label starts with the Endbot marker | `endbot_input` → `endbot_multiline_input` |

The **marker** is `§e§b§o§t§r` (`forms.MULTILINE_MARKER`), formatting codes that draw nothing. The plugin puts it
before the label of a `TextInput(..., multiline=True)`. A client without the pack shows the label unchanged and the
usual one-line field. A client with the pack checks each input's label for the marker
(`(#custom_text - '<marker>') = #custom_text`) and shows the tall field only for marked inputs.

Marked inputs today:

- **Edit all at once (UI pack)...**: the whole source in one field, shown in the line editor when the server enables
  `[scripting] ui-pack-editor`. It saves through the same compile, declaration, owner and revision checks as every
  other save. A reply that lost its line breaks is **refused, never saved**: without the pack the client's field joins
  every line into one, so a multi-line script must still have more than one line when it comes back. An unchanged
  reply goes back to the editor without saving a revision.
- **New script** (the template field): with `ui-pack-editor` the new-script screen offers the template as the same
  marked field, so a script can be written as a whole before it exists. It uses the same refusal when a reply lost its
  line breaks, and a one-line template may stay one line. The template stays readable as a label above the field, so a
  client without the pack can still see what it saves. Its **Save as shown (without the UI pack)** toggle saves the
  template unchanged, ignoring the field, so a player without the pack can still create the script and edit its
  lines afterwards (the client's field cannot be cleared).
- **Add a line below** (line editor) and **New line** (add at top/end). Without the pack they work as before; with it
  a pasted block keeps its lines (the plugin already splits a reply on line breaks).

The pack changes the look of **all** server forms while it is installed, including other servers' forms (larger
window, smaller buttons). The tall field appears only for Endbot's marked inputs.

## Install

The pack is client-side only. Add it as a **global resource pack** on the player's device. The server's pack stack
stays empty: no `world_resource_packs.json` and no `texturepacks-required`. Nothing in `server.properties` changes.

1. Download `endbot-ui-<pack version>.mcpack` from the GitHub release. The release workflow builds it beside the
   candidate and `SHA256SUMS` covers it; it ships as a release asset only, with no external site. Its version is the
   pack's own, so it differs from the Endbot version and only moves when the pack changes. Serving the same file as a
   server resource pack instead is unverified: it puts the pack in the server's pack stack, which no check covered.
2. On Windows, open the `.mcpack` file. Minecraft imports it.
3. **Settings → Global Resources**: activate *Endbot UI*.
4. The operator sets `ui-pack-editor = true` under `[scripting]` (plugin `config.toml`, or `endbot.toml` for managed
   instances) and restarts. Players without the pack still see the button; they are told to use the line buttons or a
   book if they try it. On the new-script screen they are told to turn on **Save as shown** and edit lines after
   saving.

Mobile clients can import `.mcpack` files the same way. Consoles cannot add arbitrary global packs, so this pack is
effectively for PC and mobile players. To build the file from a checkout instead, `python scripts/build_ui_pack.py`
writes `dist/ui-pack/endbot-ui-<version>.mcpack` (reproducible: same input, same bytes).

## Name, icon and licence

The pack is called **Endbot UI**, and the manifest description says what it changes and that it is optional.
`ui-pack/pack_icon.png` is the 128x128 icon the client shows; its source is `assets/icon/endbot-icon.svg`, which the
README also uses at 512x512. The light variant `assets/icon/endbot-icon-light.svg` is for light backgrounds and has
no PNG export yet. Both are rounded rectangles on a 1024 canvas. `scripts/render_icons.ps1` rasterises the dark SVG
once per size with headless Chrome, so the PNGs are exports of the SVG rather than files to hand-edit.

The pack ships `LICENSE` (Apache-2.0) and a short `NOTICE` inside the zip: players can download it on its own, and the
NOTICE names the copyright holder and states that the pack overrides vanilla element ids at runtime and bundles no
Mojang assets.

To remove it, deactivate it under Global Resources. No server change is needed. Leave `ui-pack-editor` off when no
controller uses the pack.

## Achievement safety

The [achievement gate](ACHIEVEMENTS.md) was observed with an empty pack stack and no global packs. This pack does not
touch the server or the world. It is a resource-only pack: no behavior pack, scripts, experiments or Beta APIs.
Resource packs are not known to disable achievements. Still, **a world achievement unlock with the pack active is a
manual gate of its own** (AGENTS.md, check 10). That gate has not been passed ([#277](https://github.com/amatouhake/endbot/issues/277)):
until the unlock is observed, the pack is not claimed to be achievement-safe.

## How it works

Read from the vanilla UI in [bedrock-samples](https://github.com/Mojang/bedrock-samples) (`ui/server_form.json`,
`ui/ui_common.json`, `ui/ui_template_buttons.json`, `ui/settings_sections/settings_common.json`), the same in
1.26.50.4 and `v1.26.60.5`:

- `server_form.custom_input` sets **`$max_text_edit_length: 100`** and inherits `common.text_edit_box`, which has
  no `enabled_newline`. That is why a vanilla form input drops line breaks and stops at about 100 characters.
- `common.multiline_text_edit_box` and `common.scrollable_multiline_text_edit_box` set `enabled_newline: true` and
  leave out the `button.menu_ok` mapping, so Enter inserts a line break instead of leaving the field. The book
  editor uses the same mechanism.
- Server forms are built by one factory (`main_screen_content`) whose `long_form` and `custom_form` ids pick the
  form type. The pack redirects those two ids.
- The pack's field, `endbot_multiline_control`, inherits `common.scrollable_multiline_text_edit_box`, sets `size` 150
  and `max_length` from `$max_text_edit_length` (32768), and inherits the content binding (`#custom_input_text` on
  `custom_form`) from vanilla `server_form.custom_input`. It reads `#custom_input_enabled` from the `custom_form`
  collection and binds `#focus_identifier` from `#custom_text`, the pattern vanilla uses for its own multi-line
  inputs. The collection has no dedicated id, so the focus id is the label: two marked inputs on one form need
  distinct labels.
- On the server side nothing changes. Endstone passes a modal form reply to the plugin without changing string
  content, and the plugin decodes it with `json.loads`. BDS's own handling of the reply packet is closed source.

## Behaviour on the client

Seen on a Windows client 1.26.60.5 with the pack active as a global resource, default GUI scale, 1080p
([record](https://github.com/amatouhake/endbot/blob/cad9d962b49dedabb60d6dd37641c67d2e31df84/docs/UI_PACK.md#client-check-2026-11-03-jst-windows-126605)):

- `/bot` and `/botscript` open in the larger window; buttons are compact and no label is clipped.
- A marked input shows the tall field. Typing, backspace and Enter work, and saving keeps the line breaks, Japanese
  text, `§` codes, tabs and trailing spaces. Lines of 152 and 2603 characters and pastes of 156 and 2508 characters
  were kept in full.
- **The field takes focus only when its frame is clicked.** The scroll panel inside it consumes pointer input, so a
  click in the text shows no caret. Click the border once; then the whole field edits, scrolls with the wheel and
  follows the caret. The non-scrolling variant takes focus from anywhere but cannot show a long source, so the
  scrolling one is kept.
- **Without the pack the whole-script entry is unusable.** The field shows only the first lines of the script, typing
  or deleting does nothing, and input stops at about 100 characters. The entry is therefore usable only by players
  who install the pack, and `ui-pack-editor` is a switch for an operator whose players do.
- In **Settings → Global Resources** the list shows *Endbot UI* with a two-line description and the abstract-E icon.

Not verified ([#277](https://github.com/amatouhake/endbot/issues/277)): the field on a mobile client, and a fresh achievement unlock with the pack active.

## Checking the pack after a change

On the locked pair (Endstone `0.11.15+endbot.1` / BDS `1.26.60.6`, protocol 2225), a normally authenticated Windows
client (1.26.60.5) at default GUI scale, 1080p, with the pack active as a global resource:

1. The pack imports and activates without a content-log error. *Fails* → note the error. A changed vanilla element
   name usually shows here.
2. `/bot` and `/botscript` menus open in the larger window. Buttons are compact and their text is not cut off. Other
   forms (Bot actions, Group screens) still work.
3. **Edit lines → Add a line below** shows a tall field. Typing Enter inserts a line break; Save still submits.
4. Paste a 3-line block into **Add a line below** and save. All three lines appear below the line, indented as
   documented.
5. With `ui-pack-editor = true`: **Edit all at once** shows the whole script with line breaks. Change two lines, save,
   and check that **Earlier revisions** has one new revision with exactly those changes.
6. Paste a script longer than 100 characters, and one longer than 2000. Is it kept in full or cut, and where?
   Without the pack, does the line editor keep a line longer than 100 characters when it is saved unchanged? Record
   both answers. A cut there would be an existing line-editor bug.
7. Japanese text and `§` characters in the source survive a round trip through the field.
8. Deactivate the pack: **Edit all at once** on a multi-line script refuses to save ("line breaks were lost") and the
   script is unchanged.
9. Mobile client (optional): steps 3 and 5.
10. Achievement gate with the pack active: as in [ACHIEVEMENTS.md](ACHIEVEMENTS.md).
11. **Settings → Global Resources** lists *Endbot UI*: the name, a two-line description that is not clipped, and the
    abstract-E icon. The list truncates descriptions, so a longer one would show an ellipsis.

Record results in [#277](https://github.com/amatouhake/endbot/issues/277). If 3–5
fail because the client does not use the pack's input element, keep the window and button changes and leave
`ui-pack-editor` off.

The pack relies on unofficial JSON UI internals, which may change with a client update. Mojang is also moving
screens to Ore UI; if server forms move, the pack stops applying and forms fall back to vanilla. Nothing breaks on the
server side. After a client update, run `python scripts/build_ui_pack.py --check --vanilla <dir>` against that
version's `bedrock-samples/resource_pack`, bump `header.version` and the module version in `ui-pack/manifest.json`
when the pack changes (clients cache packs by version), and repeat checks 1–5. Repeat check 11 as well after any change
to the name, the description or the icon: the resource list is the only place those show. `tests/test_ui_pack.py`
runs the offline part of the validation in CI, including that the pack's marker equals the plugin's.
