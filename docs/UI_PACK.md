# Optional Endbot UI resource pack

*Endbot UI* is an optional resource pack you install on your own device. It makes the in-game menus larger and gives
the script editor a multi-line field. Without it, everything in Endbot works except editing a whole script in one
field.

## What it changes

| Change | Where |
| --- | --- |
| A larger form window: your screen minus a margin, at most 420×300 UI units (normally 225×200) | Every server form |
| Compact buttons: 24 units high (normally 32), so menus scroll less | Every menu with buttons |
| A tall multi-line field: Enter inserts a line break, the field scrolls, up to 32768 characters | Only Endbot's script fields listed below |

The larger window and compact buttons apply to **every** server's forms while the pack is active, not only Endbot's.

Script fields that become multi-line:

| Field | With the pack |
| --- | --- |
| **Edit all at once (UI pack)...** in the line editor | The whole script in one field. Shown only when the operator turned on `ui-pack-editor`. |
| **New script** template field | Write the whole script before it exists. Also needs `ui-pack-editor`. |
| **Add a line below** and **New line** (add at top or end) | A pasted block keeps its lines. |

Saving from a multi-line field goes through the same checks as every other save. A reply that comes back unchanged
returns to the editor without saving a new revision.

## Install

The pack goes on your device only. The server needs no pack.

1. Download `endbot-ui-<pack version>.mcpack` from the [GitHub release](https://github.com/amatouhake/endbot/releases).
   It is attached to each release; there is no other download site. Its version is the pack's own and differs from
   the Endbot version.
2. On Windows, open the `.mcpack` file. Minecraft imports it.
3. In **Settings → Global Resources**, activate *Endbot UI*. It is listed with a two-line description and an
   abstract-E icon.
4. For **Edit all at once** and the multi-line **New script** field, ask the operator to turn on
   `ui-pack-editor` (see [For operators](#for-operators)).

Mobile clients can import the `.mcpack` file the same way. Consoles cannot add your own global packs, so the pack is
effectively for PC and mobile players.

## Using the multi-line field

- **Click the field's frame once to start typing.** A click on the text itself shows no caret. After that the whole
  field edits, scrolls with the mouse wheel and follows the caret.
- Line breaks, Japanese text, `§` codes, tabs and trailing spaces are kept when you save.

## Without the pack

| Feature | Without the pack |
| --- | --- |
| Menus | Normal size |
| **Add a line below**, **New line** | Work as before, one line at a time |
| **Edit all at once** | Unusable: the field shows only the first lines, typing does nothing and input stops at about 100 characters. Use the line buttons or a book and quill. |
| **New script** | The template is shown as a label above the field. Turn on **Save as shown (without the UI pack)** to save it unchanged (the field cannot be cleared), then edit its lines after saving. |

If you try **Edit all at once** without the pack, saving a multi-line script is **refused, never saved**, with a message
that the line breaks were lost. The script stays unchanged. This applies to the **New script** field too; a one-line script or template may stay one line.

The [web editor](WEB_DEPLOY.md) works without the pack and is the full authoring tool, with syntax highlighting,
completion and diffs.

## Remove it

Deactivate *Endbot UI* in **Settings → Global Resources**. Nothing changes on the server.

## For operators

Turn the editor on under `[scripting]` in the plugin `config.toml`, or in `endbot.toml` for managed instances, then
restart:

```toml
[scripting]
ui-pack-editor = true
```

- It is off by default. Turn it on only if your players install the pack; leave it off when no controller uses it.
- Players without the pack still see **Edit all at once**. If they try it, they are told to use the line buttons or
  a book. On the new-script screen they are told to turn on **Save as shown** and edit lines after saving.
- Keep the server's pack stack empty: no `world_resource_packs.json`, no `texturepacks-required`, nothing in
  `server.properties`. Serving the same file as a server resource pack instead is unverified.

## Known limits

- **Achievements:** the pack is resource-only and does not touch the server or the world, and resource packs are not
  known to disable achievements. But an achievement unlock with the pack active has not been observed yet, so the
  pack is not claimed to be achievement-safe ([#277](https://github.com/amatouhake/endbot/issues/277),
  [ACHIEVEMENTS.md](ACHIEVEMENTS.md)).
- **Mobile:** the multi-line field has not been tried on a mobile client
  ([#277](https://github.com/amatouhake/endbot/issues/277)).
- **Client updates:** the pack relies on unofficial client UI internals. After a Minecraft update it may stop
  applying, and forms then look normal again. Nothing breaks on the server.
- **Other servers:** while the pack is active, other servers' forms also get the larger window and smaller buttons.

What was checked on a real client: [validation status](COMPATIBILITY.md#validation-status). How the pack works and is
built: [ui-pack/README.md](../ui-pack/README.md).
