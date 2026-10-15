# Bot behavior fixes — 2026-10-16 JST

Scope: [BOT-001, BOT-002, and BOT-003](BACKLOG.md). These are source changes against the existing locked baseline, not
a new release or compatibility claim. Endstone core patches, `endstone.lock`, and the support manifest are unchanged.

## Changes and evidence

- Loading: default requested radius reduced from the protocol library's implicit 10 chunks to 5. Operator configuration
  is `[runtime].chunk-radius` (integer 2..32), generated as runtime `chunkRadius`. The initial upstream request uses
  `client.viewDistance`; Endbot also requests the radius on spawn and dimension changes. BDS can clamp requests.
- Dimension changes: handle `change_dimension`, adopt its position, clear old targets/interactions and motion, send
  `dimension_change_ack` and loading-screen acknowledgements with the supplied ID, and resume input/gravity. Incorporate
  `set_entity_motion` for the Bot into its predictor; authoritative corrections replace prediction.
- Held use: put air-use interactions in the authoritative input tick, mark the initial press separately from held
  `simulation_tick` interactions, and release on stop or hotbar change. Server inventory updates replace stale held
  stacks. `once` and `interval` retain short press/release semantics. A block interaction defers simultaneous air use.

Protocol references: [Mojang player input](https://mojang.github.io/bedrock-protocol-docs/1.26.50/packets/player-auth-input-packet/),
[dimension change](https://mojang.github.io/bedrock-protocol-docs/1.26.50/packets/change-dimension-packet/), and
[Gophertunnel item interaction definitions](https://github.com/Sandertv/gophertunnel/blob/master/minecraft/protocol/inventory.go).
The installed, pinned protocol-2193 serializer is the wire-layout authority for these tests; newer protocol enum ordinals
must not be copied into this baseline.

## Automated checks

The new session regression tests initially failed on the original implementation and pass after the fixes. They
round-trip outgoing packets through the pinned serializer and cover sustained use, release, slot changes, consumption
updates, interval use, simultaneous block/air interactions, repeated dimension changes, jump after landing, and motion.
After the per-Bot radius extension: runtime tests 97 passed with `--test-concurrency=1`; plugin tests 75 passed;
repository tests 47 passed. The earlier CLI run had 264 passed and 2 skipped. Parallel test-file runs hit `EPERM` in
the unchanged Windows owner-key publication race test twice; serial test-file execution passed, including that test's
own concurrent child processes. This remains a Windows test/reliability observation, not an authentication change.
Python lint, JavaScript syntax, portability, and clean patch preparation against the locked Endstone revision passed.

## Windows real-server smoke

Environment: Endstone `0.11.13+endbot.1`, official BDS `1.26.52.3` Windows build `51798905`, protocol `2193`,
`bedrock-protocol` `3.60.1`, Node.js `24.17.0`, Python `3.12.10`. Endstone and the plugin came from checksum-verified
0.2.1 release wheels; plugin and runtime behavior changes were loaded from the working source. BDS was acquired through
Endstone's normal bootstrap.

The run used a fresh disposable Survival world, `online-mode=true`, `allow-cheats=false`, and `allow-list=true`.
A private test-only extension to the plugin supplied fixture items and sampled BDS inventory, actors, dimension, and
position on the server thread. A Bot reconnect ensured login inventory full-sync before each item test. This is not a
natural-pickup test and does not demonstrate a fix for the separate pickup-cache limitation. No fixture or diagnostic
code is part of the product. Logs, identities, keys, tokens, and worlds are excluded from tracked files.

RakNet observations:

| Check | Observed result |
| --- | --- |
| Held food use | Three golden apples became two during `use continuous`, before global stop |
| Charged trident release | After two seconds of held use and `use stop`, slot empty and BDS contained a thrown-trident actor |
| Nether transition | Bot fell 5.69 blocks during the observation window and walked 0.95 blocks |
| End transition | Bot fell 12.43 blocks and walked 0.87 blocks |
| Return to Overworld | Bot fell 10.66 blocks and walked 0.95 blocks |
| Subsequent movement/jump | 3.87 blocks of horizontal movement and a sampled 0.49-block rise |
| Lifecycle | Same-UUID reconnect, despawn, and clean server/runtime shutdown passed |
| Negative local authentication | Wrong key, issuer, audience, and local auth disabled each rejected without spawn |

Initial Nether fall testing at Y=110 was inconclusive because the test destination could be solid terrain. The successful
run used Y=160 above the Nether roof; the End used Y=100, and the return Overworld test used Y=160. These are sampled
movement observations, not measurements of vanilla jump height or exhaustive collision behavior.

A second RakNet run on the final interaction implementation repeated food/trident use and all three transitions,
verified two Bots online concurrently, and passed reconnect, negative authentication, shutdown, and history inspection.
It requested a custom radius of 3 on initial connection, reconnect, and each transition; BDS replied with radius 5.
The default was therefore set to 5 rather than the initially proposed 2. Two-Bot connectivity is a smoke check, not a
sustained load or resource-usage measurement.

NetherNet real-server validation was not completed in this session. An attempted discovery timed out; the existing
Minecraft Windows client was also observed owning UDP 7551, and a subsequent precheck skipped another attempt while
that port was occupied. The client was left running. Repeat with the discovery port available and the normal packaged
server-selection checks; transport-independent unit coverage does not replace this gate.

## Per-Bot radius command validation

The shared default remains `[runtime].chunk-radius`. `/bot <name> radius <2-32>` stores an optional override in the
immutable Bot's profile; `radius default` removes it. Old profiles lacking the field still inherit the current shared
default. Live requests update without reconnecting; the chosen request is reused after dimension changes and reconnect.
`radius` and `status` distinguish the requested value/source from the last server-confirmed value.

On the same Windows baseline, the updated `scripts/raknet_spike.py` completed on a new isolated world:

- Native BDS parsed `/bot <name> radius 12` and `radius default` and reached the authorized plugin command service.
- BDS acknowledged 12 after the override and 5 after default reset.
- The override survived reconnect; another concurrently online Bot still inherited 5 while the first requested 12.
- Movement (3.52 blocks), a sampled jump (1.25 blocks), same-UUID reconnect, despawn, negative authentication controls,
  clean shutdown, and checked world-history flags passed in that run. No human/Xbox gate was claimed.

An earlier radius run passed the radius checks but stopped at the terrain-dependent movement check (0.20 blocks);
that attempt is not counted as a full smoke pass. The successful run used the final script, which takes its movement
baseline after radius/reconnect checks. Offline setting, restart/rename persistence, default inheritance after a shared
configuration change, invalid values, write-failure isolation, and changes during connection initialization have automated
coverage. Sustained generation workloads, actual loaded-chunk/resource measurements, and NetherNet remain separate gates.

After the successful run, the existing world-history inspector found Survival and all checked cheat, creative, and
experiment history flags false. This is scoped configuration/history evidence only. No human client participated and
no actual Xbox achievement unlock was attempted or observed.

## Runtime controller authorization validation

Synthetic-identity tests cover immediate permission attachment updates and command refresh calls, console-only
administration, local Bot rejection (including legacy UUID entries), pending authenticated enrollment, persistent
identity revocation, offline regrant without rebinding, legacy allowlist precedence, failed atomic writes, and stale
queued command cancellation across revoke/regrant. Native player references remain on the server thread.

The extended `scripts/raknet_spike.py` passed on the locked Windows Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`
(build `51798905`) over RakNet on 2026-10-16 JST. Native BDS registered `botcontrollers`, accepted quoted pending names,
added/removed them, refused to promote the online local Bot, and retained pending/revoked policy after BDS restart.
The run also passed radius commands, movement/jump, reconnect/despawn, negative authentication, clean shutdown and
checked world-history flags. Raw logs/policy remain ignored private test output, not tracked player data.

The subsequent command-feedback run exercised the actual `python -m endbot_cli` against the same pinned baseline on
a new isolated world. Production `dispatch_requests` forwarded correlated request IDs to the real BDS plugin; the
dedicated smoke runner supplied the forwarding loop, while a separate supervisor test covered real child stdin.
CLI add/list/remove printed the plugin results, unknown-offline removal exited 1, and `doctor --live` showed the plugin's
pending-state reason. Native `/bot` move, stop, jump and look returned operation-specific messages. The run also passed
the remaining movement/radius/reconnect/negative-auth/history smoke and shut down cleanly. An earlier attempt reached
the new CLI replies but failed doctor because its dedicated Python lacked the CLI's declared `cryptography` dependency;
that attempt is not a full pass. Installing that dependency in the ignored test environment resolved the setup failure.

Automated checks also exercise concurrent request correlation, timeout/unknown outcome, no automatic mutation retry,
old-session rejection, malformed replies, console-only result dispatch, and agreement between saved diagnostics and
plugin policy decisions. `doctor` fails on corrupt policy and explains grants/revocations instead of only bootstrap state.

These runs had no human participant. Game chat rendering and command visibility remain manual gates. With a normally
authenticated human connected, manually add/revoke access and
check both `/bot` execution and command visibility without rejoin, then repeat after reconnect/restart. Also try an
ordinary controller and operator invoking `botcontrollers`: both must be refused. Already-started operations may
finish; a revoked queued operation must not start. This is a separate gate from Xbox achievement unlocking.

## Remaining manual/E2E gates

- Two normally authenticated human clients must agree on the Bot's visible position after repeated dimension changes.
- Repeat the held-use/dimension-transition matrix over NetherNet. The radius, movement/jump, reconnect, controller
  and mining smoke passed over NetherNet on 2026-10-16 ([mining validation](MINING_VALIDATION.md)).
- Validate portal travel, live knockback after transitions, landing/jumping in each destination, and repeated returns.
- Check ordinary food when hunger permits eating, bow charge/release, and held use after dimension changes.
- Measure sustained multi-Bot load and effective chunk-loading/resource cost beyond the two-Bot and custom-radius smoke.
- Complete the applicable human login and actual Xbox achievement gates before making broader release claims.

For manual item checks, put the item in a known hotbar slot (reconnect after a world pickup if necessary), select it with
`/bot Alice hotbar <1-9>`, then run `/bot Alice use continuous`. Allow enough time to eat or charge; use
`/bot Alice use stop` to release. Observe the actual inventory/projectile and BDS state, not only the command reply.
