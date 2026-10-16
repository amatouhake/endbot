# Timed item use and equipment synchronization

Implementation/validation follow-up for BOT-003 and BOT-007 (2026-10-16 JST). Baseline remains Endstone
`0.11.13+endbot.1`, official Windows BDS `1.26.52.3` (build `51798905`), protocol `2193`,
`bedrock-protocol` `3.60.1`, Node.js `24.17.0` and Python `3.12.10`. No lock or core-patch changes.

## Agreed behavior

- `/bot Alice use for <ticks>` holds use for 1–72000 runtime input ticks and releases automatically. Input runs at
  20 Hz; this is independent of the plugin's server-tick observation schedule and actual vanilla completion conditions.
- Existing once/continuous/interval/stop controls remain. `interval` is repetition spacing; `for` is duration.
  Only `use for` is implemented. Other actions, combined interval/duration syntax, repeated charged shots and
  item-count/completion conditions are deferred in [the backlog](BACKLOG.md).
- The Use item form adds `for` and reuses its numeric ticks field. No automatic item-specific charge duration is chosen.
- `use stop`/global stop release early, including firing a charged item when vanilla rules permit. Hotbar selection
  releases the previous item and cancels timed use. Death, dimension change and disconnect discard it. Reissuing
  timed use replaces the old hold/deadline; reissuing unlimited continuous use does not release an existing charge.
- Expiry stops only use; movement and independent actions continue. Timed input is not persisted/replayed on reconnect.
- Hold duration starts when the first air-use transaction is emitted. A pending block interaction can defer that
  start without shortening the hold. Stop before start discards the pending use without releasing an unstarted item;
  empty-hand timed use remains a no-op and does not wait for a later pickup.

## Equipment and observation changes

Native inventory slot/content updates and plugin observations converge through one equipment announcer. Its last
announced slot/item is separate from the transient inventory cache. A native update emptying the selected slot therefore
sends empty equipment even when a later plugin snapshot already matches the cache. Equivalent announcements are
suppressed; explicit hotbar selection still forces one. An authoritative observation also reselects from the cache
if a delayed self-equipment packet changed only the held-item view. Incoming equipment for other entities or
non-inventory windows (including offhand) is ignored by the main-hand handler.

The plugin reads all nine hotbar slots at `hotbar-sync-interval-ticks` (default 4, range 1–1200) and sends changed
observations plus unchanged corrections at `hotbar-sync-resend-ticks` (default 20, range 1–72000). Resends round up to a
read boundary. The SDK/NBT/JSON read work still occurs every scan; suppression reduces sends and runtime decoding.
Block runtime-ID lookups, including non-block results, are cached per plugin instance with a bounded cache. Snapshot
state is pruned to online local Bots and reset on join. Human inventories are excluded.

Periodic unchanged corrections are necessary: when the runtime predicts a drop that BDS rejects, the authoritative
inventory has not changed. With defaults, correction can take about one second at 20 TPS. Higher intervals trade
responsiveness for less work; neither setting disables normal native inventory/equipment updates. Settings live in
`endbot.toml` for managed instances and are generated into the plugin config on start. See [operations](OPERATIONS.md).

## Automated checks

Regressions cover exact hold/release tick counts, one release at expiry, retained movement, command replacement,
stop/global stop, hotbar reselection, death/dimension cleanup, unchanged continuous commands, native-empty then identical
snapshot ordering, delayed self-equipment recovery, nonselected slots, changed counts, rejected drops, unchanged-send
suppression/resends, block-palette caching, config bounds/generation, form-to-command parsing and real wire serialization.

The complete runtime suite (119 tests), plugin suite (150), CLI suite (289, five skipped) and repository contract suite
(47) passed, alongside CI-pinned Ruff `0.17.1`, JavaScript syntax, portability and diff checks. Plugin and CLI wheels
built successfully. Safe/unsafe server.properties preflight fixtures were accepted/rejected respectively; that check
does not certify world history or achievements. Raw reports and test identities remain ignored under `build/`.

## Windows live matrix

From a standalone clone with the locked patched Endstone and Endbot installed in a dedicated Python environment:

```text
python scripts/raknet_spike.py --item-use --inventory --mining --output build/item-use-sync-raknet
python scripts/raknet_spike.py --item-use --inventory --mining --transport nethernet --output build/item-use-sync-nethernet
```

The opt-in fixture prepares inventory/platforms only in a new disposable Survival world. It observes BDS inventory,
projectile actors and incoming equipment packets; no fixture code is installed in the product. Source hashes and exact
runtime versions are recorded by the runner. BDS is acquired through Endstone, not redistributed.

Initial RakNet and NetherNet `--item-use --inventory` runs passed the full smoke, including the following without
reconnecting between fixture preparation and item use:

