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
- [ ] Dogfood:
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
- [x] Operator pre-check on the published rc.3 Windows bundle (informational,
  2026-09-25): `setup --fresh --controller <GamerTag> --apply`, basic Bot
  control, a non-allowlisted human is rejected with `allow-list=true` and
  joins after `allowlist add`, Bots join regardless of the allow-list, and
  the client shows no achievements-disabled warning. Not yet exercised: a
  second account, another device, joining from outside the LAN, and the
  achievement unlock. Findings folded into the next section.

### Remaining changes before the final candidate (decided 2026-09-26)

- [ ] **`/bot` from the server console.** The console is trusted and gets the
  full command surface. A console `spawn` without coordinates places a new Bot
  at the world spawn point; position-relative forms such as `tp me` explain
  that they need a player.
- [ ] **Controllers and the BDS allow-list.** `setup --fresh` adds each
  `--controller` GamerTag to `allowlist.json` (a fresh BDS ships
  `allow-list=true` with an empty list, which rejected the operator in the
  rc.3 pre-check). `setup --existing` never edits the allow-list; `doctor`
  warns when `allow-list=true` and a controller is missing from it.
- [ ] **Slimmer bundles** (measured on rc.3: Windows zip 171 → about 117 MB,
  Linux tar.gz 283 → about 170 MB): python-build-standalone
  `install_only_stripped` (drops debug symbols, the bulk of Linux
  `libpython`/`python3.12`), and drop what the runtime never loads — the
  `typescript` package pulled in by `jsp-raknet`, Node's npm/corepack/headers,
  Python's pip/ensurepip/tcl-tk, and the unused RakNet native modules if a
  real-BDS smoke confirms they are not needed.
- [ ] **Build provenance attestations** for every release asset
  (`actions/attest-build-provenance`), so anyone can verify with
  `gh attestation verify` that a bundle was built by this repository's
  workflow from a given commit.

### Final candidate and gates

The final candidate is built once, already versioned `0.1.0`, and the exact
bytes that pass the gates are what gets published — no rebuild after the
evidence is captured.

- [ ] `scripts/set_version.py 0.1.0` on `main` after the changes above; run
  `release-candidate.yml` (clean-consumer E2E with update/rollback on both
  platforms gates `assemble`); attach the artifacts to a **draft** GitHub
  release `v0.1.0`.
- [ ] Live matrix on those exact artifacts (needs a human Xbox client):
  normal Xbox human auth unchanged; a non-allowlisted human is rejected with
  `allow-list=true` while an allowlisted human and a Bot both join; GamerTag
  pending → XUID binding with `/bot` working from the client and from the
  console; a second Xbox account that is not a controller cannot use `/bot`;
  a second device on the LAN can join; bound controller after reconnect; Bot
  has member (not operator) permissions; invalid local token / wrong issuer /
  wrong key / local-bot-auth disabled negative cases. Joining from outside the
  LAN depends on the operator's network and is not a release gate.
- [ ] **Xbox achievement gate with capture.** A normal Microsoft/Xbox human
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
- [ ] Publish the draft `v0.1.0` unchanged (tag = the built commit), then
  re-verify checksums and a clean install from the published assets.

### After 0.1.0 (direction)

- 0.2.0: a runtime-download installer becomes the primary distribution. The
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
