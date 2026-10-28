# Interface guide: one vocabulary, a grammar per surface

Status: **Agreed** (2026-10-28 JST); not yet implemented. Applies to chat commands,
in-game forms and books, Luau scripts and the web. The technical basis is the Operation API (BOT-019); see also the
[product direction](DIRECTION.md).

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
same name; a bare mode word (`continuous`) becomes the `mode` option (`{mode = "continuous"}`). Operation names, parameter names, choice values, units (ticks) and target syntax (`Alice`, `#miners`,
`#miners[2]`) are spelled the same everywhere.

## Rules

1. **One registry.** The language-neutral JSON registry is the source of command help, Luau types and completion,
   web forms and completion, and in-game form fields. It holds each operation's category (help page and web tab),
   description keys, parameter units, ranges and defaults, whether it continues (needs `stop` or a wait), and
   examples.
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
| Web, forms | Picking from lists, picking coordinates | Pointing is easier than typing |
| Forms | Quick single taps (for example short moves) stay typed | Form space is limited |

## Glossary

| Term | Japanese | Meaning |
| --- | --- | --- |
| Bot | Bot | One Endbot-controlled player, addressed by its unique name |
| Group | グループ | A named list of Bots, `#name`; `#name[i]` is one member |
| Controller | コントローラー | A person allowed to control Bots |
| Operation | 操作 | One request, such as `destroy` or `tp` |
| Mode | モード | `once`, `continuous`, `interval`, `stop` |
| Script | スクリプト | Stored Luau source owned by a controller |
| Run | 実行 | One execution of a script |
| Job | ジョブ | What a Bot keeps doing after an operation returns: continuous or interval input, `for`, held use, mining (one block or many) |
| Watch | 監視 | A registered interest in a world change that a run can wait on |
| Trigger | トリガー | (BOT-022) A watch that survives restarts and starts a run; managed under `/botscript` |

*Loop* is an internal word only and does not appear in user-facing text.

## Decisions

### D1. The noun for ongoing Bot work

The grammar is already consistent (`once`, `continuous`, `interval`, `stop`, with `for`, `hold`, `count`). The nouns
for what a continuing operation leaves running are not: docs say mining *job*, *loop* and, once, a finished mining
*run*. Decision: **job (ジョブ)** for anything a Bot keeps doing after the operation returns (continuous or interval
input, `for`, mining with `count`). *Run* is reserved for scripts. *Loop* stays an internal word only. Trigger
(BOT-022) is added when it exists: a persistent watch that starts a run.

### D2. The shape of waits in Luau

Today `waitOnline(b)` is a global function and `b:waitMining{timeout = ...}` is a method. Decision:

- A continuing operation's result carries its job: `local job = b:destroy("stone", {count = 64}).job`, waited with
  `job:wait{timeout = 1200}`. This generalizes `waitMining` and is how BOT-016 adds waits for move, use and attack.
  On a Group the job covers every member, like `group:waitMining` does today.
- Watches use the same form: `w:wait{timeout = ...}`.
- State waits stay on the handle: `b:waitOnline{timeout = ...}`.
- Every wait accepts `{timeout = ticks}` and returns the common result shape; a timeout is `ok = false, code =
  "timeout"` and does not stop the job.
- `waitOnline(b)` and `b:waitMining{}` remain as documented aliases until 1.0 so stored scripts keep working.

### D3. Command roots

Rule: `/bot` makes Bots do things (single Bots or Groups); `/bot<noun>` manages that noun: `/botgroup` group
definitions, `/botscript` scripts and runs. Triggers will be managed under `/botscript`.

Existing exceptions, kept: `/bot list`, `/bot help`, `/bot ping` and `/bot menu` are root words. They win only as the
whole command, so a Bot named `list` is still reached with `/bot list status` or `/bot list menu`.

Web account linking does not fit under `/bot`: `web` is a valid Bot name, so `/bot web link` would read as operation
`link` on a Bot called `web` and would break the moment such an operation exists. Decision: `/botweb link` (issues the one-time code), `/botweb status` and `/botweb unlink`; see
[Decision 4](DIRECTION.md#decision-4-web-accounts-use-passkeys-and-are-linked-to-an-xuid-once-in-game).
