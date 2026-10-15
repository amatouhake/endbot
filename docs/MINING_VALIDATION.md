# Survival mining validation

> **Note (2026-10-16):** the command is now `destroy [once|continuous|stop]` (earlier `mine`, `mine start`), and
> other operations no longer end mining.
> The first observation list below used the old name and stop rules; the re-validation section covers the current
> behavior.

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
eligibility/unlocks. Human clients, NetherNet, underwater/effects/enchantments, partial block shapes, tool breakage and
broader multi-player protection-plugin combinations remain separate validation gates. World history is inspected only
as scoped evidence, with no new Endstone/BDS compatibility claim.
