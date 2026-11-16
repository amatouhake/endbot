# Permission model

Who may do what with Bots, Groups, scripts and triggers. The model has shipped since 0.5.0; triggers and observation
joined it in 0.7.0. [SECURITY.md](SECURITY.md) describes the enforcement boundary, and
[COMPATIBILITY.md](COMPATIBILITY.md#permissions-and-endbotd) what was checked on a real server. Code comments cite
the settled points below by number (S1 to S12).

The console (the operator) can do everything, and only the console grants or revokes access. A grant is bound to a
player's XUID with a revision that changes on every grant or revoke. Being a game operator gives no Endbot access.

## Principles

1. **A permission is about what an action does, never the surface it comes from.** The same action succeeds or fails
   the same way from a command, a form, a script or the web.
2. **The registry declares what each thing needs.** Every operation, event kind and Luau host API states the
   capability it requires. A new Luau API cannot be added without that declaration, so scripting can grow without
   drifting from the permission model.
3. **Defaults keep the behavior servers had before the model** (everything shared, everyone a scripter); stricter
   setups are opt-in.
4. **Few visible concepts.** Users see three roles and a few server limits. Capabilities are internal; extra
   per-person permissions are not offered unless a concrete need appears.

## Two layers: the person and the script

What a run may do is the **intersection** of its invoker's authority and the script's declaration.

Luau can do more than commands: it repeats and waits (amplification over time), observes continuously, reacts to other
players (a trigger fired by someone else's action lets a non-controller steer Bots indirectly), and runs other people's
code. So a script states the capabilities it uses beyond a basic set, next to `params`:

```lua
requires { "observe.chat" }
params { ... }
```

- The basic set needs no declaration: operating Bots the invoker may operate, reading the world, waiting and logging.
  Beginner scripts stay as they are.
- `requires {}` must be an unconditional top-level call before `params{}`; a later or conditional call is an
  inspection error, so what inspection reads is the whole declaration.
- The basic names are `act.bot`, `read.world`, `wait` and `log`. They are accepted in a declaration and ignored. The
  other names come from event kinds (`observe.chat`, `observe.presence`); any name outside those is an
  unknown-capability error (S8). A declaration is a set: order does not matter, and a duplicate is an error.
- Declarations are read before a run starts, through the inspection that executes only up to `params`.
  Forms and the web show them before a shared script runs ("this script observes chat").
- A missing capability fails before the run starts, with the capability named, never halfway through.
- A run started from an inspection (the run form, a menu start, a trigger) carries the inspected declaration, and
  stops before its first operation if its own `requires {}` differs (S3). A typed `/botscript run` is held to its own
  declaration, which cannot exceed its invoker's authority either.

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

- Assignment: `endbot controllers add <GamerTag> --role scripter|controller`; the same option on an existing grant
  changes the role.
- Roles are stored in `state/controller-roles.json`, keyed by XUID (S1). A grant with no entry is **scripter**, so
  controllers granted before 0.5.0 keep authoring. A pending GamerTag's role is kept by name until its first
  authenticated join.
- The role is read at each action, so a change applies on the next command without a rejoin (S5).
- A new grant without `--role` is **controller** (least privilege). Release notes and `endbot controllers list` show
  each person's role.
- Delegating administration stays console-only. A web-usable `admin` capability, granted only from the console, may be
  added if operators need it.

## Ownership

Bots, Groups, scripts and triggers share one model: the creator is the owner, and each is `private` or `shared`.

- By default Bots and Groups are shared and scripts are private. `[permissions] bots-default` and `groups-default`
  change the default for new Bots and Groups (S7).
- Owners switch visibility per item with `visibility shared|private`.
- The console can operate everything and reassign owners.
- Bots and Groups made before 0.5.0 or from the console have no owner and stay shared, like scripts saved before
  ownership; the console can assign owners. A Bot created by a run or trigger is owned by the run's invoker or the
  trigger's owner.
- Ownership is checked once, before the runtime, for every named Bot, and per member for a Group (S4, S11).
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
- A trigger's owner approves the script's declaration when arming it. Editing a script can never widen what a trigger
  does: a trigger on someone else's script is pinned to the approved revision and pauses when the script changes,
  and a trigger on one's own script follows the current revision only while its declaration stays within the approved
  one. The exact rules are in [EVENTS.md](EVENTS.md#settled-points) (S16, S17).
- A trigger runs with its owner's authority intersected with that approved declaration.

## Limits

Per controller: concurrent runs, armed triggers, watches, watched blocks and Bots (`[limits] max-bots-per-controller`,
`0` meaning no limit). Server-wide: Bot slots and the reserve for players. Limits are the answer to amplification,
not extra permissions. The settings are in [OPERATIONS.md](OPERATIONS.md#settings).

## Revocation and role changes

| Item | On revocation |
| --- | --- |
| Running scripts | End at once |
| Triggers | Disabled, not deleted; a regrant does not re-enable them by itself, but afterwards the owner (or the console) can enable them again |
| Web sessions | End at once |
| Jobs the person started | Stop |
| Owned Bots, Groups and scripts | Kept; shared ones stay usable; the console can reassign owners |

Moving a scripter to controller keeps their scripts. An owner may always run their own scripts, private ones
included, so they stay runnable by them but are no longer editable by them.

## Settled points

- **S1. The role lives in** a separate `state/controller-roles.json`, not a version 2
  `controller-policy.json`, for the reason [ENDBOTD.md P4](ENDBOTD.md#p4-authorization-state-lives-in-endbotd-presence-gives-the-session-revision)
  gives: a pre-0.5.0 loader rejects an unknown version and unknown grant fields, so a v2 policy breaks rollback, while
  an unknown file is ignored. After a rollback to such a version everyone acts as a scripter.
- **S2. Bot and Group ownership lives in** a separate store, not new fields in runtime profiles (strict
  `schemaVersion`) or `groups.json`. The consequence: after a rollback to a version before 0.5.0, private Bots and
  Groups are shared again.
- **S3. "Unconditional top-level" is enforced** at run time, not by parsing source: `requires {}` must
  come before `params{}`, once, and the run's declaration must equal the inspected one, so a conditional call cannot
  widen a run.
- **S4. Someone else's private items.** Bots are visible in game anyway, so they stay listed and are refused
  with the authorization code; private Groups are not visible in game, so they are left out of other people's lists
  and resolve as not found.
- **S5. Role changes during a run.** The role is read at each authoring action; a role change does not bump
  the grant revision, so runs and jobs continue (running is the same for both roles).
- **S6. Authoring** is create, save, paste or book import, copy, rename, restore and `visibility shared`.
  A controller may still run, make private (`visibility private`) and delete their own scripts, which only narrows
  what others can use.
- **S7. Setting names** are `[permissions] bots-default` and `groups-default`, each `shared` (default) or
  `private`.

- **S8. Names in `requires {}`.** The basic names (`act.bot`, `read.world`, `wait`, `log`) are accepted and ignored;
  the `observe.*` names of the event kinds are the only others, and any other name is an unknown-capability error.
- **S9. Owner command names.** Bots, Groups and scripts all use `visibility shared|private`: the verb `share` and
  the adjective `private` do not pair, and the settings already say `shared`. The scripts' 0.4.0 `share <name>` and
  `private <name>` spellings were removed in 0.6.0. The [interface guide](INTERFACE_GUIDE.md#d4-owner-commands) lists
  the commands.
- **S10. Authoring in menus.** A controller's menus leave out authoring buttons; a typed authoring command is
  refused with the authorization code and a reason, as from every other surface.
- **S11. Groups with someone else's private Bot** run per member: the members the invoker may operate run, the
  others are refused in the result; the Group as a whole is not refused.
- **S12. Someone else's shared Bot.** Anyone may operate it (move, mine, despawn and so on); `forget`,
  `rename` and `visibility` stay with its owner and the console. Unowned Bots stay fully shared.

## Not decided here

Storage, calls between scripts and any other new Luau capability get their class and capability name when they are
designed, under principle 2.
