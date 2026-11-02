# Survival mining validation

> **Note (2026-10-16):** the command is now `destroy [once|continuous|stop]` (earlier `mine`, `mine start`), and
> other operations no longer end mining.
> The first observation list below used the old name and stop rules; the re-validation section covers the current
> behavior.
> **Note (2026-10-17):** the `count <blocks>` stop and the `for`/`count` combination were added after the runs above;
> see the block-count re-validation section at the end.

Implementation uses packed `start_break` and `abort_break` actions in `PlayerAuthInput` (one `start_break` per block;
BDS tracks progress itself), following
[Mojang's server-authoritative breaking flow](https://mojang.github.io/bedrock-protocol-docs/guides/block-breaking-overview/).
The server computes progress and destruction; the client waits for the corrective/broadcast block update. It does not
request instant destruction via a legacy transaction, synthesize loot, edit world blocks, or predict tool damage.
The pinned wire schema is round-tripped in regression tests; no Endstone core/authentication delta was added.

Automated tests cover command parsing/help/native overload declarations, Bot-relative coordinates, sight/face selection,
occlusion/range/air/bedrock rejection, authenticated control dispatch, packed start/abort, waiting for actual
target updates, unchanged/unrelated/layer-1 update handling, hotbar/target/stop/dimension/death cleanup, stale actions,
timeout and normal reconnect behavior. Continuous mining additionally covers target reselection, empty/bedrock waiting,
controller revocation, coalesced polling/command ordering, stale loop IDs, conditional stops, lease expiry and cancellation
while waiting. Protocol dimension strings and Endstone dimension names are normalized.

The opt-in real-server check is reproducible from a standalone clone:

```text
python scripts/raknet_spike.py --mining --output build/mining-validation-1
```

It requires the locked patched Endstone/plugin environment, the CLI's declared dependencies and installed runtime
dependencies. The script refuses existing/non-ignored output, acquires BDS through Endstone, and creates a fresh world
with `online-mode=true`, `allow-cheats=false`, no experiments and Survival mode. `scripts/mining_probe.py` is copied only
into the ignored test plugin source to prepare a platform, blocks and tools, observe actual block-break ownership and
loot/tool damage, and test native event cancellation. This fixture is never part of the product plugin/wheel.

Real Windows RakNet observations on Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, build `51798905` (2026-10-16 JST):

- Bare-hand dirt and stone were broken through native `BlockBreakEvent` by the local Bot; stone took longer and yielded
  no loot without the proper tool.
- An iron pickaxe broke stone, produced a cobblestone item actor and gained durability damage.
- `mine stop`, global `stop` and hotbar switching canceled obsidian mining while preserving the target.
- Canceling `BlockBreakEvent` preserved the stone, produced no loot and caused no tool damage.
- `mine start` broke three consecutive stone blocks with normal Bot-owned events and pickaxe durability consumption,
  waited with no target, and mined three more blocks placed along the same ray without another start command.
- `mine stop` while waiting prevented mining a later refill; explicit/global/hotbar stop also canceled active continuous
  obsidian mining while preserving the target.

The full mining smoke completed, including the normal radius/movement/jump/reconnect/negative-auth/shutdown/history
checks. Sampled command-to-observation times were 2.00 seconds (dirt hand), 1.00 seconds (stone iron pickaxe), and
10.01 seconds (stone hand); console/sampling latency is included, so these are not exact vanilla break-time benchmarks.
After adding continuous mining, plugin tests passed 108 cases, the full serial runtime suite passed 108/108, and the
repository contract suite passed 47 cases. Earlier single-target runs hit intermittent Windows `EPERM` in the unchanged
concurrent owner-key hard-link test (103/104); the latest full run passed that case as well.

## Re-validation after the `destroy` rename and stop-rule change

Real Windows RakNet run on Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, build `51798905`, fresh Survival world with
`online-mode=true`, `allow-cheats=false`, no experiments (2026-10-16 JST, `raknet_spike.py --mining`, full pass with the
final `destroy [once|continuous|stop]` grammar):

- Bare-hand dirt (2.0 s, dirt drop), iron-pickaxe stone (1.0 s, cobblestone drop, durability 1) and bare-hand stone
  (10.0 s, no drop) broke through native `BlockBreakEvent` by the local Bot.
- A single `destroy` survived `look 30 10`, `use`, `attack` and `jump` sent mid-break; BDS still completed the break as
  a normal Bot break although the Bot no longer faced the block.
- `destroy continuous` kept the same loop through `look 90 0`: the block already being broken finished, the loop then waited
  with nothing in sight, and `look 0 0` resumed it on the remaining block with the same loop ID.
- `hotbar 2` during continuous obsidian mining kept the loop active (restarted bare-hand); `destroy stop` then canceled
  it with the obsidian preserved. `destroy stop` and global `stop` each canceled active continuous mining.
- Canceling `BlockBreakEvent` still preserved the block, produced no loot and no tool damage.
- Killing the Bot through the SDK ended continuous mining; the console log recorded
  `destroy continuous stopped: canceled by death` at warning level, and the Bot respawned online.
- The rest of the smoke (operation-specific replies with the new wording, radius 12/default with server confirmation,
  reconnect, controller CLI/console pending add/remove, local-Bot rejection, policy restart, clean shutdown and world
  history: Survival, cheats/commands disabled, no experiments) passed in the same run.

## NetherNet re-validation (2026-10-16 JST)

All runs above used the opt-in RakNet transport. The same smoke now takes `--transport nethernet` (default remains
`raknet` for this script):

```text
python scripts/raknet_spike.py --mining --transport nethernet --output build/nethernet-mining-1
```

NetherNet mode writes `transport=nethernet` with `enable-lan-visibility=true`, gives the runtime the expected
`serverName`/`levelName` so it connects only to this BDS through LAN discovery (the production selection path), and
refuses to start while UDP 7551 is held by another program. The negative-auth probe discovers the same server and
dials it over NetherNet.

Real Windows NetherNet run on Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, build `51798905`, `bedrock-protocol`
`3.60.1`, Node.js `24.17.0`, fresh Survival world with `online-mode=true`, `allow-cheats=false`, `allow-list=true`, no
experiments: **full pass**, with the same checks as the RakNet re-validation above:

- Bare-hand dirt (2.0 s, dirt drop), iron-pickaxe stone (1.0 s, cobblestone drop, durability 1) and bare-hand stone
  (10.0 s, no drop) broke through native `BlockBreakEvent` by the local Bot.
- `destroy` survived mid-break operations; `destroy continuous` retargeted, waited, followed `look` and resumed with the
  same loop; `hotbar` restarted it; `destroy stop` and global `stop` canceled it; native event cancellation preserved
  the block with no loot or tool damage.
- Killing the Bot ended continuous mining with `destroy continuous stopped: canceled by death` in the console log.
- Radius 12/default were confirmed by the server (12, then 5), the override survived reconnect, and a second Bot
  online at the same time kept the shared default.
- Controller CLI/console pending add/remove, local-Bot rejection and policy across restart passed.
- Movement (3.63 blocks), a sampled jump (1.25 blocks), same-identity reconnect, despawn, wrong key/issuer/audience and
  auth-disabled rejections (each kicked without spawn), clean shutdown and world history (Survival, cheats/commands
  disabled, no Creative or experiment history) passed. BDS restarted once with a fresh NetherNet DTLS identity and
  discovery found it again with no pin.

Two earlier attempts are not counted: with the Minecraft Windows client running, BDS never appeared in discovery
(`advertisements seen: none`) because the client owned UDP 7551, and Endbot's pre-start check wrongly reported the port
free. On Windows, the client's socket does not make a second bind fail, even with `SO_EXCLUSIVEADDRUSE`. The check
now also reads the Windows UDP table and names the owning process (with unit tests). After the client was closed, the
next run passed. The same modified script then passed again over RakNet.

No human client joined over NetherNet in this run, and no Xbox achievement was attempted or observed.

## Human Windows client check (2026-10-16 JST)

A normally Microsoft/Xbox-authenticated human on the Windows Bedrock client (1.26.52) joined disposable RakNet worlds
on the same Endstone/BDS pair, bound as a controller on first join and drove the Bot with `/bot`:

- `destroy continuous` dug down under the Bot and continued through `look` and `jump`.
- With the final fixes, the controller left to the title screen for about 50 seconds; the loop kept digging with no
  stop and `status` still showed continuous mining after rejoining.
- A console `despawn` delivered `destroy continuous stopped: Bot disconnected` to the human's chat; `resume` and a new
  `destroy continuous` worked afterwards.
- A console `botcontrollers remove` delivered `destroy continuous stopped: control permission revoked`, removed `/bot`
  from command suggestions and refused `/bot ping` without a rejoin; `botcontrollers add` restored both without a rejoin.
- The crack overlay advanced smoothly once the flicker fix below was in place.
- All three worlds kept Survival with cheats/commands disabled, no experiments and no Creative history.

The human check exposed three defects that automated console-driven runs had missed; each is fixed and covered by tests:

- **Crack flicker.** A per-tick `continue_break` on the same block made BDS send `block_stop_break` and
  `block_start_break` every tick, so observers saw the crack reset (538 start/537 stop events in one traced run). The
  runtime now sends one `start_break`; a traced run after the fix showed one start/stop per block with unchanged break
  times.
- **Transient control failures.** Frequent fresh loopback connections occasionally failed on Windows with
  `WinError 10048`, and the plugin ended the loop on the first failure. It now retries for about one runtime lease.
- **Retargeting after logoff.** The continuous retarget resolved the (offline) controller and failed with
  `Command player is no longer online`. Sight retargeting no longer needs the controller.

The check also showed that the `status|resume|reconnect|despawn|forget|stop` overload, declared as one enum, appeared
in client suggestions only as `<operation: EndbotNoArgs>`. Each operation is now a separate single-value overload, like
`spawn` and `destroy`; the automated smoke confirmed the declarations load and the operations still run. On the
human client the full `/bot ` suggestion list then showed `despawn`, `forget`, `reconnect` and `resume` as their own
lines with no `EndbotNoArgs` entry. Typing a partial operation (`/bot <name> re`) still narrows only to the generic
`<operation: string> <arguments: message>` overload; that catch-all is required for reliable execution on this
Endstone baseline, so prefix filtering remains a known limitation.

Early attempts exposed a dimension-name mismatch and an observation-fixture ItemType serialization error; those runs
are not full passes. Neither an automated build nor the prepared fixture world establishes actual Xbox achievement
eligibility/unlocks. A human client over NetherNet, underwater/effects/enchantments, partial block shapes, tool breakage and
broader multi-player protection-plugin combinations remain separate validation gates. World history is inspected only
as scoped evidence, with no new Endstone/BDS compatibility claim.

## Block-count limits re-validation (2026-10-17 JST, RakNet and NetherNet)

Real Windows runs on Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, build `51798905`, fresh Survival worlds
(`online-mode=true`, `allow-cheats=false`, `allow-list=true`, no experiments), `scripts/raknet_spike.py --mining` on
the `feat/destroy-count` tree — RakNet and NetherNet (the default transport), both reports `ok`, stage `complete`:

- `destroy count 3` on a prepared five-block stone row (iron pickaxe) ended with
  `destroy continuous stopped: block count reached`; exactly three Bot-owned `BlockBreakEvent`s fired, the two blocks
  behind the row were still standing after a two-second grace period, and the runtime reported
  `reason=block count reached`, `blocksBroken=3`, `blockCount=3`.
- `status` showed `last destroy: block count reached (3/3 blocks, minecraft:stone at -7 201 -1)`.
- `destroy for 10 count 4` ended with `duration elapsed` and fewer than four blocks (the earlier limit wins) while
  keeping the count limit on the finished job.
- The rest of the smoke passed unchanged on both transports: bare-hand dirt/stone, iron-pickaxe stone with
  cobblestone drop and durability, continuous retarget/wait/resume/stop, mid-break operations, look pause/resume,
  hotbar restart, explicit/all stop with the block preserved, canceled `BlockBreakEvent` without loot or tool
  damage, death notice, `destroy for 40` aborting obsidian, radius/movement/jump/reconnect, negative auth, clean
  shutdown and world history.

`scripts/mining_probe.py` carries both count cases now, so the check is reproducible with the same command. Exact
break ownership beyond "the watched target changed" remains as documented (another actor changing the watched block
counts too), and a human client is not covered by these runs.

## `look` observation gate (2026-11-02 JST)

Real Windows run on the BOT-029 branch (`ccr-02ffe5b7-fbvlzv-bot029` at `58473ae`): patched Endstone
`0.11.15+endbot.1` (the release-candidate Windows wheel) with plugin `0.5.0` built from the branch, BDS `1.26.60.6`
(build `52886189`, protocol `2225`), NetherNet (the default transport), Python `3.12.10`, Node.js `v24.17.0`, and a
fresh Survival world (`online-mode=true`, `allow-cheats=false`, `allow-list=true`, no experiments).
`scripts/raknet_spike.py --mining` reported `ok` at stage `complete`, with every mining case passing.

- `look-then-destroy` passed on all three attempts: with the Bot turned away (`look 90 0`), `look 0 0` and a
  crosshair `destroy` sent back to back with no pause each broke the aimed `minecraft:dirt` at `23 201 -30`
  (15:13:46, 15:13:51 and 15:13:55 JST). The `destroy` resolves its target from the Bot's current BDS rotation, so
  it can only hit the aimed block if BDS already holds the rotation `look` asked for.
- All ten `look` commands in the run were answered and none failed (`look failed` appears nowhere in `server.log`).
  Back-to-back pairs such as `look 90 0` then `look 0 0` answered in the same or the next second, so BDS reported
  the rotation within the 1 degree tolerance well inside the 0.5 s budget, right after the runtime took the look.
- That answers the question the PR left open: on this pair Endstone's `Player.location` yaw and pitch for a Bot are
  within 1 degree of the rotation the Bot sends in `player_auth_input` at yaw 0, 30, 45 and 90 and at pitch 10. The
  smoke cannot report the exact observed angle, because the chat reply echoes the requested one and
  the observed values travel only in `data.look`, which the console path does not print.
- The rest of the smoke passed unchanged: bare-hand and iron-pickaxe breaking with loot and durability, continuous
  retarget/wait/resume/stop, mid-break operations, look pause/resume, hotbar restart, explicit and all stop,
  protected block, death notice, `for`/`count` ends, radius, movement and jump, same-identity reconnect, despawn,
  four negative-auth variants, controller policy across a restart, clean shutdown and world history
  (`allow-cheats=false`, no Creative and no experiment history). `runtime.log` carries no warnings; `server.log`
  only the allow-list notice of a world with no players, the canceled-by-death case and the link closing at
  shutdown.

Not covered by this run: the script-facing `data.look` values (unit-tested only), `Group look` against BDS, and the
`code="timeout"` path, which needs a Bot BDS cannot observe. The wait's budget is ten ticks expressed as 0.5 s of
wall clock (`LOOK_TIMEOUT_TICKS / 20`), so a server running below 20 TPS gets fewer than ten observations before the
timeout expires; the intent, roughly half a second, is unchanged. Reproduce with the same command from the branch.

## Block targeting gate (2026-11-02 JST)

Real Windows run on the BOT-028/BOT-030 branch (`ccr-02ffe5b7-fbvlzv-bot028` at `7b638e7`), same pair as the look
gate above: patched Endstone `0.11.15+endbot.1`, BDS `1.26.60.6` (build `52886189`), NetherNet, plugin and probe built
from the branch, fresh Survival world. `scripts/raknet_spike.py --mining` reported `ok` at stage `complete`, and the
three targeting cases added to `scripts/mining_probe.py` all passed.

- **Grass does not hide a named block.** A log at `601 201 570` with `minecraft:short_grass` in the cell in front was
  listed as `oak_log: 601 201 570 (2.5m, in reach)`, and `destroy 601 201 570` broke it on BDS.
- **A log wrapped in leaves is listed, not reachable, and names the leaf.** The same log with `minecraft:oak_leaves`
  on the other five sides was listed as `oak_log: 601 201 570 (2.5m)` with no `in reach`;
  `block 601 201 570` answered `block at 601 201 570: oak_log [pillar_axis=y] (blocked by oak_leaves at 601 201 569)`;
  `destroy 601 201 570` was refused with `destroy failed: Target is blocked by oak_leaves at 601 201 569` and the block
  stayed.
- **Reach 5 to 6, eye to the nearest face.** `5.0`, `5.5` and `5.95` each broke the log; `6.5` was refused with
  `Target block is out of reach (maximum 6 blocks)`. At exactly `6.0` the refusal was instead
  `Target is blocked by another block` with no obstruction: the eye sits 0.12 above the aimed side's centre and all
  five face samples are just outside the 6-block sphere (the nearest is 6.0012), so a target that only touches the
  limit reads as blocked. Reach is usable to about 5.99 blocks; the exact boundary carried the wrong code and no
  obstruction. Recorded as observed, no code changed for this run. `772ca14` fixed it on the same branch (a block
  whose only clear lines end past the limit now reports `out_of_reach`), and a rerun of the same smoke on that head
  confirmed it on BDS: `6.0` answered `destroy failed: Target block is out of reach (maximum 6 blocks)` while `5.0`,
  `5.5` and `5.95` still broke and the grass and leaf cases still passed (`ok`, stage `complete`).

The server's own limit is higher than the product's: with `REACH` and the runtime's `BLOCK_REACH` raised to 24 on a
throwaway branch (`probe/bot028-max-reach`, not for merge), a nearest face at 10.0 blocks from the eyes still broke while
10.25, 10.5, 10.75 and 11.0 were accepted by the plugin but never changed, so moving the reach from 5 to 6 does not
lean on BDS's edge, and there is room above 6 if a later task needs it.

Not covered by this run: typed `destroy`/`interact` (`destroy minecraft:oak_log`), the `except`/area forms, and the
script-facing `reachCode` fields (unit tests only). The probe's `cells`/`watch`/`position` request fields exist for
these scenes; every earlier mining case passed unchanged with them in place.
