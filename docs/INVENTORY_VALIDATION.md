# Picked-up hotbar items and drop synchronization

Source changes on Endstone `0.11.13+endbot.1`, official Windows BDS `1.26.52.3` (observed build `51798905`), protocol
`2193`, `bedrock-protocol` `3.60.1`, Node.js `24.17.0`, Python `3.12.10`. The lock and core patch series are unchanged.

## Implementation

The plugin reads all nine BDS hotbar slots at a configurable interval (four ticks by default) and sends changed
observations plus periodic unchanged corrections (20 ticks by default). Each bounded, UUID-scoped
observation is sent over the local Bot's existing connection using public `Player.send_packet`. The `script_custom_event`
envelope is a protocol-2193 pair of strings, not execution of scripts or an experimental/Beta API. The runtime resolves
item identifiers through that connection's server item registry, preserves typed NBT and updates its transient cache.
Block runtime IDs are observed through public `create_block_data`. No inventory assignment, item creation or removal
occurs in this product path. Normal equipment packets update the selected hand; BDS still validates item transactions.

Observations have a 60 KiB bound. Wrong-identity, partial, unknown-item or malformed observations leave good cached state
intact. Native inventory updates remain supported. Four ticks means about 0.2 seconds at 20 TPS, not an instantaneous
pickup guarantee. The matching plugin is needed; a runtime alone retains the reconnect recovery for missing native deltas.

## Automated checks

Python checks cover read-only access, all nine slots, server block palette lookup, Unicode, typed enchantment NBT,
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

- The reported trident hand-image persistence is confirmed resolved in the human Windows RakNet check. Other item
  rendering and the latest form order/ASCII label still need their own confirmation.
- Broader item variants, shield behavior, other plugins, sustained multi-Bot traffic and pickup under low TPS remain
  separate checks; this is not a new Endstone/BDS compatibility or Xbox achievement claim.
