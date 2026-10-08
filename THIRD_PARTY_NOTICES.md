# Third-party notices

## Endstone

Endbot prepares a modified Endstone build from:

- Project: Endstone
- Upstream: <https://github.com/EndstoneMC/endstone>
- Tag: `v0.11.13`
- Commit: `3491c609ddfde392cee2b062e3063e39aae87274`
- License: Apache License 2.0
- Copyright: The Endstone Project contributors

The resulting build is **modified by Endbot**. The Endbot patch-set revision is SHA-256
`3a4215f678de3e26fe8abf5843a7800a117510caf137bac1b8d51266177860eb`; its ordered sources are listed in
`patches/endstone/series`. Upstream source notices are retained by the patches. Distributions of the modified build
must include this file, Endbot's `LICENSE`, Endstone's upstream license/notice material, and the generated compatibility
manifest.

Endbot does not vendor or redistribute the official Minecraft Bedrock Dedicated Server binary. Minecraft and Xbox are
trademarks of Microsoft; this project is not affiliated with or endorsed by Microsoft.

## bedrock-protocol

The Endbot runtime uses the unmodified upstream npm release:

- Project: bedrock-protocol
- Upstream: <https://github.com/PrismarineJS/bedrock-protocol>
- Version: 3.60.1
- License: MIT
- Copyright: PrismarineJS contributors

The exact release and its dependency tree, including `nethernet` and `werift` (NetherNet transport) and the
`prismarine-xbox-services` source tarball that is not published on the npm registry, are pinned in
`runtime/package-lock.json`. Its transitive dependency notices remain available in
their installed packages and must be retained when redistributing a runtime bundle.

## minecraft-data

The Endbot runtime uses the Bedrock 1.26.51/protocol-2193 schema shipped unmodified in:

- Project: minecraft-data
- Upstream: <https://github.com/PrismarineJS/minecraft-data>
- Version: 3.117.0 (npm release, pinned in `runtime/package-lock.json`)
- License: MIT
- Copyright: PrismarineJS contributors
