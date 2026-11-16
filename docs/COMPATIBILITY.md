# Compatibility and releases

## Current baseline (0.5.0 to 0.7.0)

`endstone.lock` pins Endstone `v0.11.15` at `b4aaa87d2c5305e7d340168f824ac2e2ea722e44`, with BDS `1.26.60.6`
(Linux build `52886190`, Windows build `52886189`) and protocol `2225`, `support_status: "validated-baseline"` since the
gate below passed on 2026-10-30.

Endstone pins its direct Conan requirements, but some transitive ones are version ranges. `scripts/endstone-conan.profile`
pins each of those (zlib, zstd, libdwarf, and on Linux xz_utils, libunwind and libcurl), with its recipe revision, to what
the 0.5.0 and 0.6.0 release candidates resolved, and every patched Endstone build uses it as its host profile. It is part
of the pair: change it only with a compatibility pass, like `endstone.lock`. The direct requirements' own recipe
revisions are not pinned yet.

- Minecraft 26.60 clients use protocol `2225` and cannot join the `1.26.52` pair, which is why 0.5.0 moves.
- BDS 1.26.60 refuses players on RakNet ("NetherNet is the only supported transport type", checked on the Linux build
  2026-10-30), so Endbot drops its opt-in RakNet transport.
- The Endbot patch series needed only a three-way context rebase (`runtime/CMakeLists.txt`,
  `server_network_handler.cpp`, `tests/CMakeLists.txt`).
- The runtime moves to `bedrock-protocol` 3.61.0 and `minecraft-data` 3.119.0, which ship the 1.26.60 / protocol-2225
  schema; the runtime unit suite passes against it.
- Upstream 0.11.15 binds event cancellation per event class; the plugin uses none of that API.

### 0.11.15 / 1.26.60.6 compatibility gate — 2026-10-30

- Release-candidate runs 41023533108 and 41024591616 cold-built and upstream-tested the patched Endstone on Linux and
  Windows. The bundle consumer E2E (fresh setup acquiring BDS `1.26.60.6` through Endstone, a Bot joining through
  local-bot trust, console, `doctor --live`, update and rollback) passed on both platforms, with LAN discovery and
  with direct signalling; the Linux rollback start then exposed the server-port TIME-WAIT wait now in `endbot start`.
- Windows live smoke (`scripts/raknet_spike.py --mining --inventory --item-use --entity --groups --scripting`, direct
  signalling, BDS build `52886189`) passed: movement, jump, chunk radius, reconnect, mining, pickups and drops, timed
  item use, feeding and riding, Groups, Luau scripts, controller management across a BDS restart, and the wrong-key,
  wrong-issuer, wrong-audience and auth-disabled refusals. Three fixture fixes came out of it (a wider scripting
  platform, a retry while a fed cow wanders off the crosshair, waiting for the plugin to relink after a restart); one
  run also failed once on a pickup/drop check that passed on the other runs.
- The `0.5.0-rc.1` candidate (run 41044019145, with #173: a login that never reaches spawn now fails the attempt;
  found when Bots stayed in `reconnecting` after a restart) passed its CI cold build, both consumer E2E jobs and
  assembly.
