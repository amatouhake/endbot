# RakNet validation — 2026-10-07–08

Status: **Windows human/Bot feasibility provisionally passed; CLI integration implemented as opt-in.**
This evaluates Endbot's existing accountless local-bot login over RakNet.
It does not evaluate vanilla BDS with Microsoft-authenticated headless bots.

## Observed result

A fresh native Windows world ran official BDS acquired through Endstone, the
existing patched Endstone, and only the Endbot plugin. The runtime dialed its
configured `127.0.0.1:<server-port>` over RakNet with the same owner-signed,
audience-bound, single-use, client-key-bound local token. There was no Endstone
core change, token-format change, protocol-library upgrade, or schema patch.

| Component | Observed |
| --- | --- |
| Endbot base revision | `3439ded333b4f253556e96ec04913dbf143ca087` plus the RakNet spike changes |
| Patched Endstone | `0.11.12+endbot.1` |
| BDS | `1.26.51.1`, Windows build `51061361` |
| Bedrock protocol | `2193`, configured by the pinned `1.26.51` schema |
| bedrock-protocol | `3.60.1` |
| Backend | `raknet-native` (`1.2.3` installed) |
| Node / Python | `24.17.0` / `3.12.10` |
| Server settings | `online-mode=true`, `allow-cheats=false`, Survival, `allow-list=true` with an empty list |

The Windows Endstone wheel used for the isolated run had SHA-256
`06493056583984df87a466af0660798c450649de9db274d13e3429fd4f755471`.
The report records source hashes; raw logs, keys, profiles, and worlds stay
in the ignored experiment directory and must not be committed.

**Build-number boundary:** `endstone.lock` records build `51061372`, while the
Windows binary obtained by the pinned Endstone acquisition path reports
`51061361`, also recorded in the earlier Windows smoke in `COMPATIBILITY.md`.
Both have version `1.26.51.1`; do not describe this run as an exact-build match
to the lock. The lock, release manifest, and support status are unchanged.

The isolated run completed:

- Console `/bot` spawn, `Accepted local bot`, `Player connected`, `Player Spawned`, and runtime online.
- Movement observed by the plugin's BDS world API: **3.30 blocks horizontally**.
- A one-shot jump observed by the same BDS API: **1.25 blocks upward**.
- Explicit reconnect with the same immutable UUID, followed by a second BDS spawn.
- Despawn, offline state, and clean BDS/runtime shutdown.
- Wrong owner key, wrong issuer, wrong audience, and a valid local token with
  local-bot auth disabled: each sent a Login, received a server kick, never
  spawned, and produced no accepted-local-bot entry. The disabled case ran
  after a real server restart, not just a config edit.
- After shutdown: `commandsEnabled=0`, `cheatsEnabled=0`,
  `hasBeenLoadedInCreative=0`, `GameType=0`, `experiments_ever_used=0`,
  `saved_with_toggled_experiments=0`, and no additional experiment keys.

A second fresh run with the final runner, again using only Endbot, passed the
same positive/negative gates and recorded the same movement/jump measurements.
Runtime tests (80), repository tests (46), syntax, Python lint, and portability
checks also passed. The live runs are independent of those unit-test results.

The preliminary run in the ordinary development Python environment included
an extra debug plugin and is not the isolated evidence above. Two runner
issues found before the successful isolated run were corrected: instance-path
construction and polling the asynchronous console spawn.

## Human-client provisional gate — 2026-10-07

A fresh isolated Survival world hosted a Windows client alongside a local Bot.
The client was Windows GDK `1.26.52`, protocol `2193` (as shown in the client's
connection details). The server remained BDS `1.26.51.1` build `51061361` with
patched Endstone `0.11.12+endbot.1`; no lock or support declaration changed.

Server-side evidence confirmed a human join with a populated XUID, first-join
controller binding, Bot coexistence, and a subsequent human rejoin. The human
was not accepted through local-bot authentication. The operator reported that
`/bot ping`, Bot teleport to the caller, and a one-shot Bot jump all worked
normally, and the settings screen showed no achievements-disabled notice.
The server log corroborated issuance of the three commands.

After stopping BDS and the runtime cleanly, the parsed world-history fields
matched the pre-session snapshot: `commandsEnabled=0`, `cheatsEnabled=0`,
`hasBeenLoadedInCreative=0`, `GameType=0`, `experiments_ever_used=0`,
`saved_with_toggled_experiments=0`, with no additional experiment keys.
Private logs, controller bindings, and exact source hashes remain outside
tracked files; no player identifiers are included in this public record.

The operator explicitly chose to end this as a **provisional feasibility pass**
and defer actually unlocking an achievement, which could require a long session.
The actual Xbox achievement gate is **not run**, and its observation remains
`false`. The absent UI notice and safe world-history flags are configuration
evidence; they do not establish an actual Xbox unlock. Further engineering
work can proceed with that limitation recorded.

An earlier client failure used the default port `19132` instead of the prepared
`19136`, so it is excluded from RakNet compatibility results. The BDS startup
also prints `TRANSPORT TYPE ERROR` claiming NetherNet-only support, although
the observed Bot and Windows human both successfully joined over RakNet.
Record that upstream warning without treating this narrow live success as a
general or future-version support guarantee.

