# Architecture

## Boundaries

Endbot has three intentionally separate layers:

1. **Patched Endstone** owns only the login-validation seam that a normal plugin cannot reach. The source of truth is
   the exact upstream revision in `endstone.lock` plus the ordered patch files in `patches/endstone/`.
2. **Endbot plugin** owns server-side commands, permissions, the bot registry, and future persistence/control routing.
   M1 implements only `/bot ping` and an explicit player UUID/XUID allowlist.
3. **Endbot runtime** owns accountless client identity and will later own NetherNet, reconnect, input, actions, macros,
   and tasks. M1 implements only the local identity producer and its tests.

Prepared Endstone trees and build products are disposable. No maintained fork is necessary to reproduce a build.

## Authentication flow

The hook is around `ServerNetworkHandler::_validateLoginPacket`. BDS's original rejection path disconnects immediately,
so the hook recognizes an exact configured local issuer before invoking that destructive path:

```text
exact configured local issuer + all local checks pass -> local bot identity
anything else                                      -> original Microsoft validator
original Microsoft validator passes                -> normal player identity
original validator rejects                         -> reject
```

The local checks require ES384/P-384, owner signature, audience, bounded issue/not-before/expiry times, a single-use
UUID `jti`, UUID subject, bounded name, no Microsoft XUID claim, and a client-data signature/name/UUID bound to `cpk`.
The feature is disabled by default. Invalid local-looking traffic never becomes an authenticated identity.

## Identity

`BotIdentity` is keyed by an immutable hidden UUID. Its unique Minecraft name is the user-visible command handle. A
future rename will replace the name index while preserving the UUID and all persistence/group/task relationships.
The planned command shapes are `/bot <name> <action>` and `/botgroup <group-name> <action>`; there is no selection state.

## Patch lifecycle

`scripts/prepare_endstone.py` validates the tag-to-commit relationship, clones/fetches a bare cache, makes a separate
checkout, and applies `patches/endstone/series`. The cache contains pristine upstream objects only. Patch application
creates commits in the disposable checkout and must finish with a clean working tree.

When updating Endstone, first validate a real Endstone/BDS pair, rebase the smallest necessary patch delta, run all
upstream and Endbot tests, repeat the security negative controls, complete the manual achievement gate, then update the
lock and release manifest together.