| Case | Observation |
| --- | --- |
| `use for 40`, trident | BDS slot became empty; thrown-trident actor observed; BDS received empty-hand equipment after use |
| `use for 40`, bow | Arrow actor observed; arrow count changed from 5 to 4; bow remained held |
| `use for 50`, golden apple | Food count changed from 3 to 2; timed input ended |
| Natural pickups | Blocks, food in a nonselected slot, merged stack and named/enchanted/damaged tool dropped correctly |
| Native drop cancellation | Rejected drop preserved BDS inventory/loot; periodic correction allowed subsequent one/stack drops |

The native BDS console parser accepted `use for`; this is stronger than Python parser coverage alone. Remaining smoke
covered radius, movement/jump, reconnect, controller policy, negative local authentication and shutdown. World-history
inspection and actual Xbox unlocks remain separate evidence; these runs had no human participant or Xbox unlock.

Final combined `--mining --item-use --inventory` runs also passed on both transports. Every source hash recorded by
those runners matched the final implementation. In addition to the item matrix above, mining covered bare-hand
dirt/stone, pickaxe stone with durability/loot, consecutive targets, waiting/resume/stop, look changes, hotbar restart,
native event cancellation preserving block/tool, and death/stop notices. Clean shutdown, authentication negatives and
scoped world-history inspection completed. These automated observations do not close the human rendering gate.

## Small idle cost sample

Each initial transport run measured the production publisher around SDK reads, typed NBT/JSON serialization,
comparison and any packet-send call. One stationary Bot held two golden apples in one occupied hotbar slot; the other
eight slots were empty. Each three-second sample used default 4/20 settings:

| Transport | Reads | Sends | Payload bytes sent | Mean publish time | Maximum publish time |
| --- | ---: | ---: | ---: | ---: | ---: |
| RakNet | 15 | 3 | 705 | 173 µs | 214 µs |
| NetherNet | 15 | 3 | 705 | 164 µs | 215 µs |

The final combined runs repeated the same idle sample: RakNet mean/max 204/253 µs and NetherNet 261/446 µs, again
15 reads, three sends and 705 payload bytes each. Those two final runners ran concurrently on the same host. The timing
variation is another reason to treat this as a small observation rather than a capacity benchmark.

That is 80% fewer observation sends than sending on every scan. These bytes are the application packet payload,
excluding transport/compression overhead. Timing excludes scheduler iteration, runtime decoding, client rendering and
other server work. It is not a before/after CPU benchmark, a large-NBT test, a sustained multi-Bot load test or a TPS
guarantee. Do not extrapolate the small idle sample into a supported Bot-count limit. The read interval stays at 4 by
default to preserve pickup responsiveness; operators can tune both intervals.

## Human Windows-client follow-up (2026-10-16 JST)

On implementation commit `29e782a`, the normally authenticated Windows client on the pinned RakNet server confirmed
successful trident throwing and that the trident disappeared from the Bot's hand. The user also confirmed `for` worked
from both typed commands and the Use item form. This supplies human rendering evidence for the reported trident issue;
it does not establish the other item or transport gates.

The same check reported two form presentation issues. The `for` choice was last because it had been appended to the
existing modes; the agreed order is now `once`, `for`, `continuous`, `interval`, `stop`. The screenshot showed `≈` as a
missing glyph in the ticks label. The label now uses ASCII `20 = 1 second` and shorter wording. The user visually
rechecked commit `d095b18` on the same Windows RakNet server and confirmed the revised order and label.

## Remaining human/broader gates

- Bow/food and other inventory rendering on human clients, and trident rendering over NetherNet, remain separate
  from the confirmed RakNet trident case and Use item form recheck.
- Hunger-dependent food, charged-item effects/enchantments, tool breakage, low TPS, large NBT, other plugins and
  sustained many-Bot load remain broader checks. This change makes no new compatibility or Xbox achievement claim.

## Review corrections (2026-10-16 JST)

Two boundary cases were reproduced with regression tests that failed before the fixes:

- A wire-decoded self `mob_equipment` update for `offhand` could overwrite the runtime's main-hand state, cancel
  active use and send that item back as inventory equipment. The handler now checks the window before touching
  selection, held use or announcements; normal inventory equipment updates still work.
- A pending block interaction occupied the first tick of `use for 1`, so no air-use start was emitted before expiry
  sent a release. Timed use now retains its requested duration until the first air-use transaction starts. Tests
  cover two deferred ticks followed by complete one-/three-tick holds and exactly one release, plus stop before
  start and empty-hand cancellation. Existing once/interval/continuous behavior remains covered.

The full runtime suite passed 123 tests after these corrections. JavaScript syntax, portability and diff checks passed.
Fresh `--item-use` smokes also completed on both RakNet and NetherNet: trident throw/empty equipment, bow/arrow
consumption and golden-apple consumption passed, followed by the runner's lifecycle, negative-authentication and
shutdown checks. All source hashes recorded by those runners matched the corrected implementation.
These packet/order boundary cases use the pinned wire serializer in the regression suite; they are not claims of
human-client or live offhand/interaction-overlap validation.
