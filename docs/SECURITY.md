# Security model

## Trust boundary

Normal players authenticate exactly as they do in upstream BDS. Endbot's additional path is local server-owner trust,
not a general online-auth bypass. It is disabled unless `[local-bot-auth].enabled` is set in `endstone.toml` and a P-384
public key is configured.

The private owner key belongs to the runtime operator. Keep it outside the repository and server web roots, restrict it
to the service account, back it up as a secret, and rotate both runtime and server configuration if exposure is
suspected. The runtime creates it with mode `0600` on POSIX and rejects a symlink at the private-key path. Never log
compact tokens or private-key material.

## Acceptance policy

A local identity is accepted only when all of these hold:

- configured feature is enabled and `iss` exactly matches;
- `alg` is ES384 and the owner signature verifies under the configured P-384 key;
- `aud`, `iat`, `nbf`, and `exp` satisfy the configured audience, skew, and maximum lifetime;
- `sub` and `jti` are RFC 4122 UUIDs, the name is non-empty and at most 64 bytes, and no XUID claim is asserted;
- the `jti` has not already been used during its validity window;
- `cpk` is a P-384 public key that verifies client data containing the same name and UUID.

Wrong issuer traffic is not treated as locally trusted. Every non-accepted login goes to the original BDS validator and
can connect only if Microsoft validation succeeds.

Automated patched-Endstone tests cover valid acceptance, replay, disabled configuration, malformed tokens, wrong owner
signature, wrong issuer, expiry, wrong client key, and name binding. Prior live validation also rejected modified
signatures and mismatched `cpk`; release qualification must repeat live negative controls.

## Command authorization

`endbot.command.control` defaults to false. The plugin attaches only that Endstone permission, on join, to players that
are either bound controllers or listed in the pre-bound UUID/XUID allowlists. It does not make them operators or grant
vanilla command permissions. Invalid allowlist or binding data prevents safe plugin initialization rather than granting
broadly.

Controllers are configured by Xbox GamerTag but authorized by XUID. A configured GamerTag stays pending until a player
with that name joins through Microsoft/Xbox authentication with a non-empty decimal XUID; the plugin then records the
GamerTag → XUID/UUID binding in the controllers file. A bound GamerTag can never be re-bound to another XUID, so a
different account that later takes the GamerTag gets no permission, while the bound owner keeps control after a
GamerTag change. Local Bots have no XUID and can never bind. Removing a GamerTag from the configuration revokes its
binding on the next load. Until the owner's first join, whoever currently holds a pending GamerTag could claim it, so
operators should join once right after setup; `endbot doctor` lists pending entries.

## Runtime control

The controller listens only on `127.0.0.1`, `::1`, or `localhost`; both runtime and plugin reject other configured
hosts. Every bounded JSON request is authenticated with a 256-bit token stored in a separate private file and compared
in constant time. Missing, corrupt, or wrong tokens fail closed. This is defense in depth for same-host IPC, not an
Internet-facing authorization system. Run BDS and the runtime under appropriately restricted service accounts and do
not expose the control port through a proxy or port-forward.

Profiles contain UUID, Minecraft name, and desired online state but no credentials or duplicated world inventory.
Profile files reject symlinks/corruption; creation uses the same complete-before-publish mechanism as runtime identity
artifacts. Rename preserves UUID. The plugin rejects a rename colliding with an online real player; because Endstone
does not expose a reliable complete offline GamerTag history, operators should also avoid known offline player names.

## Runtime → BDS connection

The runtime connects its Bots only to a BDS on the same host: the configuration loader and every session refuse a
`serverHost` other than `127.0.0.1`, `::1`, or `localhost`. The runtime does not pin the server's NetherNet identity.
BDS generates a new NetherNet DTLS identity on every start, so a persistent pin fails after every restart, and the
upstream NetherNet client exposes no hook to verify it.

Trust in that connection rests on the loopback path and on the local-bot token instead. A same-host process that
impersonates BDS could observe a Bot's inputs and feed it a fake world. It cannot turn a captured token into a login on
the real server: the token is owner-signed for one audience, lives at most the configured maximum lifetime, carries a
single-use `jti`, and is bound to the Bot's client key (`cpk`), whose private key never leaves the runtime. A process
able to take over BDS's loopback port already runs with privileges that can read the runtime's secrets directly, so
host account isolation (above) is the control that matters.

## Reporting

Do not include keys, tokens, XUIDs, world data, or credentials in a public report. Open a minimal private report to the
maintainer for authentication vulnerabilities before publishing details.
