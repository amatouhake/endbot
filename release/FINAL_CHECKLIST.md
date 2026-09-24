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

- [ ] **`look at` resolves the Bot by identity UUID** instead of player name
  (duplicate-name ghosts picked the wrong entity).
- [ ] **Windows Endstone wheel built by CI.** rc.1 shipped a locally built
  `win_amd64` wheel. `release-candidate.yml` gains a Windows build + inspect
  + paired-install job and an assemble job, so both platforms share one CI
  provenance.
- [ ] **S1 — upstream `bedrock-protocol` audit.** On a branch: move to exact
  `bedrock-protocol@3.60.1` (`transport: 'nethernet'` instead of the fork's
  `raknetBackend`), drop `prepare-minecraft-data.js` and the custom protocol
  2193 schema patch if upstream `minecraft-data` (>= 3.117.0) is correct,
  rerun runtime tests, then real-BDS smoke of login, movement, PlayerAuthInput
  transactions, interact, drop, inventory. Record what upstream cannot express
  (the fork's `nethernetServerKeyPin` / `onNetherNetServerTrust`), and note the
  Node `>=24` requirement plus the git-sourced `prismarine-xbox-services`
  dependency for bundling.
- [ ] **S2 — BDS allow-list spike.** Locate where BDS rejects a login that is
  not on the allow-list (after `_validateLoginPacket`; Endstone holds the
  `AllowList&` but does not model it) and whether a verified local Bot can be
  exempted inside the existing local-bot auth hook without a new core hook.

### Phase 1 — contracts (decide once, document, then build on them)

- [ ] **App/data layout.** Replaceable app (private Python, patched Endstone,
  plugin, bundled Node + runtime) versus persistent Endbot data (owner key,
  control token, controller bindings, Bot profiles, server-identity state)
  versus operator-owned BDS (worlds, `server.properties`, `allowlist.json`,
  `permissions.json`, packs). Updates never touch the latter two.
- [ ] **Config contract.** One operator config; generated state kept apart
  from operator-edited files.
- [ ] **NetherNet server trust.** BDS regenerates its NetherNet DTLS identity
  on every start, so a persistent TOFU pin cannot survive restarts. Decided
  direction: loopback-only connection plus a supervisor-managed pin that is
  reset on each supervised BDS start, so the fork-only trust API can shrink
  or go.
- [ ] **Local Bot allow-list semantics.** Humans keep vanilla allow-list
  behaviour. Preferred: exempt only identities that passed Endbot owner
  signature verification, if S2 shows this fits the existing auth hook.
  Otherwise the plugin keeps `allowlist.json` in sync through the vanilla
  `allowlist add/remove` console commands on spawn / rename / forget, rather
  than widening the Endstone core delta with a new hook.
- [ ] **Artifact shape.** Per-platform bundle: standalone CPython 3.12,
  bundled Node + runtime with pinned, prebuilt dependencies, and an `endbot`
  launcher.

### Phase 2 — implementation

- [ ] Controller authorization: pending GamerTag → XUID/UUID binding on first
  authenticated join; remove / re-enroll; GamerTag changes keep the binding.
- [ ] Local Bot allow-list behaviour per the contract, with negative tests
  (wrong issuer, wrong signature, local-bot-auth disabled, ordinary Xbox
  player, non-allowlisted human all keep vanilla rejection).
- [ ] Land the S1 migration (or rebase a minimal fork delta on 3.60.1 if S1
  shows upstream cannot carry it safely).
- [ ] Runtime platform artifact: bundled Node and `node_modules` built in CI;
  no `npm ci` or native addon build on the operator machine.
- [ ] Private Python bootstrap with patched Endstone + plugin preinstalled.
- [ ] `endbot setup` (fresh or existing BDS; keys, token, config, controller
  enrollment, preflight), `endbot doctor` (absorbs and extends
  `scripts/preflight.py`: plugin TOML parses, token file exists, identifiers
  are strings, `[local-bot-auth]` enabled with a readable key, BDS / runtime
  reachability, compatibility pair), `endbot start` / `stop` (runtime then
  Endstone/BDS, PID / log / exit capture, server-identity pin lifecycle).
- [ ] Existing-BDS migration: dry-run → backup → planned-changes listing →
  explicit apply; never loses worlds or operator config.
- [ ] `docs/INSTALL.md` and `README.md` rewritten around the new entry point.

### Phase 3 — final candidate and gates

- [ ] CI builds Linux and Windows, then one assemble job emits the platform
  bundles, manifest, and `SHA256SUMS`. No locally built file enters the
  release.
- [ ] Dogfood: fresh Windows machine, clean Linux consumer, and migration of
  an existing BDS copy.
- [ ] Live matrix on the exact final candidate: normal Xbox human auth
  unchanged; human allow-list behaviour unchanged (`allow-list=true`
  exercised with a human and a Bot); verified local Bot allow-list positive
  case; invalid local token / wrong issuer / wrong key / local-bot-auth
  disabled negative cases; GamerTag pending → XUID binding; bound controller
  after rename and reconnect.
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
  Microsoft/Xbox path for humans, local-bot trust disabled by default.
- BDS stays external (never bundled).
- Historical M0/M2 evidence stays attributed to `0.11.11+endbot.1`; the
  current package never inherits it.
