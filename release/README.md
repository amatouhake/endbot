# Release preparation

Generate metadata only from a clean, reviewed release commit:

```bash
python3 scripts/generate_compatibility_manifest.py dist/compatibility-manifest.json --endbot-version X.Y.Z
```

The compatibility manifest distinguishes current-artifact validation state from historical evidence. A current patched
package does not inherit human/Xbox achievement observations from an earlier patched-package revision merely because it
uses the same upstream Endstone/BDS pair. Historical M0/M2 evidence remains recorded with the patched Endstone package
against which it was actually observed (`0.11.11+endbot.1`). The current package `0.11.15+endbot.1` has its own
2026-10-30 human/Xbox observation in `docs/COMPATIBILITY.md`, recorded in the docs rather than in the build-time
manifest.

Release-candidate workflow input must exactly match `runtime/package.json`; the Python plugin and CLI versions must be its
PEP 440-equivalent form. `scripts/check_release_version.py` enforces that contract before any candidate is assembled.

Both Endstone wheels in the candidate (Linux `manylinux` and Windows `win_amd64`) are built by
`.github/workflows/release-candidate.yml` from the same `endstone.lock` + `patches/endstone/` inputs: the
`linux` and `windows` jobs each build, inspect, and install-test their platform wheel, then each builds the
pure-Python plugin and CLI wheels from the same commit plus its own self-tested operator bundle
(`scripts/build_bundle.py`, toolchain pinned in `packaging/toolchain.lock.json`). The `assemble` job
combines both wheels with the Linux-built plugin and CLI wheels (exactly one of each), both operator
bundles, the runtime/config tarballs, the optional UI pack (`scripts/build_ui_pack.py`, named by the pack's own
manifest version), license, manifest, and checksums into the single candidate.
The Windows wheel is no longer a local build.

Both platform jobs also build the native Luau runner using
`node runner/build.mjs --out build/runner-stage` and run `node --test` in `runner/`.
Windows uses the MSVC developer environment and CMake/Ninja; Linux uses its installed
C++ toolchain and CMake/Ninja. Each `build_bundle.py` invocation requires
`--runner-dir build/runner-stage`. The staging directory is never uploaded separately:
bundles contain `app/<version>/runner/endbot-luau-runner[.exe]`, `api.luau`, and
`licenses/LUAU_LICENSE.txt` / `NLOHMANN_JSON_LICENSE.txt`, listed in the bundled
`THIRD_PARTY_NOTICES.md`. Local bundle builds may omit the option. Bundle self-tests
require the runner and smoke-run a params-only NDJSON script without BDS; only local
builds without a runner pass `--allow-missing-runner`. Scripting remains off by default.

Bundle assembly and self-tests require `raknet-native` to load after production dependency pruning.
The consumer E2E workflow runs both `nethernet` and `raknet` on Windows and Linux, including fresh setup,
Bot join/resume, clean stop, and synthetic same-code update/rollback. RakNet is exercised with UDP 7551
occupied. Passing those jobs is artifact evidence; it does not replace human/Xbox gates or deliberately
update the compatibility lock. 

The eventual release job must add built artifact checksums and include the licenses/notices described in
`THIRD_PARTY_NOTICES.md`. It must not include BDS and must not publish solely because CI is green.
See `docs/ARCHITECTURE.md`, "Manual install from release-candidate artifacts", for the candidate contents and the
manual Linux/Windows installation; the operator path is `docs/INSTALL.md`.

Every file in the candidate (bundles, wheels, tarballs, UI pack, manifest, `SHA256SUMS`) receives a GitHub build
provenance attestation in the `assemble` job (`actions/attest-build-provenance`). Anyone can verify a downloaded file with
`gh attestation verify <file> --repo amatouhake/endbot`; the attestation binds its SHA-256 to this repository, the
`release-candidate.yml` workflow, and the commit it ran on. Publish every attested file unchanged, the
`endbot-ui-*.mcpack` included: players download the UI pack from the release on its own.

## Release checklist

Do not tag or publish until every item holds. A green build never stands in for a manual gate.

1. **Scope.** List what is merged since the last tag. If `endstone.lock`, `patches/endstone/`, the runtime
   dependencies or `scripts/endstone-conan.profile` changed, run the
   [compatibility change gate](../docs/COMPATIBILITY.md#compatibility-change-gate) first; otherwise say that the pair
   is unchanged and which earlier human/Xbox observation stands.
2. **Manual gates.** Run the human checks for the new features on a release candidate built by
   `release-candidate.yml`, and summarize them under
   [validation status](../docs/COMPATIBILITY.md#validation-status): what was checked, on which candidate, and what was
   not run. Open an issue for each check that was not run.
3. **World history.** After the pass, `endbot doctor` reports `level.dat` with no creative or experiment history. Say
   whether an actual Xbox achievement unlock was part of the pass.
4. **Versions and install guide.** Bump the versions `scripts/check_release_version.py` checks (runtime, plugin, plugin
   module, CLI, CLI module) and update the candidate table and examples in `docs/ARCHITECTURE.md` ("Candidate
   contents") and the release files table in `docs/OPERATIONS.md`.
5. **Release notes.** Name breaking changes, what a rollback to the previous release needs, and the paths not
   validated yet.
6. **Candidate.** Build `release-candidate.yml` for the version from `main` after the release commit. The `windows`,
   `linux` and `assemble` jobs and both clean-consumer E2E legs pass.
7. **Draft.** Attach the exact candidate files and `SHA256SUMS` to a draft release, download them again, compare
   every digest and run `gh attestation verify` on each file.
8. **Publish** the tested draft unchanged.

Standing invariants at publication:

- `online-mode=true`, `allow-cheats=false`, no experiments or packs, and the normal Microsoft/Xbox path for people.
  Local-bot trust stays disabled in the patched Endstone default config; only `endbot start` enables it, pinned to the
  instance's own owner public key.
- BDS stays external and is never bundled.
- Scripting stays off after setup and for old configs without `[scripting]`.
- Both platform bundles carry the checksum-pinned Luau runner, `api.luau` and the Luau and JSON licence texts.
- An observation made on one patched package is never attributed to another.

The records of releases 0.1.0 to 0.7.0, with their run numbers and dates, are kept
[as they stood at 0.7.0](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/release/FINAL_CHECKLIST.md). Later releases record theirs in the GitHub release notes.
