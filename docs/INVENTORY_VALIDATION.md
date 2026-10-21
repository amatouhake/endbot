# Picked-up hotbar items and drop synchronization

Source changes on Endstone `0.11.13+endbot.1`, official Windows BDS `1.26.52.3` (observed build `51798905`), protocol
`2193`, `bedrock-protocol` `3.60.1`, Node.js `24.17.0`, Python `3.12.10`. The lock and core patch series are unchanged.

## Implementation

The plugin reads all 36 BDS inventory slots plus offhand/head/chest/legs/feet at a configurable interval (four ticks by default) and sends changed
observations plus periodic unchanged corrections (20 ticks by default). Each bounded, UUID-scoped
observation is sent over the local Bot's existing connection using public `Player.send_packet`. The `script_custom_event`
envelope is a protocol-2193 pair of strings, not execution of scripts or an experimental/Beta API. The runtime resolves
item identifiers through that connection's server item registry, preserves typed NBT and updates its transient cache.
Block runtime IDs are observed through public `create_block_data`. No inventory assignment, item creation or removal
occurs in this product path. Normal equipment packets update the selected hand; BDS still validates item transactions.

Each `endbot:inventory-v2` fragment has a 60 KiB bound. Wrong-identity, partial, unknown-item or malformed observations leave good cached state
intact. Native inventory updates remain supported. Four ticks means about 0.2 seconds at 20 TPS, not an instantaneous
pickup guarantee. The matching plugin is needed; a runtime alone retains the reconnect recovery for missing native deltas.

## Automated checks

Python checks cover read-only access, all 41 slots, server block palette lookup, Unicode, typed enchantment NBT,
64-bit IDs, byte arrays, and packet encoding. Runtime session regressions round-trip the real pinned serializer for
the observation envelope, equipment and drop transactions, including NBT. They cover same-session pickup, own-drop
prediction, authoritative correction after rejection, empty-hand announcement, unchanged snapshots and invalid-input
isolation. Forms cover both menu entry points, compact navigation, response parsing and normal command authorization.

## Windows RakNet real-server matrix (2026-10-16 JST)

Reproduce from a standalone clone in a dedicated environment with the locked patched Endstone/plugin installed:

```text
python scripts/raknet_spike.py --inventory --output build/pickup-inventory-raknet
```

The runner acquires BDS through Endstone and uses a fresh disposable Survival world with `online-mode=true`,
`allow-cheats=false`, `allow-list=true` and no experiments. The opt-in fixture prepares a platform and world item actors
and observes actual BDS inventory/loot. It is never shipped in the plugin. Output, identities and raw logs remain ignored.

Full RakNet smoke passed. The pickup matrix performs no Bot reconnect between pickup and item actions:

| Natural pickup | Selected slot | BDS-observed ejections |
| --- | --- | --- |
| Five dirt blocks into an empty hotbar | 1 | One, then remaining four |
| Five apples into a nonselected slot | 2 | One, then remaining four |
| Four apples merged with three existing apples | 1 | One, then remaining six |
| Named diamond pickaxe, Efficiency II, damage 7 | 3 | One; full dropped-item NBT equals pickup NBT |

Each empty slot subsequently rejected drop rather than using stale cached contents. The full smoke also passed radius
override/default and isolation, movement/jump, lifecycle reconnect, controller policy restart, negative authentication
controls, clean shutdown and scoped world-history inspection. There was no human participant or actual Xbox unlock.

## Windows NetherNet extended matrix (2026-10-16 JST)

```text
python scripts/raknet_spike.py --inventory --transport nethernet --output build/pickup-inventory-nethernet
```

The same four natural-pickup cases passed on the same locked pair over NetherNet, plus native `PlayerDropItemEvent`
cancellation: a canceled drop kept all five apples and created no item actor; after cancellation was disabled, the next
one/stack operations ejected one and four. This also exercises authoritative correction of the runtime's optimistic
count. The full remaining smoke, negative authentication, clean shutdown and scoped world-history inspection passed.

An earlier combined `--inventory --mining --transport nethernet` attempt passed the entire mining matrix, then stopped
on a Windows transient observation-file read during the second pickup case (`KeyError: dropped`); it is not a full pass.
The fixture reader now retains its last successfully read atomic snapshot across transient read failures. The later
inventory-only run completed the full extended matrix. The fixture's behavior and the product synchronization are
separate; no product change was needed to resolve that observation failure.

Automated verification: plugin 146 tests, serial runtime 112 tests and repository 47 tests passed. Python lint used the
repository's CI-pinned Ruff `0.17.1`; JavaScript syntax and portability checks passed.

## Human Windows-client trident follow-up (2026-10-16 JST)

