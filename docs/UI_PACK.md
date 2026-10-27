# Optional Endbot UI resource pack

Status: **prototype, not yet checked on a real client** (2026-10-25 JST). Everything below that depends on how the
Bedrock client renders or submits a form is an expectation, not an observation, until the
[manual check](#manual-client-check) passes. The server feature that relies on it, `[scripting] ui-pack-editor`, is
off by default.

## Why

Two client limits make in-game script editing hard (BOT-009, 2026-10-24/25 Windows checks):

- Server forms are a fixed 225×200 UI-unit window with 32-unit buttons, so menus scroll a lot (BOT-006).
- The form text field is short and **drops line breaks**, so multi-line source has to go through a book and quill.

Neither is fixed by anything a server can put in a form. A resource pack can restyle the form screens on the client.
This pack is the in-game stopgap. [BOT-013](BACKLOG.md#bot-013-web-script-editor), the web editor, stays the
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
- **Add a line below** (line editor) and **New line** (add at top/end). Without the pack they work as before; with it
  a pasted block keeps its lines (the plugin already splits a reply on line breaks).

The pack changes the look of **all** server forms while it is installed, including other servers' forms (larger
window, smaller buttons). The tall field appears only for Endbot's marked inputs.

## Install

The pack is client-side only. Add it as a **global resource pack** on the player's device. The server's pack stack
stays empty: no `world_resource_packs.json` and no `texturepacks-required`. Nothing in `server.properties` changes.

1. Build it from a checkout: `python scripts/build_ui_pack.py` writes `dist/ui-pack/endbot-ui-<version>.mcpack`
   (reproducible: same input, same bytes).
2. On Windows, open the `.mcpack` file. Minecraft imports it.
3. **Settings → Global Resources**: activate *Endbot UI (optional)*.
4. The operator sets `ui-pack-editor = true` under `[scripting]` (plugin `config.toml`, or `endbot.toml` for managed
   instances) and restarts. Players without the pack still see the button; they are told to use the line buttons or a
   book if they try it.

Mobile clients can import `.mcpack` files the same way. Consoles cannot add arbitrary global packs, so this pack is
effectively for PC and mobile players.

To remove it, deactivate it under Global Resources. No server change is needed. Leave `ui-pack-editor` off when no
controller uses the pack.

## Achievement safety

The [achievement gate](ACHIEVEMENTS.md) was observed with an empty pack stack and no global packs. This pack does not
touch the server or the world. It is a resource-only pack: no behavior pack, scripts, experiments or Beta APIs.
Resource packs are not known to disable achievements. Still, **a world achievement unlock with the pack active is a
manual gate of its own** (AGENTS.md). Until that unlock is observed, the pack is not claimed to be achievement-safe.

## What was checked in the cloud (2026-10-25 JST)

No Minecraft client or BDS could run there: `www.minecraft.net` is outside that environment's network policy. These
checks were done:

- **Vanilla UI read**: [bedrock-samples](https://github.com/Mojang/bedrock-samples) `main`, version **1.26.50.4**.
  Findings:
  - `server_form.custom_input` sets **`$max_text_edit_length: 100`** and inherits `common.text_edit_box`, which has
    no `enabled_newline`. That explains the dropped line breaks. It may also mean the client keeps only 100
    characters of a typed or pasted input (see check 6).
  - `common.multiline_text_edit_box` and `common.scrollable_multiline_text_edit_box` set `enabled_newline: true` and
    leave out the `button.menu_ok` mapping, so Enter inserts a line break instead of leaving the field. The book
    editor uses the same mechanism.
  - Server forms are built by one factory (`main_screen_content`) whose `long_form` and `custom_form` ids pick the
    form type. The pack redirects those two ids.
- **Pack validation**: `scripts/build_ui_pack.py --check --vanilla <bedrock-samples>/resource_pack` confirmed that
  all 23 vanilla elements the pack inherits from or names exist in 1.26.50.4, that the pack's own references
  resolve, and that the manifest is a single resources module. `tests/test_ui_pack.py` runs the offline part in CI,
  including that the pack's marker equals the plugin's.
- **Server path read**: locked Endstone `v0.11.13` (`3491c609`) passes a modal form reply from BDS's `Json::Value`
  to the plugin with `asString()` and `nlohmann::json::dump()` (`src/endstone/core/json.h`,
  `EndstonePlayer::onFormResponse`). Neither changes string content, and the plugin decodes it with `json.loads`.
  The Endbot patches do not touch forms. BDS's own handling of `ModalFormResponsePacket` is closed source and
  unverified.
- **Plugin**: unit tests cover the marker on labels, the refusal of a reply without line breaks, unchanged replies,
  trailing blank lines and CRLF, and the setting in the plugin and the CLI.

## Manual client check

On the locked pair (Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`), a normally authenticated Windows client
(1.26.5x) at default GUI scale, 1080p, with the pack active as a global resource:

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

Record results in [BACKLOG.md](BACKLOG.md#bot-018-optional-ui-resource-pack-for-forms-and-multi-line-editing). If 3–5
fail because the client does not use the pack's input element, keep the window and button changes and leave
`ui-pack-editor` off.

## Maintenance

The pack relies on unofficial JSON UI internals, which may change with a client update. Mojang is also moving
screens to Ore UI; if server forms move, the pack stops applying and forms fall back to vanilla. Nothing breaks on the
server side. After a client update, run `python scripts/build_ui_pack.py --check --vanilla <dir>` against that
version's `bedrock-samples/resource_pack`, bump `header.version` and the module version in `ui-pack/manifest.json`
when the pack changes (clients cache packs by version), and repeat checks 1–5.
