# Inventory stack request spike

## Scope and versions

Prepared on 2026-10-22 for the existing locked baseline only:

- Endstone upstream 0.11.13, patched package `0.11.13+endbot.1`.
- BDS `1.26.52.3`, build 51798919, protocol 2193 (`endstone.lock`).
- `bedrock-protocol` 3.60.1 (`runtime/package-lock.json`); its `1.26.51`
  serializer is the protocol-2193 schema used by the runtime.
- Windows, RakNet native transport. No version/support manifest changes.

## Observations

The 22 builder tests passed against the installed pinned serializer and
deserializer. They cover take/place/swap in both directions between main
inventory and hotbar, cursor take/place with a request-relative stack ID,
whole/partial drop, offhand/armor selectors, legacy non-selected main-slot
transactions, and invalid input. This proves wire encoding only.
Additional offline tests cover the runtime hook's native-ID handling, open/close,
disconnect capture and replay prevention, plus 27 independent fixture cases,
conservative outcome classification and reconnect recovery. These extensions
have not been run live.

Follow-up preparation checks: the 28 Node builder/hook tests pass. The full
runtime suite reports 183/185 passing; the two existing concurrent identity
publication tests fail on sandbox `EPERM` hard-link creation. Runtime syntax
checks and `ruff check scripts tests plugin/endbot cli/endbot` pass. Both
requested Python unittest suites could not run because `python` is unavailable
on the sandbox PATH; the probe test module now contains 13 tests for caller
validation. No BDS process was started for this preparation.

Codex prepared the probe but could not start Python/BDS from its sandbox. Claude then ran it twice live
(2026-10-22 JST, Windows RakNet, reusable sandbox, locked pair above):
`build/inventory-stack-spike-1` and `-2`, the second after making the runtime hook read the main inventory the
session has tracked since join. Both runs stopped at the helmet case (see below); the chest/legs/feet cases did
not run.

| Requested operation | Result | Evidence |
| --- | --- | --- |
| Main 10 to hotbar 1; swap main 10/hotbar 2; main 10 drop; legacy drop from main 10 | **Not sent** | The Bot client never held a native copy of main-inventory slot 10 after the SDK fixture set it: no `inventory_slot`/`inventory_content` reached the Bot for it. |
| Hotbar 1 to main 36 (take, place, cursor move) | **Not sent** | The hotbar item is known only through the plugin's read-only hotbar observation, which has no native stack network ID. The probe refuses to invent one. |
| Offhand drop | **Rejected** | Sent with the native stack ID 13 that BDS had sent for the offhand. `item_stack_response` status **49** (neither 0 `ok` nor 1 `error`); offhand unchanged, nothing dropped. |
| Head (helmet) drop | **Bot disconnected** | No `item_stack_response`; BDS logged "Player disconnected: RakNetSpike" about a second later and the next fixture timed out. Cause not established. |
| Chest, legs, feet drop | Not run | The disconnect ended the run. |

Conclusion so far: on this baseline the Bot client does not receive native inventory contents with stack network
IDs for server-side changes outside the hotbar/offhand, so `item_stack_request`, which must name the stack IDs it
moves, cannot be built for main-inventory slots from the client's own state. Product `hold`/`swap`/`drop <slot>`/
`drop all` therefore cannot rely on `item_stack_request` as designed. Directions to evaluate next: whether a
legacy `inventory_transaction` from a non-selected slot works once the plugin observation covers all 36 slots
(item data without stack IDs); whether opening the inventory (`interact` open-inventory / `container_open`) makes
BDS send native contents with stack IDs; why source-slot validation failed (status 49); why the armor drop disconnects.

## Run and inspect

From a Windows shell with access to the dedicated environment and reusable
sandbox, run (PowerShell uses `$env:USERPROFILE` in place of `%USERPROFILE%`):

```bat
build/group-prototype-env/Scripts/python.exe scripts/raknet_spike.py --server-dir %USERPROFILE%\endbot-dev\sandbox\server --inventory-stack --output build/inventory-stack-spike-N
```

