# Endbot documentation

Each document is written for one reader. Start with the section that describes you.

## Players: controlling Bots in game and on the web

For anyone the server's operator has granted access. Nothing here needs access to the server machine.
日本語: [プレイヤー向けガイド](ja/README.md).

| Document | What it is |
| --- | --- |
| [Getting started](GETTING_STARTED.md) | Your first Bot in five minutes, and a few things to try |
| [Commands](COMMANDS.md) | `/bot`, `/botgroup`, `/botscript` and `/botweb`: every operation, the menus, and what the replies mean |
| [Groups](GROUPS.md) | Operating many Bots as one target |
| [Scripting tutorial](SCRIPTING_TUTORIAL.md) | From a first script to reacting to events |
| [Scripting reference](SCRIPTING.md) | Authoring, the Luau API, watches, triggers and run lifetime |
| [UI resource pack](UI_PACK.md) | An optional client pack for larger menus and a multi-line editor |

The roles (controller, scripter) and who may use whose Bots are explained in [PERMISSIONS.md](PERMISSIONS.md).
Signing in to the web starts in game with `/botweb link` ([commands](COMMANDS.md#linking-the-web)).

## Operators: running a server

| Document | What it is |
| --- | --- |
| [Install](INSTALL.md) | Installing from a release bundle: a new server or adopting an existing one |
| [Operations](OPERATIONS.md) | Start and stop, granting access, `endbot.toml` settings, update and rollback, `doctor`, directory layout and release files |
| [Scripting setup](SCRIPTING_SETUP.md) | Turning scripting on, its limits and how runs are confined |
| [Deploying the web](WEB_DEPLOY.md) | The Cloudflare Worker, passkeys, the optional trusted site, troubleshooting |
| [Security](SECURITY.md) | The trust boundary and what is enforced where |
| [Compatibility](COMPATIBILITY.md) | The supported Endstone/BDS pair, and what has and has not been checked on real servers |
| [Achievement safety](ACHIEVEMENTS.md) | What is established about achievements, and the manual check |

## Developers: how it is built and why

| Document | What it is |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Processes, boundaries and the authentication flow |
| [Product direction](DIRECTION.md) | The five decisions behind the design, and what comes next |
| [Interface guide](INTERFACE_GUIDE.md) | One vocabulary across commands, forms, Luau and the web; the glossary with Japanese terms |
| [Permission model](PERMISSIONS.md) | Roles, ownership, script declarations and limits |
| [Bot control](BOT_CONTROL.md) | How commands, forms and Group operations reach BDS, and the command declaration |
| [endbotd](ENDBOTD.md) | The process that owns policy and state, and its link to the plugin |
| [Scripting internals](SCRIPTING_INTERNALS.md) | How script runs, arguments, waits and cleanup are implemented, behind the scripting reference |
| [Events, watches and triggers](EVENTS.md) | The event registry, delivery and trigger rules |
| [Event guarantees](EVENT_MEASUREMENTS.md) | What each event kind guarantees and the measurement behind it |
| [The web](WEB.md) | Pairing, accounts, sessions and the relay |
| [Web protocol](WEB_PROTOCOL.md) | The messages between browser, Worker and endbotd |
| [Windows development](WINDOWS_DEV.md) | Building and running the whole stack on one Windows machine, and the live probes |
| [Releasing](../release/README.md) | Release candidates and the release checklist |

The component READMEs cover their own code: [runtime](../runtime/README.md), [runner](../runner/README.md),
[plugin](../plugin/endbot/README.md), [CLI](../cli/endbot/README.md), [core](../core/README.md), the
[UI resource pack](../ui-pack/README.md) and the [Endstone patches](../patches/endstone/README.md).

## How these documents are kept

- They describe the current state. History is in git, and open work is in the [issues](https://github.com/amatouhake/endbot/issues/).
- A statement that something was checked on a real server or client belongs in
  [COMPATIBILITY.md](COMPATIBILITY.md#validation-status), with what was not run beside it.
- Design documents keep their decision numbers (P1, E4, S9, ...) because code comments cite them.
- English is the source. Player documents may have a Japanese translation under [ja/](ja/README.md). Apart from the
  one-line pointers to it and the glossary in [INTERFACE_GUIDE.md](INTERFACE_GUIDE.md#glossary), Japanese prose
  belongs only there. Each translation's first line names its source and the source's git blob
  (`<!-- source: docs/COMMANDS.md <blob> -->`); `python scripts/check_translations.py` lists the translations whose
  source has changed since, with the English diff to carry over. Example replies in a translation are the game's own
  Japanese wording from `core/src/endbot_core/i18n.py`. Japanese paragraphs are not hard-wrapped, because each line
  break renders as a stray space; `--unwrap` joins them.

### Player documents

Player documents are looked up, not read through. They follow these rules:

- **Only what a player needs.** What to type or tap, what happens, what the reply means, the limits and what to do
  when something fails. How it is implemented (packets, transactions, overloads, protocol bounds, stores) belongs in
  a developer document such as [BOT_CONTROL.md](BOT_CONTROL.md); what was tested belongs in
  [COMPATIBILITY.md](COMPATIBILITY.md#validation-status). A player document links there instead.
- **One shape per operation.** A one-sentence description, the syntax in a code block, the options as a list or a
  table, an example, then notes.
- **Short paragraphs.** One topic per paragraph and at most about three sentences. A sequence of items, options,
  states or cases is a list or a table, never a run of sentences.
- **Lead with the common case.** The usual way first; exceptions, edge cases and console-only behavior after it,
  marked as such.

### Operator documents

Operator documents follow the same rules, with these differences:

- **Ordered by the operator's work.** Install, start and stop, grant players access, configure, update, check
  health, then reference material (layout, files, trust details).
- **Only what an operator does or decides.** Why a design was chosen and how a component works inside belong in a
  developer document; the operator document keeps the consequence and links there.
- **Settings as tables.** Each setting with its default, what it changes and when it applies.
- **Records stay records.** COMPATIBILITY.md and ACHIEVEMENTS.md keep every check and its boundary, written as lists
  and tables rather than long paragraphs.
