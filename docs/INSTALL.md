# Installing Endbot from release artifacts

This guide installs Endbot `0.1.0` from the release-candidate artifacts without reconstructing the development build
process. No artifact contains the official Bedrock Dedicated Server (BDS) binary: Endstone's normal bootstrap
downloads the pinned BDS (`1.26.51.1`, protocol `2193`) into the server folder after confirmation.

## Candidate contents

A release candidate (`endbot-<version>-candidate` workflow artifact, assembled but never published automatically)
contains exactly:

| File | Contents |
| --- | --- |
| `endstone-0.11.12+endbot.1-*.whl` | Patched Endstone, repaired to a `manylinux_*` platform tag (Linux x86_64, CPython 3.12). The only supported Endstone artifact. |
| `endstone_endbot-0.1.0-*.whl` | Endbot plugin. Requires exactly `endstone==0.11.12+endbot.1`, so pip cannot substitute official unpatched `0.11.12`. |
| `endbot-runtime-0.1.0.tar.gz` | Runtime source bundle (`package.json`, `package-lock.json`, `README.md`, `endbot-runtime.example.json`, `scripts/`, `src/`). |
| `endbot-config-0.1.0.tar.gz` | Example configuration (`config/`), `LICENSE`, `THIRD_PARTY_NOTICES.md`. |
| `ENDSTONE_LICENSE` | Upstream Endstone license carried from the pinned checkout. |
| `compatibility-manifest.json` | Machine-readable binding of Endbot revision, Endstone tag/commit/package, BDS version/build/protocol, patch revision, and validation state (schema: `release/compatibility-manifest.schema.json`). |
| `SHA256SUMS` | SHA-256 checksums of every file above. |

Verify before installing:

```bash
sha256sum -c SHA256SUMS
python3 -c "import json; print(json.load(open('compatibility-manifest.json'))['endstone'])"
```

The manifest's `tested_safety_conditions` reports the current artifact's own validation state. A current patched
package never inherits human/Xbox achievement observations from an earlier one; historical M0/M2 evidence stays
attributed to `0.11.11+endbot.1` under `historical_validation`. Do not claim achievement-preserving compatibility for
the candidate until the exact candidate passes the documented human/live gate.

## Linux install and start

Requirements: Python 3.12, Node.js 24+, git, cmake, and a C++ compiler. Docker is not needed to install
(only to build) the candidate. The compiler toolchain matters because `npm ci` falls back to building
`raknet-native` from source when no published prebuild matches the platform; without it the install fails
inside the dependency's build check rather than with an Endbot error.

