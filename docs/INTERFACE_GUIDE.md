# Interface guide: one vocabulary, a grammar per surface

This is the standard that chat commands, in-game forms and books, Luau scripts and the web are held to, and the
source of the terms and their Japanese translations. New work follows it; a difference it does not permit is a bug.

Decisions D1 to D4 below are built. Rules 1 to 3 are built in part: the registry holds operations, capabilities, Luau
host requests, event kinds and reserved Bot names, but not yet categories, descriptions or examples, and the web does
not yet show the equivalent command and Luau line ([#286](https://github.com/amatouhake/endbot/issues/286)). See also the [product direction](DIRECTION.md).

## Principle

The surfaces do not look alike and should not try to. They share one **vocabulary** and translate into each other
mechanically:

```text
command  /bot Alice destroy oak_log count 5
Luau     bot("Alice"):destroy("oak_log", {count = 5})
web      [Alice] › Destroy   block: oak_log   count: 5   [Run]
form     Alice › Destroy › (same fields)
```

Translation rule: positional arguments stay positional; a clause (`count 5`) becomes a named option or field with the
same name; a bare mode word (`continuous`) becomes the `mode` option (`{mode = "continuous"}`). Operation names,
parameter names, choice values, units (ticks) and target syntax (`Alice`, `#miners`, `#miners[2]`) are spelled the same
everywhere.

## Rules

1. **One registry.** The language-neutral JSON registry is the source of command help, Luau types and completion,
   web forms and completion, and in-game form fields. It holds operations, event kinds and Luau host APIs. Each entry
   has its category (help page and web tab), description keys, parameter units, ranges and defaults, whether it
   continues (needs `stop` or a wait), the capability it requires ([permissions](PERMISSIONS.md)), and examples.
2. **Examples are written once**, as Operations, and rendered as command, Luau and web text, so docs and help cannot
   drift apart.
3. **Every surface shows the equivalent.** Forms already echo `/bot ...`. The web shows the command and the Luau line
   for each action and offers "copy as script". This is the learning path click → command → script. Recording web or
   form actions into a script draft is a later extension of the same idea.
4. **One result and error catalog.** The same failure reads the same in chat, on the web and in Luau `r.message`;
   scripts branch on `r.code`. Wording comes from the shared message catalog.
5. **No web-only operations.** Anything the web can do exists in the registry first, so commands and Luau keep parity.
   Presentation (map picking, live views, diffs) and editor conveniences may be web-only.
6. **Permitted differences are listed, with reasons.** Any other difference is a bug.
7. **One glossary.** Each concept has one name per language; no synonyms.
8. **Shared information architecture, not shared layout.** In-game menus and the web use the same top level
   (Bots, Groups, Scripts) and the same operation categories as the help pages.

## Permitted differences

| Surface | Difference | Reason |
| --- | --- | --- |
| Chat | Sender-relative `~` and `me` | Typing coordinates is slow; resolved to absolute values at the entry |
| Luau | Handles (`bot("Alice")`), vectors, `wait` family | Programs need values and sequencing |
| Luau | Capabilities beyond operations: watches, events, triggers set up from scripts, future host APIs | Scripts automate and observe over time; each such API declares its capability in the registry ([permissions](PERMISSIONS.md)) |
| Web, forms | Triggers set up without code | A no-code subset of what scripts can do |
| Web, forms | Picking from lists, picking coordinates | Pointing is easier than typing |
| Forms | Quick single taps (for example short moves) stay typed | Form space is limited |

## Glossary

| Term | Japanese | Meaning |
| --- | --- | --- |
| Bot | Bot | One Endbot-controlled player, addressed by its unique name |
| Group | グループ | A named list of Bots, `#name`; `#name[i]` is one member |
| Controller | コントローラー | A person allowed to control Bots, and the base role; every scripter is also a controller |
| Scripter | スクリプター | The role that can also create, edit, copy and share scripts |
| Operator | オペレーター | The console; grants and revokes access |
| Operation | 操作 | One request, such as `destroy` or `tp` |
| Mode | モード | `once`, `continuous`, `stop`; `interval <ticks>` is a clause (`{interval = ticks}`), not a mode |
| Script | スクリプト | Stored Luau source owned by the scripter who created it |
| Run | 実行 | One execution of a script |
| Job | ジョブ | What a Bot keeps doing after an operation returns: continuous or interval input, `for`, held use, mining (one block or many) |
| Event | イベント | Something that happened, of a registry-defined kind such as `block.change`; observation only |
| Watch | 監視 | A run's registered interest in events of one kind and filter, waited on with `w:wait{}` |
| Trigger | トリガー | A persistent binding from an event kind and filter to a script; starts runs; managed under `/botscript` |

*Loop* is an internal word only and does not appear in user-facing text.

## Decisions

### D1. The noun for ongoing Bot work

The grammar is `once`, `continuous`, `interval`, `stop`, with `for`, `hold`, `count`. What a continuing operation
leaves running is a **job (ジョブ)**: anything a Bot keeps doing after the operation returns (see the glossary). *Run*
is reserved for scripts. *Loop* stays an internal word only. A trigger is a persistent binding from an event kind
and filter to a script that starts runs, managed under `/botscript trigger ...`.

### D2. The shape of waits in Luau

- A continuing operation's result carries its job: `local job = b:destroy("stone", {count = 64}).job`, waited with
  `job:wait{timeout = 1200}`. Move, jump, attack, use, interact, destroy and a teleport's arrival are jobs.
  On a Group the job covers every member.
- Watches use the same form: `w:wait{timeout = ...}`.
- State waits stay on the handle: `b:waitOnline{timeout = ...}`.
- Every wait accepts `{timeout = ticks}` and returns the common result shape; a timeout is `ok = false, code =
  "timeout"` and does not stop the job.
- The older `waitOnline(b)` and `b:waitMining{}` remain as documented aliases until 1.0 so stored scripts keep working.

### D3. Command roots

Rule: `/bot` makes Bots do things (single Bots or Groups); `/bot<noun>` manages that noun: `/botgroup` group
definitions, `/botscript` scripts and runs. Triggers are managed under `/botscript trigger ...`.

Existing exceptions, kept: `/bot list`, `/bot help`, `/bot ping` and `/bot menu` are root words. `list`, `ping` and
`menu` win only as the whole command, so a Bot named `list` is still reached with `/bot list status` or `/bot list menu`.
`help` wins with any arguments (`/bot help <page|command>`), and `/bot help status` cannot be told apart from operation
`status` on a Bot called `help`, so no parser rule fixes it. Therefore `help`, `list`, `ping` and `menu` are
reserved Bot names, listed in the registry so every surface applies the same check, and rejected when a Bot is created
or renamed. Existing profiles with those names are kept and pointed at `rename`.

Web account linking does not fit under `/bot`: `web` is a valid Bot name, so `/bot web link` would read as operation
`link` on a Bot called `web` and would break the moment such an operation exists. So the commands are `/botweb link` (issues the
one-time code), `/botweb status` and `/botweb unlink`; see
[Decision 4](DIRECTION.md#decision-4-web-accounts-use-passkeys-and-are-linked-to-an-xuid-once-in-game).

### D4. Owner commands

Players set the visibility of their own items; only the console assigns owners. One `visibility shared|private` verb
spells the same values as the `bots-default` / `groups-default` settings, rather than pairing the verb `share` with
the adjective `private` ([PERMISSIONS.md S9](PERMISSIONS.md#settled-points)), and D3 puts Bot actions under `/bot` and noun management under
`/bot<noun>`:

| What | Command |
| --- | --- |
| Set a Bot's visibility | `/bot <Bot> visibility shared\|private` |
| Set a Group's visibility | `/botgroup visibility #group shared\|private` |
| Set a script's visibility | `/botscript visibility <name> shared\|private` |
| Reassign a Bot's owner (console only) | `/bot <Bot> owner <GamerTag>` |
| Reassign a Group's owner (console only) | `/botgroup owner #group <GamerTag>` |
| Reassign a script's owner (console only) | `/botscript owner <name> <GamerTag>` |

An unowned Bot or Group stays shared and cannot be made private until the console assigns an owner, so a player
cannot claim a pre-ownership item by making it private. The owner may switch visibility; only the console reassigns
owners, and the GamerTag must be an online player or a recorded controller grant.
