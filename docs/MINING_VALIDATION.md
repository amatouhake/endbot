# Survival mining validation

> **Note (2026-10-16):** the command is now `destroy` (earlier `mine`), and other operations no longer end mining.
> The first observation list below used the old name and stop rules; the re-validation section covers the current
> behavior.

Implementation uses packed `start_break`, `continue_break` and `abort_break` actions in `PlayerAuthInput`, following
[Mojang's server-authoritative breaking flow](https://mojang.github.io/bedrock-protocol-docs/guides/block-breaking-overview/).
The server computes progress and destruction; the client waits for the corrective/broadcast block update. It does not
request instant destruction via a legacy transaction, synthesize loot, edit world blocks, or predict tool damage.
The pinned wire schema is round-tripped in regression tests; no Endstone core/authentication delta was added.

Automated tests cover command parsing/help/native overload declarations, Bot-relative coordinates, sight/face selection,
occlusion/range/air/bedrock rejection, authenticated control dispatch, packed start/continue/abort, waiting for actual
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
`online-mode=true`, `allow-cheats=false`, no experiments (2026-10-16 JST, `raknet_spike.py --mining`, full pass):

- Bare-hand dirt (2.0 s, dirt drop), iron-pickaxe stone (1.0 s, cobblestone drop, durability 1) and bare-hand stone
  (10.0 s, no drop) broke through native `BlockBreakEvent` by the local Bot.
- A single `destroy` survived `look 30 10`, `use`, `attack` and `jump` sent mid-break; BDS still completed the break as
  a normal Bot break although the Bot no longer faced the block.
- `destroy start` kept the same loop through `look 90 0`: the block already being broken finished, the loop then waited
  with nothing in sight, and `look 0 0` resumed it on the remaining block with the same loop ID.
- `hotbar 2` during continuous obsidian mining kept the loop active (restarted bare-hand); `destroy stop` then canceled
  it with the obsidian preserved. `destroy stop` and global `stop` each canceled active continuous mining.
- Canceling `BlockBreakEvent` still preserved the block, produced no loot and no tool damage.
- Killing the Bot through the SDK ended continuous mining; the console log recorded
  `continuous destroy stopped: canceled by death` at warning level, and the Bot respawned online.
- The rest of the smoke (operation-specific replies with the new wording, radius 12/default with server confirmation,
  reconnect, controller CLI/console pending add/remove, local-Bot rejection, policy restart, clean shutdown and world
  history: Survival, cheats/commands disabled, no experiments) passed in the same run.

Not covered by this automated run: a human controller logging off while continuous mining runs, a human receiving the
abnormal-end chat message, and online human add/remove. They need a normally authenticated human client.

Early attempts exposed a dimension-name mismatch and an observation-fixture ItemType serialization error; those runs
are not full passes. Neither an automated build nor the prepared fixture world establishes actual Xbox achievement
eligibility/unlocks. Human clients, NetherNet, underwater/effects/enchantments, partial block shapes, tool breakage and
broader multi-player protection-plugin combinations remain separate validation gates. World history is inspected only
as scoped evidence, with no new Endstone/BDS compatibility claim.
