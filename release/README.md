# Release preparation

Generate metadata only from a clean, reviewed release commit:

```bash
python3 scripts/generate_compatibility_manifest.py dist/compatibility-manifest.json --endbot-version X.Y.Z
```

The eventual release job must add built artifact checksums and include the licenses/notices described in
`THIRD_PARTY_NOTICES.md`. It must not include BDS and must not publish solely because CI is green.
