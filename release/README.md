# Release preparation

Generate metadata only from a clean, reviewed release commit:

```bash
python3 scripts/generate_compatibility_manifest.py dist/compatibility-manifest.json --endbot-version X.Y.Z
```

The compatibility manifest distinguishes current-artifact validation state from historical evidence. A current patched
package does not inherit human/Xbox achievement observations from an earlier patched-package revision merely because it
uses the same upstream Endstone/BDS pair. Historical M0/M2 evidence remains recorded with the patched Endstone package
against which it was actually observed (currently `0.11.11+endbot.1`; current package `0.11.12+endbot.1` has no new
human/Xbox observation).

Release-candidate workflow input must exactly match `runtime/package.json`; the Python plugin and CLI versions must be its
PEP 440-equivalent form. `scripts/check_release_version.py` enforces that contract before any candidate is assembled.

Both Endstone wheels in the candidate (Linux `manylinux` and Windows `win_amd64`) are built by
`.github/workflows/release-candidate.yml` from the same `endstone.lock` + `patches/endstone/` inputs: the
`linux` and `windows` jobs each build, inspect, and install-test their platform wheel, then each builds the
pure-Python plugin and CLI wheels from the same commit plus its own self-tested operator bundle
(`scripts/build_bundle.py`, toolchain pinned in `packaging/toolchain.lock.json`). The `assemble` job
combines both wheels with the Linux-built plugin and CLI wheels (exactly one of each), both operator
bundles, the runtime/config tarballs, license, manifest, and checksums into the single candidate.
The Windows wheel is no longer a local build.

The eventual release job must add built artifact checksums and include the licenses/notices described in
`THIRD_PARTY_NOTICES.md`. It must not include BDS and must not publish solely because CI is green.
See `docs/INSTALL.md` for the candidate contents and the Linux/Windows installation, start, configuration, and
update paths supported by the current artifact model.

Every file in the candidate (bundles, wheels, tarballs, manifest, `SHA256SUMS`) receives a GitHub build provenance
attestation in the `assemble` job (`actions/attest-build-provenance`). Anyone can verify a downloaded file with
`gh attestation verify <file> --repo amatouhake/endbot`; the attestation binds its SHA-256 to this repository, the
`release-candidate.yml` workflow, and the commit it ran on. Publish the attested files unchanged.
