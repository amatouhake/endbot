# Permission model validation (BOT-024)

This record covers the validation of [PERMISSIONS.md](PERMISSIONS.md) after steps 1a to 5 landed (#154–#164). It
separates the autonomous live smoke, which local Bots can drive on their own, from the checks that need people: a
local Bot has no XUID, so it can never hold a controller grant, own a Bot or be refused as someone else. Those checks
belong to the single final validation pass of the BOT-020/024/021/022 release ([delivery](DIRECTION.md#delivery)) and
are listed below so that pass can run them as written. Runs use the locked pair: official BDS `1.26.52.3` and patched
Endstone `0.11.13+endbot.1`. No server, world, identity, key, token or machine-local path is kept in the repository.

Status: **autonomous smoke recorded; human checks open.** The release containing BOT-024 is not validated until every
item below is recorded as passed on the final main.

## Autonomous live smoke on the locked pair — 2026-10-30 JST

`scripts/raknet_spike.py --mining --groups --scripting` on Windows over RakNet against main at `7cd22d5` (all of
BOT-024) plus the console `--role` fix (#165), with the BOT-024 checks this record adds to the spike. The run completed
with `ok: true`, BDS `1.26.52.3` (build 51798905).

- [x] Roles through the CLI and endbotd: `endbot controllers add` without `--role` lists the grant as
      `(role controller)`; `--role scripter` changes it; `--role admin` is refused by the CLI; `endbot doctor` reports
      `controller-roles`.
- [x] Roles at the BDS console: `botcontrollers add "Pending Controller" --role scripter` lists `(role scripter)`, and
      the revoke leaves no pending role in `controller-roles.json`. The first run of this check found that the console
      passes its text as one argument, so the option was read as part of the GamerTag; #165 fixes it and this run
      passed on the fix.
- [x] Ownership: a Bot the console spawns gets no owner in `ownership.json`; `/bot <Bot> private` on it is refused
      ("has no owner"), and `owner` refuses a local Bot's name and an unknown GamerTag ("is not a known player").
- [x] `requires {}`: a script declaring `act.bot` and `wait` saved and ran to `completed`; a script declaring
      `observe.chat` was refused at save with "Unknown capability in requires: observe.chat" and was not stored.
- [x] The existing checks in the same run: Group commands, script runs and stops, Survival mining, movement, radius,
      reconnect, despawn, controller policy across a restart, a clean shutdown, and local-bot authentication refusing
      the wrong key, issuer and audience and the auth-disabled variant.
- [x] World history stayed clean (no cheats, no experiments, never loaded in Creative).

Not covered here, by design: anything that needs a player with an XUID (below).

## Human checks for the final validation pass

Run with real Xbox accounts: the operator at the console and two people on unmodified Windows clients, called A and B
here. Never record their GamerTags or XUIDs in the repository.

Update and rollback (S1, S2):

- [ ] Update a server that has existing grants, Groups and scripts from the last release. `endbot controllers list`
      shows every existing controller as `(role scripter)`, every Bot and Group stays shared, and `endbot doctor`
      passes `controller-roles` and `ownership`.
- [ ] Roll back with `endbot update --rollback`. The old version starts and behaves as before; Bots and Groups made
      private on the new version are shared again, and every controller can author scripts again.

Roles (3b, 3c, S6, S10):

- [ ] `endbot controllers add <A>` (no `--role`) makes A a controller; `list` shows `(role controller)`.
- [ ] As A: `/botscript` save from the editor, `copy`, `rename`, `restore`, `share`, `edit` and book import are each
      refused with the "ask the operator for the scripter role" reason, in English and in Japanese.
- [ ] As A: the script menus leave out *New script*, *Copy and edit*, *Edit lines*, *Rename* and *Share*, and keep
      *Run*, *Stop runs*, *Make private*, *Delete* and a read-only *Earlier revisions*.
- [ ] As A: running a shared script and a file script works; making A's own script private and deleting it works.
- [ ] `endbot controllers add <A> --role scripter` (and the console's `botcontrollers add <A> --role scripter`)
      lets A author again on the next command, without a restart or a rejoin.
- [ ] A role given before A first joins (`add` while A is offline and pending) applies after A's first join.

Ownership (4a–4c, S4, S11, S12):

- [ ] A spawns a new Bot: it belongs to A (`ownership.json` names A's XUID). With `[permissions] bots-default =
      "private"` a new Bot is private from the start.
- [ ] A runs `/bot <Bot> private`. B still sees it in `/bot list` and the Bot menu, but `/bot <Bot> status`, a move
      from a form and a shared script targeting it are all refused with the same authorization reason.
- [ ] A Group of B's holding A's private Bot and an unowned Bot: `/bot #group move forward` runs the unowned member
      and refuses only A's Bot; the summary counts it as "not permitted".
- [ ] On A's shared Bot, B can move and mine it but `forget`, `rename`, `share` and `private` are refused, and A can
      do all four.
- [ ] A makes a Group private: it disappears from B's `/botgroup list`, Group menu and script parameter dropdown,
      and `/bot #group status` tells B it does not exist.
- [ ] B starts `destroy continuous` on A's shared Bot; A makes it private; B's job ends with "the Bot became
      private".
- [ ] A Bot an A-run script spawns belongs to A.
- [ ] The console reassigns a Bot, a Group and a script with `owner <GamerTag>`; a player's `owner` is refused.
- [ ] Unowned Bots (made before the update or by the console) refuse `share` / `private` from everyone with the
      pointer to the console's `owner`.

Limits (5):

- [ ] With `[limits] max-bots-per-controller = 1`, A's second new Bot is refused with the count and the limit;
      reconnecting A's first Bot still works.

Scripts (2a, 2b):

- [ ] The run form shows no declaration line for a script without `requires {}` (no capability beyond the basic set
      exists until BOT-022; the line itself is covered by unit tests).

History:

- [ ] Achievement history is unaffected before and after the session.
