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

Release-candidate workflow input must exactly match `runtime/package.json`; the Python plugin version must be its
PEP 440-equivalent form. `scripts/check_release_version.py` enforces that contract before any candidate is assembled.

Both Endstone wheels in the candidate (Linux `manylinux` and Windows `win_amd64`) are built by
`.github/workflows/release-candidate.yml` from the same `endstone.lock` + `patches/endstone/` inputs: the
`linux` and `windows` jobs each build, inspect, and install-test their platform wheel, and the `assemble` job
combines both wheels with the once-built plugin wheel, runtime/config tarballs, license, manifest, and checksums
into the single candidate. The Windows wheel is no longer a local build.

The eventual release job must add built artifact checksums and include the licenses/notices described in
`THIRD_PARTY_NOTICES.md`. It must not include BDS and must not publish solely because CI is green.
See `docs/INSTALL.md` for the candidate contents and the Linux/Windows installation, start, configuration, and
update paths supported by the current artifact model.