**Still untested/deferred:** allow-list rejection for humans, an actual new Xbox
achievement unlock, Linux RakNet live compatibility, unexpected BDS restart recovery,
or sustained/multi-Bot load. Packaged Windows startup/resume evidence is recorded below.

## Reproduce from a standalone clone

Use Node.js 24+ and a dedicated Python environment containing the exactly
pinned patched Endstone wheel, this clone's `plugin/endbot`, and `tomlkit`.
Obtain/build the wheel through the existing [installation](INSTALL.md) or
[Windows development](WINDOWS_DEV.md) workflow. A normal unpatched Endstone
wheel cannot substitute for it. The runner refuses extra plugin entry points.

From the repository root, with that environment's Python active:

```text
npm ci --prefix runtime
python scripts/raknet_spike.py --output build/raknet-spike-1
```

`--node <executable>` selects an explicit Node executable when necessary.
The output must be a new, git-ignored directory. The runner downloads BDS via
Endstone's normal integrity-checked acquisition path and refuses a startup
version different from the lock. It never adopts an existing server or world.
It chooses separate local server/control ports, generates private auth material,
starts the runtime and server, performs the checks above, stops both, and writes
`report.json`. Failure records the last stage; raw details stay in `server.log`
and `runtime.log`. Native `raknet-native` must load; install/build its native
addon if the platform has no usable prebuild. Linux execution remains unvalidated.

Terrain can affect the movement/jump checks. A failure there is not evidence
of a transport/auth failure: inspect the last successful stage and BDS position
observations, then use a fresh output directory for the next experiment.

## Operator CLI integration — 2026-10-08

The current CLI accepts `setup --fresh --transport raknet`. It records `[server] transport = "raknet"`
in `endbot.toml` and writes matching BDS settings while preserving `online-mode=true` and `allow-cheats=false`.
Existing-server setup infers and preserves the BDS transport when the option is omitted; an explicit mismatch
fails before edits. Old configs without `server.transport` retain NetherNet.

Config generation passes that selection to runtime JSON. RakNet reads the exact BDS `server-port`, rejects
invalid ports, omits NetherNet advertisement identity, and skips the UDP 7551 pre-start ownership check.
Doctor rejects a transport mismatch. Endstone patches, authentication, dependencies, and the lock are unchanged.
Both bundle assembly and self-tests require the native RakNet addon to load after pruning.

A Windows development bundle built from the current CLI/plugin/runtime passed the clean-consumer harness
with system Python, Node, npm, and pip removed from PATH. RakNet passed fresh setup, doctor, local-auth Bot
join, console dispatch, live doctor, and clean stop. A synthetic next version containing the same code then
passed update and rollback, including desired-online Bot resume and clean stop after each restart.
UDP 7551 was occupied independently throughout all three RakNet sessions. This tests application replacement
and state preservation; it does not validate a different BDS pair or rollback to the older published CLI.

The development archive SHA-256 was
`44aec7902ba8e836892024ab7923fe129f4f12f592b7768979569edcf065bd02`.
Its bundled Node/Python versions were `24.21.0` / `3.12.14`, with `raknet-native` `1.2.3`.
The archive retains the development `0.1.0` version label and is not a published release.
Logs, worlds, keys, and state remain ignored. BDS was acquired through Endstone, never bundled.

After the final clean RakNet stop, direct parsing of `level.dat` again showed all six safety/history flags
false, `GameType=0`, and no extra experiment keys. This remains separate from the deferred Xbox unlock gate.

The same Windows bundle's NetherNet consumer rerun timed out during Bot join: discovery saw another local
Minecraft world's advertisement but none matching BDS. Replacing only `config.js` and `protocol-session.js`
inside the private extracted bundle with their pre-change `3439ded` versions reproduced that same discovery
error; those files were then restored and BDS/runtime stopped cleanly. The NetherNet consumer rerun therefore
remains blocked by the local LAN-host environment, not a passing regression result. No unrelated world was closed.

CLI tests (259, including two skips), repository tests (46), plugin tests (73), Python lint, syntax, and
portability checks passed. Runtime tests passed all 80 with `--test-concurrency=1`; the default parallel run
hit an existing short-timer lifecycle test once (retry-budget resume still reconnecting at its assertion).
No transport implementation change was made in response to that timing-sensitive assertion.

The consumer CI matrix now covers both transports on Windows and Linux. That workflow has not been run
for these uncommitted changes; Linux RakNet and its native bundle remain unverified.

## Runtime behavior and next decision

Runtime JSON accepts `transport: "nethernet" | "raknet"`; omission keeps
NetherNet. Both configuration loading and direct session construction reject
unknown transports and non-loopback hosts. RakNet skips NetherNet discovery
even when advertisement names are present, and uses `skipPing=true` and
`followPort=false` to keep the exact configured address/port.

NetherNet remains the default while the remaining compatibility gates are open. Next:

1. The Windows human/Bot coexistence gate passed provisionally. Actual Xbox
   unlock validation is deferred until a dedicated achievement/release gate;
   do not inherit historical achievement observations for this RakNet path.
2. Run the same live smoke on Linux and verify the native backend in both bundles.
3. Validate unexpected server restart recovery, sustained/multi-Bot load, and reviewed release artifacts
   before deliberately changing defaults or support claims. Fresh/existing setup has unit coverage;
   Windows packaged fresh setup and synthetic update/rollback have the live evidence above.
