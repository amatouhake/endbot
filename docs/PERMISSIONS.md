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
requires { "observe.chat" }
params { ... }
```

- The basic set needs no declaration: operating Bots the invoker may operate, reading the world, waiting and logging.
  Beginner scripts stay as they are.
- `requires {}` must be an unconditional top-level call before `params{}`; a later or conditional call is an
  inspection error, so what inspection reads is the whole declaration.
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

- Assignment: `endbot controllers add <GamerTag> --role scripter|controller`; the same option on an existing grant
  changes the role.
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
- Migration: existing Bots and Groups have no owner and stay shared, like scripts saved before ownership; the console
  can assign owners. A Bot created by a run or trigger is owned by the run's invoker or the trigger's owner.
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

Moving a scripter to controller keeps their scripts. An owner may always run their own scripts, private ones
included, so they stay runnable by them but are no longer editable by them.

## Steps

Status: **Agreed** (2026-10-29 JST). Each step is split into PRs; a PR changes one thing for one reason and keeps
main releasable. BOT-024 ships in the same release as BOT-020, BOT-021 and BOT-022, so a step may land between them,
but no release is cut from main until the whole set is in. "Claude" marks security boundaries, cross-cutting
authorization and migrations; "DeepSeek" marks pieces that can be handed over with a precise spec and acceptance
tests, reviewed by Claude.

Placement against the [endbotd steps](ENDBOTD.md#steps): today the two-layer check lives in the plugin
(`EndbotPlugin._authorize`) and moves to endbotd in BOT-020 3c (P4). Anything that changes that check waits for 3c;
anything that adds a store waits for BOT-020 4, so it joins `migrations.json` and the doctor `stores` check instead
of inventing its own rollback; anything that changes `endbot controllers` waits for BOT-020 5, so it is written
once, against the endbotd endpoint, not against the file request channel being removed.

1. **Declarations in the registry.** Can land now, before endbotd.
   - **1a. Every operation and Luau host request names its capability** in `operations.json` (schema-required
     field). Today everything is in the basic set (`act.bot`, `read.world`, `wait`, `log`); the classes in the table
     above are the only names. A test fails when an operation or a `runner/api.luau` request type has none
     (principle 2). Nothing players see. Step 1a landed 2026-10-30 JST. *DeepSeek.*
2. **Script declarations.** Can land before endbotd, but not while 3c is open: it touches `scripting.py`, which the
   switch moves.
   - **2a. `requires {}` in the runner and inspection.** Exactly once, before `params{}`; an unknown name, a second
     call or a call after `params{}` is an inspection error naming it. Inspection returns the declaration with the
     params schema. A run whose own `requires {}` differs from what inspection read stops before its first
     operation (S3). Scripters see a new Luau function and its errors; existing scripts are unchanged.
     *Claude.* **Landed** (2026-10-30 JST). Until events add `observe.*`, the basic names are accepted and ignored
     and every other name is unknown (S8); the basic set is fixed in code, not derived from the registry. A
     declaration is a set (order does not matter, a duplicate is an error). The comparison applies to runs started
     from an inspection: the run form carries the declaration in its guard, the parameterless menu start passes it,
     and triggers will; a typed `/botscript run` is held to its own declaration, which it cannot widen beyond its
     invoker either.
   - **2b. Forms show the declaration** before a shared script runs, from the inspection result. Players see one
     line on the run form when a script declares anything. *DeepSeek.*
3. **Roles.** After BOT-020 3c and 4; 3c after BOT-020 5.
   - **3a. Role store.** `state/controller-roles.json`, keyed by XUID, read by endbotd; a grant with no entry is
     **scripter** (the migration rule), revoked grants keep no role (S1). Registered with the BOT-020 4
     migration and doctor `stores` check. Nothing players see. *Claude.* **Landed** (2026-10-30 JST).
   - **3b. Role in the principal and the check.** `Principal` carries the role, read at each action (S5);
     the authoring operations (S6) refuse a controller with the authorization code, from every surface.
     Nobody is a controller yet, so nothing players see. *Claude.*
   - **3c. Assigning roles.** `--role scripter|controller` on `endbot controllers add` and the `botcontrollers` console
     command, the same option changing an existing grant, the default **controller** for a new grant, and the role in
     `endbot controllers list`. Operators see the option; new controllers can no longer author. *DeepSeek.*
4. **Ownership of Bots and Groups.** After BOT-020 3c and 4. Scripts already follow the model.
   - **4a. Ownership store** for Bots and Groups: owner and visibility by identity id, absent means unowned and shared
     (S2); a Bot spawned by a run or trigger is owned by its invoker or the trigger's owner; `[permissions]
     bots-default` and `groups-default` (S7). Nothing players see while the defaults are `shared`. *Claude.*
   - **4b. Enforcement in target resolution**, per member for Groups, so a command, form, script or web call on
     someone else's private Bot fails the same way (S4). Players see refusals only after someone makes an
     item private. *Claude.*
   - **4c. Owner commands.** `share` / `private` for Bots and Groups and console reassignment of owners for Bots,
     Groups and scripts, with names from the [interface guide](INTERFACE_GUIDE.md). Players see the new subcommands.
     *DeepSeek.*
5. **Per-controller Bot limit.** After 4a, since it counts owned Bots: `[limits] max-bots-per-controller`, `0` meaning
   no limit (the default). *DeepSeek.* The trigger and watch limits, trigger pinning and pausing, and triggers being
   disabled on revoke ship with BOT-022; web sessions ending on revoke ship with BOT-021. Both build on 3b and 4b and
   carry their own tests; they are not BOT-024 PRs.

SECURITY.md is updated in the PR that changes what is enforced, not in a separate PR. Running scripts and jobs already
end on revoke (the grant revision); no PR is needed for that row.

**Validation in the single final pass** (not per PR): update a server with existing grants, Groups and scripts, and
check every controller is a scripter and everything is shared as before; then roll back and check the old version
starts and behaves as today. Live RakNet smoke on the locked pair: a controller-role account refused authoring from
command, form and script menu; another person's private Bot refused identically from command, form and a shared script;
a run spawning a Bot and that Bot's owner; a run stopped by a `requires {}` mismatch; revoke during a run. Human Windows
Client check: the declaration line on the run form, the refusal messages, `controllers list` roles, and achievement
history unaffected.

### Settled points for the steps

Decided 2026-10-29 JST, following the recommendations of the draft.

- **S1. The role lives in** a separate `state/controller-roles.json`, not a version 2
  `controller-policy.json`, for the reason P4 gives: the current loader rejects an unknown version and unknown grant
  fields, so a v2 policy breaks rollback, while an unknown file is ignored. After a rollback everyone acts as a
  scripter, which is today's behavior.
- **S2. Bot and Group ownership lives in** a separate store, not new fields in runtime profiles (strict
  `schemaVersion`) or `groups.json`. The release notes must state the consequence: after a rollback, private Bots and
  Groups are shared again.
- **S3. "Unconditional top-level" is enforced** at run time, not by parsing source: `requires {}` must
  come before `params{}`, once, and the run's declaration must equal the inspected one, so a conditional call cannot
  widen a run.
- **S4. Someone else's private items.** Bots are visible in game anyway, so they stay listed and are refused
  with the authorization code; private Groups are not visible in game, so they are left out of other people's lists
  and resolve as not found.
- **S5. Role changes during a run.** The role is read at each authoring action; a role change does not bump
  the grant revision, so runs and jobs continue (running is the same for both roles).
- **S6. Authoring** is create, save, paste or book import, copy, rename, restore and `share`.
  A controller may still run, `private` and delete their own scripts, which only narrows what others can use.
- **S7. Setting names** are `[permissions] bots-default` and `groups-default`, each `shared` (default) or
  `private`.

Decided 2026-10-30 JST, before the steps were implemented.

- **S8. `requires {}` before events exist.** Until BOT-022 adds `observe.*`, the basic names (`act.bot`,
  `read.world`, `wait`, `log`) are accepted and ignored, and every other name is an unknown-capability error.
- **S9. Owner command names for Bots and Groups** follow the scripts' `share` / `private`; the interface guide
  records them in 4c.
- **S10. Authoring in menus.** A controller's menus leave out authoring buttons; a typed authoring command is
  refused with the authorization code and a reason, as from every other surface.
- **S11. Groups with someone else's private Bot** run per member: the members the invoker may operate run, the
  others are refused in the result; the Group as a whole is not refused.
- **S12. Someone else's shared Bot.** Anyone may operate it as today (move, mine, despawn and so on); `forget`,
  `rename` and `share` / `private` stay with its owner and the console. Unowned Bots stay fully shared.

## Not decided here

Storage, calls between scripts and any other new Luau capability get their class and capability name when they are
designed, under principle 2.
