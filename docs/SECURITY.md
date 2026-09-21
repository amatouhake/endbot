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

`endbot.command.control` defaults to false. The plugin reads UUID/XUID allowlists on load and attaches only that Endstone
permission to matching players when they join. It does not make them operators or grant vanilla command permissions.
Invalid allowlist data prevents safe plugin initialization rather than granting broadly.

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

## Reporting

Do not include keys, tokens, XUIDs, world data, or credentials in a public report. Open a minimal private report to the
maintainer for authentication vulnerabilities before publishing details.
