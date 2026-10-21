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
Additional offline tests cover the runtime hook's native-ID handling and response
capture, plus all 16 fixture cases and conservative outcome classification.

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
BDS send native contents with stack IDs; what status 49 means; why the armor drop disconnects.

## Run and inspect

From a Windows shell with access to the dedicated environment and reusable
sandbox, run (PowerShell uses `$env:USERPROFILE` in place of `%USERPROFILE%`):

```bat
build\group-prototype-env\Scripts\python.exe scripts/raknet_spike.py --server-dir %USERPROFILE%\endbot-dev\sandbox\server --inventory-stack --output build/inventory-stack-spike-3
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