Use a **new ignored output directory** on every actual run. `--inventory-stack` records all independent cases, including rejected
and inconclusive requests. Raw artifacts stay ignored and must not be committed.

Like `mining_probe.py`, the isolated fixture plugin prepares disposable items
and terrain via the SDK. Once a case is ready, only the normal client packet
performs the operation. The server thread snapshots all 36 slots, five equipment
slots, selected slot, and ejected item actors; the observer records then removes
item actors to prevent re-pickup. No product plugin inventory writes are added.
Authentication, Survival, cheats and experiment settings are unchanged.

`inventory-stack-results.json` and `report.json` preserve before/expected/after
BDS snapshots, the request and native source item, raw decoded response packets,
and inventory updates. Response statuses are `ok` (wire code 0), `error` (1),
or the decoder's numeric value for any other code. Live runs have shown 49 (offhand drop). A request is classified accepted only when the expected BDS state matches
and its correlated response says `ok`; legacy transactions have no correlated
stack response and require the expected state and ejected items. An explicit
failure response is rejected; silence or contradictory evidence is inconclusive.
Missing native items/stack IDs are reported as not-sent, never acceptance.
The runner's top-level `ok` means its smoke sequence completed; inventory support
must be assessed from the individual cases and their BDS evidence.

## Follow-up cases (live run 2026-10-22 JST, `build/inventory-stack-spike-3`)

Replace `N` above with a fresh run number. All 16 original cases remain;
the six open-inventory cases and five equipment legacy cases bring the total
to 27. Main/hotbar labels here are one-based; wire slot 9 means main slot 10.
Each case resets its own fixture; an open case never reuses another case's IDs.

| Case ID | Client operation after fixture setup | Result |
| --- | --- | --- |
| `open-take-10-to-1` | Open; `take` 5 stone from inventory slot 9 to hotbar slot 0; close | **Not sent**: BDS answered `container_open`/`container_close`, but sent no native contents or stack IDs |
| `open-place-10-to-1` | Open; `place` 5 stone from inventory slot 9 to hotbar slot 0; close | **Not sent** (same) |
| `open-swap-10-2` | Open; `swap` inventory slot 9 (5 stone) / hotbar slot 1 (3 dirt); close | **Not sent** (same) |
| `open-drop-main-stack` | Open; `drop` all 5 stone from inventory slot 9; close | **Not sent** (same) |
| `open-drop-main-partial` | Open; `drop` 1 stone from inventory slot 9; close | **Not sent** (same) |
| `open-drop-item_in_off_hand` | Open; `drop` the shield from offhand slot 0; close | **Not sent** (same) |
| `legacy-drop-main-stack` | Normal `inventory_transaction`: window 0, slot 9 -> world, 5 stone | **Accepted**: slot emptied, 5 stone ejected |
| `legacy-drop-main-partial` | Normal `inventory_transaction`: window 0, slot 9 -> world, 1 stone (4 remain) | **Accepted**: 4 stone remain, 1 ejected |
| `legacy-drop-item_in_off_hand` | Normal `inventory_transaction`: window 119, slot 0 -> world, shield | **Accepted**: offhand emptied, shield ejected |
| `legacy-drop-helmet` | Normal `inventory_transaction`: window 120, slot 0 -> world, iron helmet | **Inconclusive**: the after-snapshot was not captured; rerun needed |
| `legacy-drop-chestplate` | Normal `inventory_transaction`: window 120, slot 1 -> world, iron chestplate | **Accepted**: slot emptied, chestplate ejected |
| `legacy-drop-leggings` | Normal `inventory_transaction`: window 120, slot 2 -> world, iron leggings | **Accepted**: slot emptied, leggings ejected |
| `legacy-drop-boots` | Normal `inventory_transaction`: window 120, slot 3 -> world, iron boots | **Accepted**: slot emptied, boots ejected |
| `drop-helmet`, `drop-chestplate`, `drop-leggings`, `drop-boots` | Native `item_stack_request` armor slot 0/1/2/3, each in its own case, last in the run | chest and feet **rejected** (status 49); head and legs **disconnected**: BDS sent `packet_violation_warning` (malformed, terminating, packet 147: "expected a valid ItemStackRequestId") |

