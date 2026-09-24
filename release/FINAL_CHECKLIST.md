# Final release checklist (0.1.0)

Tracks what remains between pre-release `v0.1.0-rc.1` (candidate `b0c5cb3`)
and the final `v0.1.0` tag + GitHub Release. Do not create the final tag or
publish the final Release until every gate item below is satisfied.

## Must complete

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
- [ ] **Linux toolchain note in `docs/INSTALL.md`.** `npm ci` from the runtime
  tarball falls back to building `raknet-native` from source when no prebuild
  matches, which needs git + cmake + a C++ compiler alongside Node 22.
  Document this in the Linux section (found during rc.1 download-and-run
  validation).
- [ ] **Windows Endstone wheel built by CI.** rc.1 shipped a locally built
  `win_amd64` wheel (same lock + patches, verified install) alongside the
  CI-built Linux wheel. Extend `release-candidate.yml` with a Windows build
  job (inspect + paired-install equivalents) so the final release is fully
  CI-backed on both platforms.
- [ ] **Rebuild + re-verify the final candidate** from the exact final `main`
  SHA after the above land, and confirm checksums, manifest binding, and a
  clean install from the published assets before tagging.
- [ ] **Onboarding review (operator-gate feedback).** A fresh user-perspective
  setup succeeded end to end (venv + release wheels, runtime tarball +
  `npm ci`, fresh server bootstrap, `/bot ping`), but two TOML traps needed
  guidance: Windows paths must be single-quoted literal strings, and XUIDs
  must be strings, not numbers (both now documented in `docs/INSTALL.md`).
  Decide before release how far to go for plain BDS operators: at minimum
  consider extending `scripts/preflight.py` beyond `server.properties` to
  validate the plugin `config.toml` (TOML parses, `token-file` points at an
  existing file, allowlist entries are strings) and the `[local-bot-auth]`
  table (enabled + readable public key), so these fail fast with a clear
  message instead of a server-log traceback. Node.js/Python prerequisites
  are architectural and stay, but every manual step should be either
  documented or mechanically checked.
- [ ] **Live-check `allow-list=true`.** The gate ran with `allow-list=false`;
  `allow-list=true` (which also blocks Bot names unless allowlisted) has not
  been exercised with a human + bot join. Either test it or document it as
  untested.

## Explicitly deferred (recorded, not blocking 0.1.0)

- Same-session pickup inference (reconnect is the recovery path).
- Sneak ledge/collision simulation.
- Block breaking/mining.
- Endstone 0.11 autocomplete redesign / upstream Brigadier work.
- Stale sessions after a BDS *force-kill* may need a runtime restart; the
  graceful `stop` path and bounded reconnect are the supported mechanisms.
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
