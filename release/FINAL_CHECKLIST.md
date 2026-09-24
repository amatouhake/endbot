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
  - [ ] `endbot update` from one published candidate bundle to the next, and
    `--rollback`.
- [ ] Live matrix on the exact final candidate (needs a human Xbox client):
  normal Xbox human auth unchanged; a non-allowlisted human is still rejected
  with `allow-list=true` while an allowlisted human and a Bot both join;
  invalid local token / wrong issuer / wrong key / local-bot-auth disabled
  negative cases; GamerTag pending → XUID binding with `/bot` working;
  bound controller after reconnect; Bot has member (not operator) permissions.
- [ ] **Xbox achievement gate with capture.** Manifest observations
  (`actual_xbox_achievement_unlock_observed`,
  `m2_actual_xbox_achievement_unlock_observed`) stay `false` until a normal
  Microsoft/Xbox human client unlocks a *still-locked* vanilla Survival Xbox
  achievement in the exact final-candidate environment, with capturable
  evidence (in-game notification video, client/BDS versions, timestamps,
  candidate SHA + artifact SHA-256 list, populated human XUID, Survival
  no-warning UI, post-session world-history fields). The earlier
  operator-reported `強靭なお腹` unlock has no capture and is explicitly
  **not** counted; use a different, still-locked achievement.
- [ ] Rebuild + re-verify from the exact final `main` SHA: checksums,
  manifest binding, and a clean install from the published assets before
  tagging.

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