- People, on Windows with Minecraft 26.60: one client joined three instances on the same PC by address while their
  Bots were online, two of them at once, with UDP 7551 held by one BDS; `/bot list` and `/bot <Bot> tp me` worked on
  each. On an instance updated from 0.4.0 the player unlocked the Xbox achievement **Body Guard** with no
  achievements-disabled notice, and `level.dat` stayed clean
  ([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/PERMISSIONS_VALIDATION.md#050-human-pass-with-one-account--2026-10-30-jst)).

0.6.0 (2026-11-02 JST) keeps this pair unchanged: no `endstone.lock`, `patches/endstone/` or runtime dependency
changed, so no new compatibility gate applies and the 2026-10-30 gate and achievement observation stand. Its release
pass covered the new command surface on a client; see [bot operations](#bot-operations).

0.7.0 (2026-11-12 JST) keeps the pair as well: `endstone.lock`, `patches/endstone/` and the runtime dependencies are
unchanged. The Conan host profile above is new in 0.7.0; it pins what the 0.6.0 candidate had resolved, after conancenter
published newer ranges that broke the Windows build, so the built Endstone is the same. The 2026-10-30 gate and
achievement observation stand; the release pass checked `level.dat` again and found no creative or experiment
history ([events](#events-watches-and-triggers)).

## Previous baselines

A patched package never inherits an earlier package's human or Xbox observations, even on the same upstream pair.

| Endbot | Endstone | BDS | Protocol | Human and Xbox observation |
| --- | --- | --- | --- | --- |
| 0.2.1 to 0.4.0 | `v0.11.13` (`3491c609`), package `0.11.13+endbot.1` | `1.26.52.3` | 2193 | 2026-10-09: on a vanilla server adopted with `setup --existing`, an Xbox-authenticated human joined beside a Bot, ran `/bot ping` and unlocked **Rabbit Season**. Operator `/give` and `/effect` were used in the same session, so it is not evidence of a cheat-free session |
| 0.1.0 to 0.2.0 | `v0.11.12` (`1c71186c`), package `0.11.12+endbot.1` | `1.26.51.1` | 2193 | 2026-09-27: on the exact 0.1.0 release files, the operator unlocked **Archer** after driving Bots with `/bot` |
| before 0.1.0 | `v0.11.11`, package `0.11.11+endbot.1` | `1.26.51.1` | 2193 | M0 (2026-09-18): `/bot ping` and an unlock in one session, with ten external Bots tested. M2 (2026-09-19): an unlock after lifecycle, input and dimension-teleport operations |

0.2.0 to 0.4.0 also offered an opt-in RakNet transport. BDS 1.26.60 refuses players on RakNet, so `endbot setup` and
`endbot start` have not offered it since 0.5.0.

The gates, their negative controls and what each left open are in the records as they stood at 0.7.0:
[compatibility](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/COMPATIBILITY.md#previous-baseline-021-to-040),
[achievements](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/ACHIEVEMENTS.md), [M2](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/M2_VALIDATION.md), [RakNet](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/RAKNET_VALIDATION.md) and the
[release record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/release/FINAL_CHECKLIST.md).

## Validation status

What people checked on the current baseline, and what they did not. A green build never stands in for these checks.
Each area links to the dated record of its runs, kept at the commit that last held it.

### Web

Checked on 0.7.0 release candidates (rc.1 to rc.3, then rc.5 and rc.6) by the operator, on Cloudflare with real
passkeys on a Windows PC and an Android phone ([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/WEB_VALIDATION.md)):

- deploy, redeploy and `rotate-key`; the token permissions in the guide; a Worker Endbot did not create is refused;
- linking from `/botweb link` on the phone and the PC, a second passkey from a second code, cross-device QR sign-in;
- the rate limits; revoking a grant with sessions open and during a script run;
- status, history and script editing with BDS stopped;
- a Worker with a wrong link key refused; network loss and a power cut of the server PC;
- a protocol mismatch refused and named in the log; `endbot update`, `--rollback` and `--force` between 0.7.0
  candidates, with passkeys and triggers kept;
- a custom domain and the change back to `workers.dev`, each deleting passkeys and sessions as documented;
- the trusted site in `link` and `sign-in` mode, two keys, a replayed and a foreign-browser assertion refused, an XUID
  without a grant refused, and `endbot web revoke`. The site was a signer Worker made for the pass, not an operator's
  production site.

Not run:

- a trigger made private disappearing from another viewer's open list, and the web refusing a change to another
  player's trigger: both need a second Xbox account ([#272](https://github.com/amatouhake/endbot/issues/272));
- a rollback from 0.7.0 to 0.6.0 ([#278](https://github.com/amatouhake/endbot/issues/278)). It needs `[events]`, `[web]` and the three new `[limits]` keys removed first,
  because 0.6.0 refuses keys it does not know.

### Events, watches and triggers

Checked on the locked pair. What each event kind guarantees, and the measurement behind it, is in
[EVENT_MEASUREMENTS.md](EVENT_MEASUREMENTS.md).

With Bots only, 2026-11-08 ([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/EVENTS_VALIDATION.md#bot-only-live-smoke)): a watched block change delivered
once, the 64-occurrence queue reporting `missed`, a dropped plugin link reported as a gap, a watched area over the
limit refused, a trigger starting its script, and triggers coming back armed after endbotd was killed.

With one Xbox account in the Windows client, on `0.7.0-rc.5` and `rc.6`
([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/EVENTS_VALIDATION.md#release-pass)):

- a human breaking and placing watched blocks; `player.join`, `player.leave` and opt-in `player.chat`, with `/tell`,
  `/msg` and `/w` never delivered;
- `bot.online`, `bot.offline`, `bot.died`, `job.finished`, `server.started`, `schedule` and `world.time`;
- BDS stopped with a watch open, BDS started again, and endbotd killed under a running BDS;
- the start cap throttling a trigger, the per-controller share of watched cells, and a relative watched area refused
  with its reason;
- revoking a grant disabling the player's triggers, which stay disabled after a regrant;
- the trigger forms in the Windows client and the trigger screens on a phone;
- `state/triggers.json` carried through `endbot update`, `--rollback` and `--force`;
- `level.dat` with no creative or experiment history after the pass.

Not run:

- a shared script's declaration growing under another player's trigger (`script changed`), and a player refused a
  change to another player's trigger: both need a second Xbox account ([#272](https://github.com/amatouhake/endbot/issues/272));
- a chat muted by a behavior pack still reaching `player.chat` ([#273](https://github.com/amatouhake/endbot/issues/273));
- a profile removed while its Bot is online (`bot.offline` with `reason=removed`) ([#273](https://github.com/amatouhake/endbot/issues/273));
- actual Xbox achievement unlocks, which were not part of this pass.

### Bot operations

On the current pair:

- The 2026-10-30 compatibility gate's Windows smoke covers movement, jump, chunk radius, reconnect, mining, pickups
  and drops, timed item use, feeding and riding, Groups and Luau scripts (see [the gate](#current-baseline-050-to-070)).
- 2026-11-02, Bots only ([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/MINING_VALIDATION.md#look-observation-gate-2026-11-02-jst)): `look`
  completing when BDS observes the rotation; a named block reached through grass, a block wrapped in leaves listed
  but refused with the obstruction named, and reach up to 6 blocks; boxes, spheres and `except` narrowing `blocks`
  and `destroy`, with the removed `radius` spelling refused.
- 2026-11-02, one human Windows client
  ([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/MINING_VALIDATION.md#human-windows-client-pass-2026-11-02-jst)): the Japanese help pages, the Find
  blocks form, `look` then crosshair `destroy`, and the removed spelling refused.

On the earlier pair (`1.26.52.3`), not repeated since:
[mining](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/MINING_VALIDATION.md), including a human client watching continuous mining and a live revoke and
regrant; [pickups, drops, swap and hold](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/INVENTORY_VALIDATION.md);
[Groups](https://github.com/amatouhake/endbot/blob/cad9d962b49dedabb60d6dd37641c67d2e31df84/docs/GROUPS.md#verification), by console probe and on a human client;
[timed item use and equipment](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/ITEM_USE_SYNC_VALIDATION.md), including a human client confirming the trident
throw; and [dimension changes, radius and controller policy](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/BOT_BEHAVIOR_VALIDATION.md), Bots only.

Not run:

- dimension changes watched by human clients, portal travel and knockback after a transition ([#274](https://github.com/amatouhake/endbot/issues/274));
- item use and held-item rendering beyond the trident case, other-client rendering of `swap` and `hold`, low TPS and
  large NBT ([#275](https://github.com/amatouhake/endbot/issues/275));
- a felling script on a real tree, live Luau `area.*` values and the script-facing reach and look fields
  ([#276](https://github.com/amatouhake/endbot/issues/276));
- boats and horses, a revocation in the middle of a Group batch, the UI pack on a mobile client, and sustained
  multi-Bot load ([#277](https://github.com/amatouhake/endbot/issues/277)).

### Scripting

- Bots only ([record](https://github.com/amatouhake/endbot/blob/cad9d962b49dedabb60d6dd37641c67d2e31df84/docs/SCRIPTING.md#live-validation)): a Group row template placing Bots
  within 0.35 blocks, a patrol stopped by name and by run id, console input surviving a run's cleanup, a runaway loop
  ended by the execution budget, the operation rate limit throttling, a message flood ending the run, and mining at
  spots. The two rate-limit cases ran on the current pair (2026-11-02), the others on the earlier pair (2026-10-23).
- A human Windows client, for 0.4.0 on the earlier pair: templates, Copy and edit, revision history, rename, sharing,
  book import and export, `runs` and `stop`, and a runaway loop interrupted.

Not run: a second controller seeing only shared scripts ([#272](https://github.com/amatouhake/endbot/issues/272)); the OS confinement under real memory
pressure, CPU exhaustion and parent death inside a running BDS on both platforms, beyond the unit and OS probes.

### Permissions and endbotd

Checked for 0.5.0:

- endbotd ([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/ENDBOTD_VALIDATION.md)): the Bot-only live smoke, then a human Windows client finding
  commands, forms, script menus and messages unchanged, and a command issued while endbotd restarts refused with a
  reason and working afterwards. One observation stayed unexplained ([#279](https://github.com/amatouhake/endbot/issues/279)).
- The permission model ([record](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/PERMISSIONS_VALIDATION.md)): the Bot-only live smoke, and a human pass with
  one account covering the update from 0.4.0, roles and ownership as far as one person can see them.

Not run: every check that needs a real second person ([#272](https://github.com/amatouhake/endbot/issues/272)). 0.5.0 shipped without them; their logic is
covered by unit tests, and with the default `shared` nothing changes for players until someone makes an item private.

## Compatibility change gate

A proposed Endstone/BDS update is not supported merely because it compiles. It must pass:

1. exact lock resolution and clean patch application;
2. patched Endstone build and upstream tests;
3. Endbot plugin, runtime, auth-policy, portability, and preflight tests;
4. real BDS normal-player Microsoft login;
5. valid local-bot login plus wrong-key/signature/expiry/issuer/`cpk`, disabled-mode, and replay negative controls;
6. concurrent-bot smoke testing and reconnect behavior appropriate to that release;
7. configuration and version-aware world-history inspection;
8. the manual `/bot ping` and actual Xbox achievement observation in `ACHIEVEMENTS.md`.

Only after reviewed evidence should `endstone.lock`, the compatibility manifest, and support documentation change.
Linux remains the CI/release-artifact baseline. The runtime uses Node and Python filesystem/socket APIs that are portable to
Windows and avoids POSIX-only runtime requirements. Native Windows hosting has additionally been live-smoked
successfully after PR #4 (see above), but that smoke does not give Linux and Windows identical CI/release coverage.

## Release contents

`.github/workflows/release-candidate.yml` exists and assembles a release candidate on manual dispatch: it clean-builds
the patched Endstone tree, runs its upstream tests, builds and inspects the Linux Endstone wheel, builds the Endbot
plugin wheel, packages the Endbot runtime and example configuration, generates the compatibility manifest and
SHA-256 checksums as currently implemented, and uploads the `dist/` candidate as a workflow artifact. It does not
publish a production release automatically. It must not package the official BDS binary. `scripts/generate_compatibility_manifest.py` binds metadata to the current Endbot Git revision
and SHA-256 of the ordered patch series. The Linux Endstone artifact is repaired with pinned upstream Endstone's
configured cibuildwheel manylinux image and `auditwheel` repair command, then inspected and installed with the plugin in
a clean consumer image that has no LLVM runtime. Raw build-host wheels remain inside cibuildwheel's disposable container
and are never uploaded. No workflow publishes a production release.