The installed `runtime/node_modules/minecraft-data/minecraft-data/data/bedrock/1.26.51/protocol.json`
defines the open as `interact` action 6 (`open_inventory`), targeting the Bot's
own runtime entity ID, with `has_position=false`. Each open case records
`open.answered` and the complete `container_open` packet. It waits one second
for native contents, then uses screen selectors `inventory` (29), `hotbar` (28)
or `offhand` (34), with slot indices 9, 0/1 or 0 respectively. These are stack
container selectors, distinct from the inventory/equipment window IDs 0/119/120.
No new IDs are inferred from the SDK observation. Missing BDS open response,
contents or native IDs produces a recorded not-sent result. After a two-second
response window, `container_close` echoes the **BDS-provided** window ID and
window type, with `server=false`, even if the stack request could not be built.
A disconnected client cannot close its window.

For legacy cases the fixture publishes `stack-state.json.observation`: all 36
slots plus offhand/head/chest/legs/feet, using the unchanged `hotbar_snapshot`
item encoding in nine-slot batches. The hook decodes it through `decodeHotbar`
in matching batches. Identity and case ID must match. Metadata, block runtime
ID, typed NBT (including 64-bit values), restrictions and shield extra data
are retained; stack network IDs are absent. The transaction's container action
carries the exact old item and remaining item, followed by a world-interaction
action carrying the ejected count. Main slot 10 remains non-selected (selected
slot is hotbar 1). Product `endbot:hotbar-v1` and product inventory code are unchanged.

The per-case wire log captures native inventory updates and responses, all
other decoded packets during the case, raw `disconnect` reason/message/filtered
message, `play_status`, text, `kick`, and transport close information. Generic
packet capture occurs before disconnect handlers can remove listeners. The
runner persists BDS before/expected/after snapshots and dropped item actors
before recovery, then issues control `reconnect`, waits for `online`, and
continues. The new hook ignores an old command file after reconnect. Connection
loss during fixture preparation is also recorded and recovered. Disconnect or
stale fixture evidence cannot imply acceptance; an explicit correlated failure
still records rejection. Matching state includes the full item observation
(metadata/NBT as well as counts), selected slot and recorded ejected items.
Native armor requests run last.

Result: the legacy normal `inventory_transaction` (container slot -> world drop, carrying the exact item data
from the observation, no stack IDs) is accepted for non-selected main-inventory slots (whole and partial), the
offhand and chest/legs/feet; helmet needs a rerun. Opening the inventory does not make BDS send stack IDs, so
`item_stack_request` stays unusable for Bots on this baseline. The two disconnects were BDS rejecting the request
packet as malformed (alternate requests in the same run got status 49), which suggests the probe's request ID
sequence, not the armor slot, is malformed; this was not investigated further because the legacy path works.
Moving items between slots (`hold`/`swap`) has not been tried through a legacy transaction yet.

## Status 49

Mojang's protocol-2193 [ItemStackResponseInfo result enum](https://mojang.github.io/bedrock-protocol-docs/1.26.51/types/item-stack-response-info/)
names result 49 **`FailedToValidateSrcSlot`** (not independently checked) (`ItemStackNetResult`): source-slot
validation failed. This identifies the enum value; it does not establish which
source-slot check failed in the observed offhand request, or explain the helmet
disconnect. The raw numeric status remains in the artifacts.

## Open questions / gates

- Which direct take/place, cursor move, and combined-container selectors does
  this BDS actually accept? Are explicit inventory open/close packets needed?
- Do SDK fixture updates supply native stack network IDs for every container?
  The probe refuses to invent an ID for a non-empty item. A missing native ID
  requires investigating normal BDS synchronization before retrying.
- Does the server accept non-selected legacy drops and arbitrary equipment
  drops, and does it return codes other than 0/1?
- Verify cancellation/protection and real-client behavior before designing
  product hold/swap/drop operations. These builders are unused by product code.
- World achievement history and actual Xbox achievement unlocks remain explicit
  manual/E2E gates; serializer tests establish neither.