During the ongoing normally authenticated Windows-client check on the same pinned RakNet server, the user reported:

1. `use continuous`, followed by `use stop`, throws the selected trident.
2. The observer sees both the projectile and a trident remaining in the Bot's hand.
3. Reselecting the hotbar with `hotbar 2` or another slot clears the stale hand image.

This is evidence of incomplete held-equipment rendering synchronization, not evidence that BDS created a second item.
Actual inventory counts and projectile state were not captured for this reproduction, so duplication is not ruled out
solely by the visual recovery. The earlier pickup/drop matrices do not cover this throw sequence.

A separate in-memory session replay equipped an item, delivered a native `inventory_slot` update emptying the selected
slot, and then delivered an all-empty plugin hotbar observation. The runtime's selected inventory slot and held item
became empty, but it sent no empty-hand `mob_equipment` announcement after those updates. Source inspection agrees:
native inventory handlers update the held-item cache, while the plugin-observation handler announces equipment only
when the observation differs from that cache. A native update arriving first can therefore suppress the subsequent
announcement. This explains a possible path to the live symptom; the replay did not use a real server or establish
the actual packet order of the reported throw. Hotbar reselection explicitly sends equipment, consistent with the
reported visual recovery.

This original report preceded the equipment update fix and the `use for <ticks>` addition. The later implementation
and server-side validation are in [the follow-up record](ITEM_USE_SYNC_VALIDATION.md). A later human Windows RakNet
check on `29e782a` confirmed successful throwing, clearing of the held trident image, and `for` from both commands/forms.

## Remaining gates

- The reported trident hand-image persistence is confirmed resolved in the human Windows RakNet check; the latest
  form order/ASCII label was also confirmed on `d095b18`. Other item/transport rendering remains a separate check.
- Broader item variants, shield behavior, other plugins, sustained multi-Bot traffic and pickup under low TPS remain
  separate checks; this is not a new Endstone/BDS compatibility or Xbox achievement claim.

## Full inventory product drops (Windows RakNet live run, 2026-10-22 JST)

The new product observation is `endbot:inventory-v2`. It keeps the existing typed NBT encoding and item registry
validation, with 36 main/hotbar items and five equipment items in offhand/head/chest/legs/feet order. Changed-only
publication and periodic correction retain the existing `hotbar-sync-*` settings and defaults. The observer stays
strictly read-only; fixture item creation/assignment below is isolated to the opt-in probe.

The JSON snapshot is split into 40 KiB byte chunks, base64 encoded inside UUID/sequence/part-count envelopes. The
runtime keeps at most one incomplete snapshot per session, ignores stale sequences, rejects conflicting fragments,
and applies all 41 items only after complete decoding. Every packet remains below 60 KiB, including the event name
and length prefixes. A snapshot over 2 MiB is refused before any packets are sent; item data is never truncated.
The previous valid cache remains until a valid authoritative correction arrives, and BDS still validates requests.
A stress fixture puts a 32 KiB NBT string in each of all 41 slots. With a 36-character UUID, Node measured
1,349,346 snapshot bytes, 33 fragments and a maximum 54,751-byte packet payload, including the event name,
length prefixes and a maximum safe-integer sequence number.
This exercises fragmentation beyond the old single-packet bound, rather than assuming nine-slot
payload sizes scale safely. These bounds limit synchronization, not BDS's inventory capacity.

Legacy normal `inventory_transaction` requests use window 0 slots 0-35, window 119 slot 0, and window 120 slots 0-3.
They carry exact old/remaining/ejected items without stack network IDs. Each successful send predicts that slot;
the next valid observation corrects a canceled/rejected request. Selected-hand and offhand changes announce equipment.
Item counts are consumed in increasing main/hotbar slot order, then offhand and armor. `drop all` includes the helmet;
its previously inconclusive spike result does not remove it from the product operation.

The `--inventory` runner now additionally sends real `bot RakNetSpike drop ...` commands against independent disposable
fixtures. It checks every main slot, equipment, selected slot and ejected items against authoritative BDS observations,
including exact NBT and total ejected counts even when actors merge. It persists before/expected/after evidence in
ignored `product-drop-results.json`. Unchanged or contradictory state cannot be called accepted.

Run: locked Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, reusable sandbox, `build/inventory-drops-1`.
Every case matched the expected authoritative BDS inventory and ejected items.

