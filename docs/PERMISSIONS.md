# Permission model

Status: **Agreed design, not implemented** (2026-10-28 JST). Backlog item BOT-024. It extends the
[product direction](DIRECTION.md); [SECURITY.md](SECURITY.md) describes what is enforced today.

## Today

The console (the operator) can do everything, and only the console grants or revokes access. A controller holds one
permission, `endbot.command.control`, bound to their XUID with a revision that changes on every grant or revoke.
Being a game operator gives no Endbot access. Bots have no owner: any controller may operate any Bot. Groups are shared.
Scripts have an owner and are private or shared. A run uses its invoker's authority. Limits are server-wide (Bot slots)
or per controller (concurrent runs).

## Principles

1. **A permission is about what an action does, never the surface it comes from.** The same action succeeds or fails
   the same way from a command, a form, a script or the web.
2. **The registry declares what each thing needs.** Every operation, event kind and Luau host API states the
   capability it requires. A new Luau API cannot be added without that declaration, so scripting can grow without
   drifting from the permission model.
3. **Defaults keep today's behavior** for existing servers; stricter setups are opt-in.
4. **Few visible concepts.** Users see three roles and a few server limits. Capabilities are internal; extra
   per-person permissions are not offered unless a concrete need appears.

## Two layers: the person and the script

What a run may do is the **intersection** of its invoker's authority and the script's declaration.

Luau can do more than commands: it repeats and waits (amplification over time), observes continuously, reacts to other
players (a trigger fired by someone else's action lets a non-controller steer Bots indirectly), and runs other people's
code. So a script states the capabilities it uses beyond a basic set, next to `params`:

```lua
requires { "observe.chat", "trigger.arm" }
params { ... }
```

- The basic set needs no declaration: operating Bots the invoker may operate, reading the world, waiting and logging.
  Beginner scripts stay as they are.
- Declarations are read before a run starts, through the existing inspection that executes only up to `params`.
  Forms and the web show them before a shared script runs ("this script observes chat").
- A missing capability fails before the run starts, with the capability named, never halfway through.

| Class | Examples | Governed by |
| --- | --- | --- |
| Act | Operate Bots | Roles and ownership |
| Observe | Public chat, joins and leaves | Data class (below); declaration in scripts |
| Amplify and persist | Armed triggers, storage, long runs | Limits; declaration in scripts |
| External | Network access | Not planned; operator-only if ever added |

## Roles

| Role | Can |
| --- | --- |
| operator | The console. Everything, including granting and revoking access and reassigning owners |
| scripter | Everything a controller can, plus create, edit, copy and share scripts |
| controller | Commands and menus, Groups, running shared and file scripts, setting up triggers from scripts they may run, reading the source of shared scripts |

Authoring is separate from running because a scripter decides what gets automated and at what scale, publishes code that
others run, and spends server resources freely. A controller automates only with scripts someone published, such as
the operator's file scripts. Running is the same for both roles: the intersection above applies.

- Assignment: `endbot controllers add <GamerTag> --role scripter|controller`.
- Migration: controllers granted before this model become **scripter**, which keeps today's behavior.
- A new grant without `--role` is **controller** (least privilege). Release notes and `endbot controllers list` show
  each person's role.
- Delegating administration stays console-only. A web-usable `admin` capability, granted only from the console, may be
  added if operators need it.

## Ownership

Bots, Groups, scripts and triggers share one model: the creator is the owner, and each is `private` or `shared`.

- Defaults keep today's behavior: Bots and Groups are shared; scripts are private. A server setting (for example
  `[permissions] bots-default = private`) changes the default.
- Owners switch visibility per item, as `share` and `private` do for scripts today.
- The console can operate everything and reassign owners.
- Consistency follows from principle 1: running someone's shared script against your own private Bot works; running
  it against another person's private Bot fails exactly as the command would.

## Observation

The line is what an ordinary player in game can see.

| Data | Examples | Treatment |
| --- | --- | --- |
| Public in game | Join and leave messages, public chat, death messages | Available to controllers; scripts declare it |
| Not normally visible | Other players' positions and inventories | Not provided. Revisit only with a concrete need |
| Private | `/tell` and other direct messages | Never provided |

## Triggers

- Controllers and scripters may arm triggers. A per-controller trigger limit bounds them; `0` disables triggers on a
  server.
- A trigger is bound to the script revision its owner approved. If an edit grows the script's declaration, the trigger
  pauses until its owner approves again, so editing a shared script cannot widen what other people's triggers do.
- A trigger runs with its owner's authority intersected with that approved declaration.

## Limits

Per controller: concurrent runs (exists today), armed triggers, watches and Bots. Server-wide: Bot slots and the
reserve for players (exists today). Limits are the answer to amplification, not extra permissions.

## Revocation and role changes

| Item | On revocation |
| --- | --- |
| Running scripts | End at once |
| Triggers | Disabled, not deleted; a regrant can re-enable them |
| Web sessions | End at once |
| Jobs the person started | Stop, as continuous mining already does |
| Owned Bots, Groups and scripts | Kept; shared ones stay usable; the console can reassign owners |

Moving a scripter to controller keeps their scripts, which stay runnable but no longer editable by them.

## Not decided here

Storage, calls between scripts and any other new Luau capability get their class and capability name when they are
designed, under principle 2.
