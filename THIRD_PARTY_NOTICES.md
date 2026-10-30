# Third-party notices

## Luau and nlohmann/json

The native `endbot-luau-runner` uses unmodified official Luau **0.743** (Roblox
Corporation and Lua.org, PUC-Rio; MIT) and nlohmann/json **3.12.0** (Niels Lohmann;
MIT). Exact source URLs and SHA-256 hashes are pinned in `runner/luau.lock.json`.
Upstream projects: <https://github.com/luau-lang/luau> and
<https://github.com/nlohmann/json>.

Operator bundles include the executable and trusted `api.luau` in
`app/<version>/runner/`. Their `licenses/` directory includes `LUAU_LICENSE.txt`
(copied from the verified Luau archive's `LICENSE.txt`) and
`NLOHMANN_JSON_LICENSE.txt` (the complete MIT notice retained in
`runner/licenses/`). These notices must accompany redistributed runner builds.
Scripting is disabled by default.

## Endstone

Endbot prepares a modified Endstone build from:

- Project: Endstone
- Upstream: <https://github.com/EndstoneMC/endstone>
- Tag: `v0.11.15`
- Commit: `b4aaa87d2c5305e7d340168f824ac2e2ea722e44`
- License: Apache License 2.0
- Copyright: The Endstone Project contributors

The resulting build is **modified by Endbot**. The Endbot patch-set revision is SHA-256
`fe40fb1856dc813bfd641821d00de7fbf3c24f550096037c57bf6d8a9669d64a`; its ordered sources are listed in
`patches/endstone/series`. Upstream source notices are retained by the patches. Distributions of the modified build
must include this file, Endbot's `LICENSE`, Endstone's upstream license/notice material, and the generated compatibility
manifest.

Endbot does not vendor or redistribute the official Minecraft Bedrock Dedicated Server binary. Minecraft and Xbox are
trademarks of Microsoft; this project is not affiliated with or endorsed by Microsoft.

## Optional UI resource pack

`ui-pack/` is Endbot's own JSON UI written for the Minecraft Bedrock client. It names and inherits vanilla UI
elements but contains no Mojang files. Its layout was written against the
[bedrock-samples](https://github.com/Mojang/bedrock-samples) vanilla resource pack 1.26.50.4, which is
subject to the Minecraft EULA. That pack is neither vendored here nor redistributed; `scripts/build_ui_pack.py --vanilla`
reads a copy the operator fetches.

## bedrock-protocol

The Endbot runtime uses the unmodified upstream npm release:

- Project: bedrock-protocol
- Upstream: <https://github.com/PrismarineJS/bedrock-protocol>
- Version: 3.61.0
- License: MIT
- Copyright: PrismarineJS contributors

The exact release and its dependency tree, including `nethernet` and `werift` (NetherNet transport) and the
`prismarine-xbox-services` source tarball that is not published on the npm registry, are pinned in
`runtime/package-lock.json`. Its transitive dependency notices remain available in
their installed packages and must be retained when redistributing a runtime bundle.

## minecraft-data

The Endbot runtime uses the Bedrock 1.26.60/protocol-2225 schema shipped unmodified in:

- Project: minecraft-data
- Upstream: <https://github.com/PrismarineJS/minecraft-data>
- Version: 3.119.0 (npm release, pinned in `runtime/package-lock.json`)
- License: MIT
- Copyright: PrismarineJS contributors