1. Create a virtual environment and install the two wheels offline from the candidate directory. The repaired
   Endstone wheel carries no LLVM runtime dependency:
   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   python -m pip install ./endstone-0.11.12+endbot.1-*.whl ./endstone_endbot-0.1.0-*.whl
   python -c "import importlib.metadata as m; print(m.version('endstone'))"
   ```
   Expected: `0.11.12+endbot.1`. Never install official `endstone==0.11.12` into this environment.
2. Unpack the runtime bundle outside the server folder and install its pinned dependencies:
   ```bash
   mkdir -p /opt/endbot && tar -xzf endbot-runtime-0.1.0.tar.gz -C /opt/endbot
   npm ci --prefix /opt/endbot
   cp /opt/endbot/endbot-runtime.example.json /opt/endbot/endbot-runtime.json
   node /opt/endbot/src/cli.js --config /opt/endbot/endbot-runtime.json
   ```
   The runtime creates its control token and owner key pair on first start. Keep that directory under the operator's
   own account; POSIX files are mode `0600`.
3. Create the server folder and start once so Endstone bootstraps official BDS and writes its default configuration:
   ```bash
   mkdir -p /srv/endbot-server && endstone -s /srv/endbot-server
   ```
   Stop the server, then configure:
   - `server.properties`: keep `online-mode=true`, `allow-cheats=false`; leave `server-udp-ports` unset (do not set
     a single port or a range; since upstream `v0.11.12` the server shares one UDP port across NetherNet sessions).
     Check with `python3 scripts/preflight.py /srv/endbot-server/server.properties
     --acknowledge-world-state-unverified` from a checkout, or apply the same two values by hand.
   - `endstone.toml`: set the `[local-bot-auth]` table from the unpacked `config/endstone.local-auth.example.toml`
     with `enabled = true` and `public-key-file` pointing at the runtime's `owner-public.pem`. Only the public key
     is ever configured in Endstone. The `[network]` `stun-servers` default needs no change for local/loopback use.
   - `plugins/endbot/config.toml` (written by the first start): set `[runtime].token-file` to the runtime's
     `control.token` (absolute path), keep loopback host/port, and add the human tester's UUID or decimal XUID to
     one `[authorization]` allowlist. Do not make the tester an operator. Two TOML traps found during the
     operator gate: Windows paths must use *single-quoted* literal strings
     (`token-file = 'C:\endbot\secrets\control.token'`) because backslashes in double-quoted strings are
     escapes, and XUIDs must be *strings* (`allowed-xuids = ["2535466722952312"]`), not bare numbers.
4. Start the runtime first, then the server. The runtime prints `endbot_runtime_ready` when its loopback control
   socket listens; the server log reports `Accepted local bot '...'` on Bot login and the human joins through the
   normal Microsoft/Xbox path.

## Native Windows install and start

The Linux wheel does not install on Windows. The supported Windows path reuses every release artifact except the
Endstone wheel:

1. Build patched Endstone from source at the exact locked revision: unpack any checkout of this Endbot revision,
   run `python scripts/prepare_endstone.py` (bare cache under `.cache\`, ordered patch series applied with
   `git am`, local tag `v0.11.12+endbot.1`), then follow the toolchain and source-install steps in
   [`WINDOWS_DEV.md`](WINDOWS_DEV.md). Verify `0.11.12+endbot.1` before continuing.
2. Install the candidate plugin wheel into the same environment (`python -m pip install
   .\endstone_endbot-0.1.0-*.whl`); it is platform-independent and still pins the exact patched Endstone version.
3. Unpack `endbot-runtime-0.1.0.tar.gz`, run `npm ci` (npm 12 needs `--allow-remote=root` on the command line for
   the pinned tarball dependency URL; see [`WINDOWS_DEV.md`](WINDOWS_DEV.md)), copy the example runtime
   configuration outside the unpacked tree, and start the runtime with Node 24+.
4. Create the server folder with `endstone -s` (official Windows BDS `1.26.51.1` build `51061361`, same protocol
   `2193`), then apply the same `server.properties` / `endstone.toml` / plugin configuration as on Linux. Native
   Windows hosting is live-smoked but Linux remains the CI/release-artifact baseline.

## Updating to a newer candidate

1. Stop the server (`stop` in its console) and the runtime (`Ctrl+C`). Despawn Bots first.
2. Verify the new candidate's `SHA256SUMS` and confirm its `compatibility-manifest.json` names the intended
   Endstone package and BDS pair.
3. Replace the installed wheels (`pip install -U` the new pair; the plugin's exact pin rejects a mismatched
   Endstone) and unpack the new runtime/config tarballs next to the old ones. Re-apply only the operator-owned
   configuration values (token file, owner keys, allowlists); never copy generated secrets, pins, worlds, or
   machine-local paths between machines.
4. Start the runtime, then the server, and re-run the live regression matrix in [`WINDOWS_DEV.md`](WINDOWS_DEV.md)
   plus `/bot ping` before returning the server to normal use.

## What is intentionally out of scope

- BDS is never bundled, vendored, or copied into the repository or the candidate.
- No tag or GitHub Release is created from a green build alone. Publication waits for the exact candidate to pass
   the required human/live gate (normal Microsoft/Xbox login, local Bot login and coexistence, the command/input
   matrix, and the actual Xbox achievement observation), with the manifest and evidence updated accordingly.
- World pickup → same-session runtime inventory synchronization remains a known limitation with reconnect as the
   documented recovery path; same-session pickup inference, sneak ledge simulation, and block breaking/mining stay
   deferred (see [`M2_VALIDATION.md`](M2_VALIDATION.md)).
