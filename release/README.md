# Release preparation

Generate metadata only from a clean, reviewed release commit:

```bash
python3 scripts/generate_compatibility_manifest.py dist/compatibility-manifest.json --endbot-version X.Y.Z
```

The safety metadata scopes the completed Xbox-achievement observation to the M0 `/bot ping` gate and reports the M2
achievement gate separately. Do not infer M2 completion from the preserved M0 evidence.

Release-candidate workflow input must exactly match `runtime/package.json`; the Python plugin version must be its
PEP 440-equivalent form. `scripts/check_release_version.py` enforces that contract before any candidate is assembled.

The eventual release job must add built artifact checksums and include the licenses/notices described in
`THIRD_PARTY_NOTICES.md`. It must not include BDS and must not publish solely because CI is green.
