# Final release checklist (0.1.0)

Tracks what remains between pre-release `v0.1.0-rc.1` (candidate `b0c5cb3`)
and the final `v0.1.0` tag + GitHub Release. Do not create the final tag or
publish the final Release until every gate item below is satisfied.

## Scope decision

rc.1 proved the stack works end to end, but a fresh operator-perspective setup
needed Python, a venv, patched Endstone, Node.js, npm, runtime config, plugin
TOML, and hand-entered XUIDs. 0.1.0 therefore ships only once Endbot can be
set up and run as one product by a plain BDS operator. Internals stay Python
(Endstone + plugin) and Node.js (runtime); packaging, identity enrollment, and
orchestration hide them instead of a pre-release rewrite.

0.1.0 targets:

- No source build, and no manual Python / Node.js / venv / pip / npm steps.
- Fresh-BDS and existing-BDS setup paths.
- Controller enrollment by Xbox GamerTag, bound to the stable XUID/UUID on
  first authenticated join.
- Verified local Bots do not need hand-maintained BDS allow-list entries.
- One `endbot setup | doctor | start | stop` entry point.
- CI-backed Linux and Windows release artifacts with one manifest and
  `SHA256SUMS`.
- Runtime on upstream `bedrock-protocol` instead of the 1.26.50-era fork where
  that is safe.

Not required for 0.1.0: GUI wizard, auto-update daemon, full Windows Service /
systemd integration, a true single executable, a Node runtime rewrite,
pickup inference, block mining, autocomplete redesign.

## Workstreams (dependency order)

### Phase 0 — spikes and independent fixes

