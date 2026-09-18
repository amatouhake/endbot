# Security model

## Trust boundary

Normal players authenticate exactly as they do in upstream BDS. Endbot's additional path is local server-owner trust,
not a general online-auth bypass. It is disabled unless `[local-bot-auth].enabled` is set in `endstone.toml` and a P-384
public key is configured.

The private owner key belongs to the runtime operator. Keep it outside the repository and server web roots, restrict it
to the service account, back it up as a secret, and rotate both runtime and server configuration if exposure is
suspected. The M1 runtime creates it with mode `0600` on POSIX and rejects a symlink at the private-key path. Never log
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

## Reporting

Do not include keys, tokens, XUIDs, world data, or credentials in a public report. Open a minimal private report to the
maintainer for authentication vulnerabilities before publishing details.

