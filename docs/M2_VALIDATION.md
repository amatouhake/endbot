# M2 validation record

This record describes the M2 implementation smoke run completed on 2026-09-18 JST. It used official BDS `1.26.51.1`
build `51061372`, protocol `2193`, and patched Endstone `0.11.11+endbot.1`. The behavior under test is contained in the
focused M2 commit series beginning with `d3e0382` and ending with `91d2b64`; later documentation-only commits do not
change that runtime result.

No server, world, identity, key, token, packet capture, or machine-local path is retained in the repository.

## Live results

- First spawn created a stable hidden UUID and a BDS player; respawn of an offline profile reused it.
- Ten distinct profiles connected concurrently. Movement and lifecycle commands addressed only the named UUID/session;
  a failing or stopped Bot did not alter another profile.
- BDS-observed position changed under movement input. Look/rotation synchronization and jump were observed at the
  server boundary.
- A targeted attack produced server-observed knockback on the target player. Missed attacks remained harmless swings.
- One-shot and continuous/stop held-item use transactions were accepted without a protocol violation or disconnect.
  Empty-hand use remains an intentional no-op.
- Endstone's `Actor.teleport` path changed position/rotation and performed a cross-dimension teleport without dispatching
  vanilla `/tp`. The command parser and world adapter cover caller, player, coordinates, facing, relative coordinates,
  aliases, and namespaced dimensions in automated tests.
- An unexpected kick entered bounded reconnect and returned online with the same UUID. Explicit reconnect also retained
  the UUID. After the session-closure fix, the old transport left before the replacement connected and BDS no longer
  assigned a duplicate-name suffix.
- `despawn` disabled reconnect and retained the profile. `resume` restored the same UUID without requesting relocation.
  `forget` rejected a live profile, removed an offline Endbot registration, and a later spawn of the released name
  received a new UUID.
- Live rename preserved the UUID, reconnected under the new login name, removed the old lookup, and was reversed for
  cleanup. Automated tests additionally reject case-insensitive collisions and online-player impersonation.

## Persistence finding

BDS restored the same UUID's position and inventory after reconnect, so M2 keeps BDS authoritative for player/world
state. Endbot persists identity, name, and desired lifecycle only. Server rotation updates were observed and fed back
into runtime input, but an isolated reconnect assertion for exact yaw/pitch was not recorded. Cross-dimension teleport
worked live; cross-dimension state restoration after a clean reconnect was not separately isolated. Those narrower
checks do not justify a competing Endbot location database.

## Automated and safety boundary

The repository suites cover command grammar, authorization, loopback-token IPC, profile concurrency/corruption,
lifecycle/reconnect timers, rename/forget, multi-Bot isolation, action scheduling, packet serialization, teleport
adapter behavior, packaging, patch application, compatibility metadata, and portability. A final run and its exact
totals are reported with the milestone handoff.

The live server reported Survival mode and an empty pack stack. The scoped preflight passed `online-mode=true` and
`allow-cheats=false`; no experiments, Beta APIs, GameTest, operator grant, or vanilla cheat command was introduced.
Endbot has no robust in-repository parser for this BDS version's world-history fields, so those fields remain unknown for
this smoke rather than being declared safe by inference.

## Remaining manual M2 gate

The completed M0 human `/bot ping` and Xbox-achievement result still applies to the unchanged baseline, but it does not
prove the expanded M2 surface. A human operator must still:

1. join from an unmodified Windows Bedrock client through Microsoft/Xbox auth using an explicitly allowlisted UUID/XUID;
2. exercise representative `/bot` spawn, list/status, teleport, action/stop, reconnect, rename, despawn, and resume flows;
3. confirm no operator or vanilla command permission was granted and cheats remain disabled;
4. perform a version-aware post-save world/history inspection; and
5. unlock a still-locked vanilla Survival Xbox achievement in that session.

Native Windows runtime operation is designed for but was not executed in this record. Linux is the live-validated M2
host. The server is intentionally stopped after autonomous validation; follow the normal setup documentation to create
fresh local secrets and configure the real operator allowlist before the manual gate.