- [x] **`look at` resolves the Bot by identity UUID** instead of player name
  (#12).
- [x] **Windows Endstone wheel built by CI** (#20): `release-candidate.yml`
  builds, inspects, and install-tests the `win_amd64` wheel next to the Linux
  wheel, and one assemble job publishes both.
- [x] **S1 — upstream `bedrock-protocol` audit**, landed in #15: exact
  `bedrock-protocol@3.60.1` + `minecraft-data@3.117.0`, no fork, no
  install-time schema rewrite, byte-level packet assertions unchanged, Node 24,
  `prismarine-xbox-services` pinned as an HTTPS tarball (no git at install).
  Live smoke on real BDS passed.
- [x] **S2 — BDS allow-list spike.** With `allow-list=true` and an empty
  `allowlist.json`, a Bot accepted by the local-bot path joins; the existing
  auth hook's result is already exempt. No new core hook (see
  `docs/OPERATIONS.md` section 4).

### Phase 1 — contracts

- [x] **App/data layout**, **config contract**, and **artifact shape**:
  `docs/OPERATIONS.md` (#13).
- [x] **NetherNet server trust**: no server identity pin. Loopback-only BDS
  connection plus the owner-signed, audience-bound, single-use, `cpk`-bound
  Bot token (`docs/SECURITY.md`, #15).
- [x] **Local Bot allow-list semantics**: humans keep vanilla allow-list
  behaviour; verified local Bots are already exempt (S2).

### Phase 2 — implementation

- [x] Controller authorization: GamerTag → XUID/UUID binding on first
  Xbox-authenticated join, revocation, reset (#17, #19).
- [x] Land the S1 migration (#15).
- [x] Runtime platform artifact and private Python bootstrap: per-platform
  bundles with pinned CPython 3.12 and Node 24, production `node_modules`
  built in CI, pruned `minecraft-data` (#22).
- [x] `endbot doctor` (#18), `endbot start` / `stop` / `console` /
  `controllers reset` with generated configs and a supervising process (#19),
  `endbot setup` (fresh / existing) and `update` / `--rollback` (#21).
- [x] Existing-BDS migration: dry-run → backup with SHA-256 manifest →
  planned-changes listing → explicit apply (#21).
- [x] `docs/INSTALL.md` and `README.md` lead with the bundle path (#21, #22).

### Phase 3 — final candidate and gates

- [x] CI builds Linux and Windows, then one assemble job emits both platform
  bundles, wheels, manifest, and `SHA256SUMS` (#20, #22).
- [x] Dogfood:
  - [x] Windows, CI-built bundle, operator commands only: extract →
    `endbot.cmd setup --fresh --apply` (BDS downloaded through Endstone) →
    `start` → Bot joins with `allow-list=true` → live smoke → clean `stop`.
    Ran on the development machine, not a clean one.
  - [x] Migration of an existing BDS copy (no `version.txt`, human allowlist
    entry, custom pack file, custom port): refused without a world-backup
    flag; with `--backup-worlds` it backed up 2,893 files, reinstalled BDS,
    kept operator files, and the existing world loaded with a Bot joining.
  - [x] Clean-consumer E2E in CI on every release candidate
    (`.github/workflows/bundle-e2e.yml`, gating `assemble`): each platform
    bundle on a fresh `windows-2022` / `ubuntu-22.04` runner with a PATH that
    exposes no Python, Node.js, npm, or pip — `setup --fresh --apply` (BDS
    through Endstone) → `doctor` → `start` → Bot joins through local-bot
    trust → `console` → `doctor --live` → clean `stop`. This replaces a
    hand-prepared clean machine and found a Linux-only bundle defect
    (relocated `libpython` not found by BDS) that is now fixed.
  - [x] `endbot update` / `--rollback` between real bundles: the published
    rc.2 → rc.3 Windows bundles (with the update fix) plus, on every
    candidate, the CI E2E updating to a renamed copy of the bundle, restarting,
    rolling back, and restarting on both platforms. This found that rc.2/rc.3's
    own `update` could not read real bundles (top-level directory, `app/current`,
    Linux symlinks and executable bits) — fixed; operators on rc.2/rc.3 reinstall.
  - [x] Another NetherNet host on the machine (a Minecraft world open to LAN):
    Bots connect only to the BDS advertising this instance's `server-name` /
    `level-name`, and `endbot start` refuses while another program holds UDP
    7551 (verified with a decoy host).
    Note (post-0.1.0, #37): expected `levelName` resolution changed after this gate —
    `worlds/<level-name>/level.dat` `LevelName` is now preferred over the `level-name`
    directory name (unit-tested; live decoy-host regression for the new resolution
    was not part of the 0.1.0 gate).
- [x] Operator pre-check on the published rc.3 Windows bundle (informational,
  2026-09-25): `setup --fresh --controller <GamerTag> --apply`, basic Bot
  control, a non-allowlisted human is rejected with `allow-list=true` and
  joins after `allowlist add`, Bots join regardless of the allow-list, and
  the client shows no achievements-disabled warning. Not yet exercised: a
  second account, another device, joining from outside the LAN, and the
  achievement unlock. Findings folded into the next section.

### Remaining changes before the final candidate (decided 2026-09-26)

- [x] **`/bot` from the server console.** (#30) The console is trusted and gets the
  full command surface. A console `spawn` without coordinates places a new Bot
  at the world spawn point; position-relative forms such as `tp me` explain
  that they need a player.
- [x] **Controllers and the BDS allow-list.** (#31) `setup --fresh` adds each
  `--controller` GamerTag to `allowlist.json` (a fresh BDS ships
  `allow-list=true` with an empty list, which rejected the operator in the
  rc.3 pre-check). `setup --existing` never edits the allow-list; `doctor`
  warns when `allow-list=true` and a controller is missing from it.
- [x] **Slimmer bundles** (#32; Windows zip measured 171.3 → 118.1 MB, Linux tar.gz about 165 MB) (measured on rc.3: Windows zip 171 → about 117 MB,
  Linux tar.gz 283 → about 170 MB): python-build-standalone
  `install_only_stripped` (drops debug symbols, the bulk of Linux
  `libpython`/`python3.12`), and drop what the runtime never loads — the
  `typescript` package pulled in by `jsp-raknet`, Node's npm/corepack/headers,
  Python's pip/ensurepip/tcl-tk, and the unused RakNet native modules if a
  real-BDS smoke confirms they are not needed.
- [x] **Build provenance attestations** (#29; verified with `gh attestation verify`, tampered file rejected) for every release asset
  (`actions/attest-build-provenance`), so anyone can verify with
  `gh attestation verify` that a bundle was built by this repository's
  workflow from a given commit.

### Final candidate and gates

The final candidate is built once, already versioned `0.1.0`, and the exact
bytes that pass the gates are what gets published — no rebuild after the
evidence is captured.

- [x] `scripts/set_version.py 0.1.0` on `main` after the changes above; run
  `release-candidate.yml` (clean-consumer E2E with update/rollback on both
  platforms gates `assemble`); attach the artifacts to a **draft** GitHub
  release `v0.1.0`. (#34; run 36189148021 from `b51fc3c`)
- [x] Live matrix on those exact artifacts (needs a human Xbox client):
  normal Xbox human auth unchanged; a non-allowlisted human is rejected with
  `allow-list=true` while an allowlisted human and a Bot both join; GamerTag
  pending → XUID binding with `/bot` working from the client and from the
  console; a second Xbox account that is not a controller cannot use `/bot`;
  a second device on the LAN can join; bound controller after reconnect; Bot
  has member (not operator) permissions; invalid local token / wrong issuer /
  wrong key / local-bot-auth disabled negative cases. Joining from outside the
  LAN depends on the operator's network and is not a release gate.
  **Done 2026-09-27 with scope recorded in `docs/ACHIEVEMENTS.md`:**
  - Exercised: Xbox auth, `--fresh` allow-list enrollment, GamerTag → XUID binding, client and console `/bot`, and the
    four negative auth cases.
  - Not exercised: a second account and a second device. Allow-list rejection is covered by the rc.3 pre-check, and
    non-controller refusal by tests.
- [x] **Xbox achievement gate with capture.** (2026-09-27: `Archer`, recorded in
  `docs/ACHIEVEMENTS.md`) A normal Microsoft/Xbox human
  client unlocks a *still-locked* vanilla Survival Xbox achievement in the
  exact final-candidate environment, with capturable evidence (in-game
  notification video, client/BDS versions, timestamps, candidate SHA +
  artifact SHA-256 list, populated human XUID, Survival no-warning UI,
  post-session world-history fields). The earlier operator-reported
  `強靭なお腹` unlock has no capture and is explicitly **not** counted; use a
  different, still-locked achievement. The compatibility manifest inside the
  artifact is build-time data and keeps its observation flags `false`; the
  captured evidence is recorded against the artifact SHA-256 list in
  `docs/ACHIEVEMENTS.md` and the release notes instead of rebuilding.
- [x] Publish the draft `v0.1.0` unchanged (tag = the built commit), then
  re-verify checksums and a clean install from the published assets.
  **Done 2026-09-27:**
  - `v0.1.0` was published at `b51fc3c`.
  - The downloaded assets pass `SHA256SUMS` and `gh attestation verify`.
  - The clean-consumer E2E passed from the published Windows zip.

### 0.1.1 patch release (decided 2026-10-08)

Scope: only the post-0.1.0 fixes on `main` — #36 (`transport=nethernet` enforced by fresh setup and doctor),
#37 (expected `levelName` from `level.dat` `LevelName`), #38 (spawn reports connecting/reconnecting instead of
online). `endstone.lock` and `patches/endstone/` are unchanged from 0.1.0, so no new Endstone/BDS pair is claimed.

- [x] `scripts/set_version.py 0.1.1` on `main`; `release-candidate.yml` with `0.1.1` (clean-consumer E2E with
  update/rollback on both platforms); attach the exact artifacts to a **draft** release `v0.1.1`.
  (#42; run 37688550948 from `337da22`)
- [x] Bot-side live check on the exact Windows bundle (2026-10-08): `setup --fresh` writes `transport=nethernet`; a
  world whose `level.dat` `LevelName` differs from `level-name` (Japanese display name vs `Bedrock level`) is
  selected and a Bot joins, while rolled-back 0.1.0 on the same world stays disconnected with the expected
  level-name mismatch (this control run replaced a separate decoy host; selection rejection is unchanged code with
  unit coverage); `endbot update` from the published 0.1.0 bundle is refused without `transport` (0.1.0 kept
  current) and succeeds with `--force` once it is restored; repeated spawn reports `already online`.
- Human/Xbox gates are **not re-run** for 0.1.1 (decided 2026-10-08): the lock and patches are identical and the
  fixes do not touch authentication or world flags. The release notes cite the 0.1.0 gate and say so explicitly.
- [x] Publish the draft unchanged, then re-verify checksums from the published assets. (2026-10-08: published
  `SHA256SUMS` identical to the candidate's; Windows bundle attestation verified)

### 0.2.0 release — opt-in RakNet transport (decided 2026-10-08)

Scope: `[server] transport = "nethernet" | "raknet"` (#43). NetherNet stays the default; RakNet is opt-in and
documented as experimental. `endstone.lock` and `patches/endstone/` are unchanged, so no new Endstone/BDS pair is
claimed. Evidence and open items: `docs/RAKNET_VALIDATION.md`.

- [x] CI consumer E2E on both transports × Windows/Linux, including update/rollback (branch run 37688666654).
- [x] `release-candidate.yml` with `0.2.0` on the merged `main`; attach the exact artifacts to a **draft** `v0.2.0`.
  (run 37727279011 from `aec5789`; all four consumer jobs passed)
- [x] Bot-side live check on the exact Windows bundle (2026-10-08): `setup --fresh --transport raknet` wrote and
  verified `transport=raknet`; with another process holding UDP 7551, `start` succeeded, a Bot joined over RakNet,
  `doctor --live` passed, the stop was clean, and the world history flags stayed clean; a published-0.1.1 instance
  updated to 0.2.0 with `endbot.toml` byte-identical, doctor passed with `transport=nethernet`, and its Bot resumed
  over NetherNet.
- [x] **Human gate on RakNet (operator, before publication):** 2026-10-09 on the draft's Windows bundle (the RakNet
  instance above): a Windows client joined with a normal Xbox-authenticated login alongside the Bot, first-join
  controller binding succeeded, `/bot ping`, `/bot Alice jump once`, and `/bot Alice tp me` worked from the client,
  the settings screen showed no achievements-disabled notice, and after a clean stop every `level.dat` history flag
  was false with `GameType=0`. **Actual Xbox achievement unlock: not run for 0.2.0** (operator decision, 2026-10-09);
  the release notes say so.
- [x] Publish the draft unchanged, then re-verify checksums from the published assets. (2026-10-09: tag `v0.2.0`
  = `aec5789`; every published asset matches the candidate `SHA256SUMS`; both bundle attestations verified)

### 0.2.1 release — Endstone v0.11.13 / BDS 1.26.52.3 (decided 2026-10-09)

Scope: the validated compatibility update (#48): Endstone `v0.11.13`, BDS `1.26.52.3`, protocol `2193`, the
context-only patch rebase, and the `operators` doctor WARN. Compatibility evidence is in `docs/COMPATIBILITY.md`
(gate run on the branch candidate, including an observed Xbox achievement unlock).

- [x] `release-candidate.yml` with `0.2.1` on `main`; attach the exact artifacts to a **draft** `v0.2.1`.
  (run 37854937634 from `f62a059`; all four consumer jobs passed)
- [x] Bot-side live check on the exact Windows bundle (2026-10-09): fresh setup acquired BDS `1.26.52.3` and a Bot
  joined over NetherNet; a published-0.2.0 RakNet instance updated to 0.2.1 backed up 2886 files, moved BDS to
  `1.26.52.3` with `endbot.toml` byte-identical, and its Bot resumed with `doctor --live` passing and clean world
  flags; doctor showed `operators` PASS without operators and WARN with one.
- Human/Xbox gate: covered by the 2026-10-09 compatibility gate on the branch candidate (same lock, patches, and
  runtime); not re-run on the 0.2.1 bytes, and the release notes say so.
- [x] Publish the draft unchanged, then re-verify checksums from the published assets. (2026-10-09: tag `v0.2.1`
  = `f62a059`; every published asset matches the candidate `SHA256SUMS`; both bundle attestations verified)

### After 0.1.0 (direction)

- 0.2.0: opt-in direct-loopback RakNet transport — released 2026-10-09 (see `docs/RAKNET_VALIDATION.md`).
- 0.3.0: a runtime-download installer becomes the primary distribution. The
  release then carries only Endbot's own artifacts (patched Endstone wheel,
  plugin, CLI, runtime source; about 25 MB on Windows and 50 MB on Linux), and
  the installer fetches CPython, Node.js, and third-party packages from their
  official sources with pinned hashes, so no third-party binary is
  redistributed by this project.

## Explicitly deferred (recorded, not blocking 0.1.0)

- Same-session pickup inference (reconnect is the recovery path).
- Sneak ledge/collision simulation.
- Block breaking/mining.
- Endstone 0.11 autocomplete redesign / upstream Brigadier work.
- The earlier silent runtime exits (3 events, all near BDS taskkill churn, no
  crash evidence, never reproduced under supervision) remain unexplained but
  unreproduced; the gate runtime now runs supervised with PID/exit-code
  capture. Any recurrence under supervision is a release blocker.

## Standing invariants (must hold at publication)

- `online-mode=true`, `allow-cheats=false`, no experiments/packs, normal
  Microsoft/Xbox path for humans. Local-bot trust stays disabled in the patched
  Endstone default config; only `endbot start` enables it, pinned to the
  instance's own owner public key.
- BDS stays external (never bundled).
- Historical M0/M2 evidence stays attributed to `0.11.11+endbot.1`; the
  current package never inherits it.