| Product command case | Live result |
| --- | --- |
| Nonselected slot 12 whole stack; slot 36 named/enchanted/damaged tool | Accepted: 5 stone; the tool ejected with its name, enchantment and damage NBT |
| `drop stone 7`, crossing hotbar/main stacks in order | Accepted: 3 then 4 from the lower slots first |
| `drop stone 99`, fewer items available | Accepted: all 5 dropped; reply "drop stone x5 requested (asked for 99)" |
| `drop minecraft:stone`, all matching stacks with another item untouched | Accepted: 2 + 4 stone, other item kept |
| `drop offhand`, `drop head`, `drop chest`, `drop legs`, `drop feet` independently | Accepted, all five (helmet included) |
| `drop all`, all 36 main/hotbar slots plus offhand and all four armor slots | Accepted: 41 slots emptied, every stack ejected |

Reproduce with `python scripts/raknet_spike.py --inventory --server-dir <sandbox> --output build/<fresh-directory>`.
Native declaration/client hints, forms (including drop-all cancel/confirm), Japanese replies, group batch summaries,
real-client equipment rendering and protection/cancellation for wider slots remain live reviewer checks. The earlier
pickup results above are historical and do not validate these new selectors. World achievement history and actual
Xbox achievement unlocks remain explicit manual/E2E gates. No new Endstone/BDS support claim is made.

## Legacy slot exchanges and product swap/hold (Windows RakNet live runs, 2026-10-22 JST)

Prepared for the same locked pair only. No Python suite or BDS run was performed in the preparation sandbox.
Run both matrices in one disposable-world smoke with a fresh ignored output directory:

```text
python scripts/raknet_spike.py --inventory-stack --inventory --server-dir <sandbox> --output build/<fresh-directory>
```

The stack spike now has 31 independent cases. Four new `legacy-swap` cases send a normal `inventory_transaction`
with exactly two window-0 container actions: source old->destination item and destination old->source item. They use
the fixture's complete item observation, including metadata/NBT/restrictions, without stack network IDs. They never
predict the product cache. Before/expected/after snapshots, both source items, responses, packet violations and
disconnect evidence remain in `inventory-stack-results.json`. Acceptance requires matching authoritative full
inventory, equipment, selected slot and absence of drops, with no disconnect or packet violation. Silence alone
cannot imply acceptance; legacy exchanges have no correlated stack response.
In a combined run the spike runs first, then deactivates its fixture observer so product item ejections are observed
only by the product fixture. A product failure cannot prevent the spike evidence from being collected.

Runs `build/inventory-moves-1` (`--inventory --inventory-stack`) and `build/inventory-moves-2` (`--inventory`) on the locked pair and reusable sandbox. Every case matched the authoritative BDS inventory and selected slot. The combined run then timed out in the later per-Bot radius step ("server confirmation pending"); the `--inventory` rerun passed that step and completed, so it was not reproduced.

| Spike case | Fixture / operation | Live result |
| --- | --- | --- |
| `legacy-move-10-to-1` | Main 10 stone x5 to empty hotbar 1 | Accepted |
| `legacy-swap-10-2` | Main 10 stone x5 / hotbar 2 dirt x3 | Accepted |
| `legacy-move-1-to-36` | Hotbar 1 stone x5 to empty main 36 | Accepted |
| `legacy-swap-identical-counts` | Main 10 stone x5 / hotbar 2 stone x3; counts exchange | Accepted |

Product swap/hold are implemented provisionally on that path. Product observation remains read-only. The runtime
predicts both slots after sending; the next complete BDS observation can restore a rejected swap. Selection and
selected-stack changes re-announce normal held equipment. Equipment movement/equip is deferred.
The `--inventory` matrix also issues the real product commands below and records exact item/NBT, all slots,
equipment, selected slot and zero ejections in `product-inventory-results.json`, before and after a periodic correction.
The main hold fixture has an occupied selected slot and duplicate main matches to verify lowest-slot selection.

| Product case | Authoritative expectation | Live result |
| --- | --- | --- |
| `bot RakNetSpike swap 10 2` | Stone x5 and dirt x3 exchange; selected hotbar 1 unchanged | Accepted |
| `bot RakNetSpike swap 1 36` | Named/enchanted/damaged tool moves to main 36; selected hotbar 1 becomes empty | Accepted |
| `bot RakNetSpike hold apple` | Select hotbar 3, before hotbar 5 or main 10; inventory unchanged | Accepted |
| `bot RakNetSpike hold diamond_pickaxe` | Lowest main match 14 exchanges with occupied hotbar 1; main 18 unchanged | Accepted |

If the spike rejects these exchanges, discard the provisional product operation before committing. Automated
serializer/prediction tests establish neither BDS acceptance nor achievement preservation. Japanese errors/replies,
native command hints, forms and echoed commands, group partial failures, real-client equipment rendering and
protection/cancellation remain live/manual gates. World achievement history and actual Xbox achievement unlocks
remain explicit manual/E2E gates; no new compatibility claim is made.
