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

`endbot.command.control` defaults to false. The plugin grants that permission to authenticated bound controllers or
players in the pre-bound UUID/XUID allowlists unless explicitly revoked, and to the server console. Every player must
have a valid Xbox XUID; local Bots cannot gain access via a UUID entry. Operator players get no Endbot access from being
op. No operator or vanilla permissions are granted. Invalid allowlist, binding or runtime policy data prevents plugin
initialization rather than granting broadly. The command handler also checks effective authorization independently of
attachments supplied by other plugins.

Runtime administration uses the separate `endbot.command.controllers` permission and an explicit console-sender check.
Even a controller or operator with an externally granted permission cannot administer access from a player session.
`endbot controllers add|remove|list` forwards to that trusted console; the plugin alone atomically writes
`controller-policy.json`. Identity revocations override config and legacy allowlists, survive restart/name changes, and
are cleared by an explicit regrant. Offline unknown grants bind only on a normal authenticated join. Command execution
rechecks authorization and its grant revision on the server thread, so queued commands cannot regain validity after a
revoke/regrant or reconnect. Already-started operations may finish; see [runtime semantics](OPERATIONS.md#runtime-grants-and-revocations).

Each controller grant has a role ([PERMISSIONS.md](PERMISSIONS.md#roles)), kept by endbotd in
`state/controller-roles.json` by XUID; a grant with no entry is a scripter, and a revoke removes the entry. A grant made
without `--role` is a controller, and an existing grant keeps its role unless `--role` is given. endbotd
reads the role for every command it receives, so a role change applies to the next command without a restart or a
grant revision change. A **controller** is refused script authoring (create, save, book import, copy, rename, restore,
making a script shared, and opening the editor) with the authorization outcome, whether the request is typed, sent by a form or comes
from any later surface; its menus leave those actions out. Running scripts, and making private and deleting the
scripts it may change (its own, and unowned scripts saved before ownership existed), stay open; both only narrow what
others can use. The console is never refused, and the check fails closed for any caller that is not a known player
or the console. An older Endbot ignores the file, so after a rollback every controller
can author again.

Bots and Groups have owners ([PERMISSIONS.md](PERMISSIONS.md#ownership)), kept by endbotd in `state/ownership.json`:
a player's new Bot or Group (or one a script run creates, for the run's invoker) is owned by that player with the
`[permissions]` default visibility; console creations and everything created before ownership are unowned and shared.
Every command that names a Bot — typed, from a form, from a script or, later, the web — passes one check before it
reaches the runtime: someone else's **private** Bot is refused with the authorization outcome (it stays listed, since
it is visible in game), and on someone else's shared Bot `forget` and `rename` stay with its owner. A Group operation
checks each member before anything is sent, so only the members the invoker may not operate are refused. Someone
else's private Group is left out of lists and menus and resolves as not found; its name stays taken, and
creating it only says the name is not available. A script's waits on a Bot pass the same check, and a continuing
mining job ends when its Bot becomes private to someone else. The console is never refused. Owners may share or
make private their own Bots and Groups; unowned items stay shared until the console assigns an owner, and only the
console reassigns owners. An older Endbot ignores the file, so after a rollback private Bots and Groups are shared again.

A script states the capabilities it uses beyond the basic set (operating Bots, reading the world, waiting, logging) in
`requires {}`, once and before `params {}` ([PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script)). An
unknown name is an inspection error, so such a script is never saved, loaded or run; no capability beyond the basic set
exists until events add one. A run started from an inspection (the run form, the parameterless menu start, later a
trigger) carries the declaration that inspection read and stops before its first operation if its own declaration
differs, so a conditional `requires {}` cannot widen what was shown.

`[limits] max-bots-per-controller` (0, the default, means no limit) caps how many Bots one player owns: a player's new
Bot beyond it is refused with the conflict outcome before the runtime creates a profile, a Bot a script run creates
counts for the run's invoker, reconnecting an existing Bot is never limited, and the console is never limited.

`endbot controllers` connects to endbotd directly over its loopback link with the instance's endbotd token, and endbotd
performs and answers the request. A request whose answer is lost reports an unknown outcome; mutations are never
retried automatically, and endbotd refuses a request id already used on the connection. This adds no network listener
beyond endbotd's loopback link and no player administration API.

Controllers are configured by Xbox GamerTag but authorized by XUID. A configured GamerTag stays pending until a player
with that name joins through Microsoft/Xbox authentication with a non-empty decimal XUID; the plugin then records the
GamerTag → XUID/UUID binding in the controllers file. A bound GamerTag can never be re-bound to another XUID, so a
different account that later takes the GamerTag gets no permission, while the bound owner keeps control after a
GamerTag change. Local Bots have no XUID and can never bind. Removing a GamerTag from the configuration revokes its
bootstrap binding on the next load; an explicit runtime grant remains until revoked. Until the owner's first join,
whoever currently holds a pending GamerTag could claim it, so
operators should join once right after setup; `endbot doctor` lists pending entries.

### Events, watches and triggers

Event kinds are observation only: nothing a script receives can cancel or change what happened ([EVENTS.md](EVENTS.md)).
Every listed kind is public in the PERMISSIONS sense, and the registry test refuses any other visibility. `block.change`
names a Bot that changed a block but never a human player, because a name next to a coordinate would reveal where that
player is. Kinds beyond the basic set (`observe.chat`, `observe.presence`) need the capability in the script's
`requires {}`, which every form shows before a run or a trigger is armed.

The plugin reports only what endbotd's interest set asks for, and endbotd matches occurrences to watches and triggers
itself; occurrences carry no owner. Bounds keep a script from loading the server thread: a watched area holds at most
512 blocks, the whole server at most `[events] max-watched-blocks` (default 2048, at most 16384), read a quarter per tick;
a run holds at most 16 watches and a controller `[limits] max-watches-per-controller` (32).

A trigger starts runs as its owner, never as whoever caused the occurrence. Arming (creating or enabling) records the
script revision, its declaration, a hash of its parameters and the owner's grant revision. Each start checks that the
owner still holds that grant (a revoke disables the trigger and a regrant does not re-arm it), that the owner may still
run the script, that its declaration has not grown and its parameters have not changed (otherwise the trigger pauses
until the owner enables it again, which re-approves). Runs count toward the owner's concurrent-run limit, at most
`[limits] max-triggers-per-controller` (8) triggers are armed per controller, and the per-minute start cap disables a
trigger that feeds itself, telling the owner. No trigger starts while the plugin is not linked.

### Operators and achievements

From Endstone `v0.11.13`, permissions alone decide who may run a command: upstream marks every command as not
requiring cheats. An operator can therefore run `/give`, `/gamemode`, `/tp`, and similar commands while
`allow-cheats=false`, and the world keeps achievements enabled. `allow-cheats=false` no longer means "nobody can
cheat"; it means that only operators can. This is upstream behavior that Endbot does not patch (the Endstone delta stays
limited to local-bot authentication). Bots are never operators, and controllers get no operator permission from Endbot.
`endbot doctor` WARNs when `permissions.json` lists operators or `default-player-permission-level=operator` makes every
player one. Keep the operator list to people you trust not to cheat, or empty, if fair achievement history matters.

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

LAN discovery can surface other NetherNet hosts on the same machine. The runtime logs in only to the host advertising
the configured `server-name` and world display name (`level.dat` `LevelName`, falling back to `level-name` before
the world exists); it does not fall back to the first host that answers, so another local
program is not sent Bot login tokens merely by answering discovery first.

The transport is NetherNet only: BDS 1.26.60 refuses players on RakNet, so the CLI rejects `transport = "raknet"`.
Doctor requires `transport=nethernet` in BDS.

Trust in that connection rests on the loopback path and on the local-bot token instead. A same-host process that
impersonates BDS could observe a Bot's inputs and feed it a fake world. It cannot turn a captured token into a login on
the real server: the token is owner-signed for one audience, lives at most the configured maximum lifetime, carries a
single-use `jti`, and is bound to the Bot's client key (`cpk`), whose private key never leaves the runtime. A process
able to take over BDS's loopback port already runs with privileges that can read the runtime's secrets directly, so
host account isolation (above) is the control that matters.

## The web

The web is optional and off until an operator runs `endbot web deploy`
([deployment guide](WEB_DEPLOY.md), [design](WEB.md)). It has not yet been validated on Cloudflare or with a real
authenticator.

**The boundary.** The Worker and its Durable Object in the operator's Cloudflare account are an untrusted relay. endbotd
on the server PC is the authority: it issues and verifies passkey ceremonies, issues sessions, and authorizes every relayed
request at execution time through the same permission path as the game, with the current grant revision. The Worker verifies
no passkey, holds no account and makes no authorization decision. The server PC opens no inbound port: endbotd opens one
outbound WebSocket and proves itself with an Ed25519 link key. The private key stays in `state/secrets/web-link.key` (mode
`0600` on POSIX; on Windows it inherits the instance directory's ACL, so restrict that directory; a symlink is refused), and
only the public half is on Cloudflare. TLS to the operator's own address
authenticates the Worker to endbotd. `endbot web rotate-key` replaces the key.

**What a compromised Worker or Cloudflare account can do.** It can read what passes through while it is compromised,
including script source, logs and session cookies of browsers that visit it, and it can misuse those browsers' sessions
within those players' own roles. It can serve a page that asks a visiting player to approve a passkey prompt for the same
address. It cannot make an authenticator sign for another address or account, sign in as a player who does not visit, link a
new XUID, widen a role, or reach grants, role changes, owner reassignment or BDS start and stop, none of which exist on the web.
Keep the Cloudflare account protected accordingly.

**Who can have an account.** Only an XUID that holds a controller grant bound by an Xbox-authenticated join. Linking uses a
code issued in game to that player: 6 symbols, 5 minutes, one use, and code entry closes for 10 minutes after 10 wrong codes
server-wide. A new passkey always starts in the game or at the trusted site, never from a signed-in browser. The web principal
is never the console, even for the operator's own XUID.

**Passkeys and sessions.** endbotd checks the challenge (single use, bound to its ceremony, 2 minutes), the origin and RP ID
against its own configuration rather than anything the Worker sends, user presence and verification, the signature and the
sign counter. A session token is 256 random bits, of which endbotd stores only the SHA-256. The cookie is `__Host-` prefixed,
`Secure`, `HttpOnly` and `SameSite=Strict`, and sessions last 7 days idle and 30 days at most. Removing a passkey needs a recent
assertion. A change of address deletes all passkeys and sessions and keeps the accounts.

**Revocation.** Removing a controller grant ends that player's sessions and closes their connections at once, and because every
request rechecks the grant revision, a lost close message does not extend access. A GamerTag removed from `endbot.toml` takes
effect at the next start, which ends its sessions. `/botweb unlink` and `endbot web revoke`
delete the account, its passkeys and its sessions. A role change applies to the next request.

**Requests from browsers.** The Worker refuses non-GET requests and WebSocket upgrades whose `Origin` is not exactly the
configured address, and state-changing HTTP calls need `Content-Type: application/json`. The one exception is the trusted
site's form POST to `/auth/site/return`, which comes from the site's origin: the Worker requires an `Origin` and the site cookie
and relays it, and endbotd compares that `Origin` with the configured issuer. The pages carry a strict
Content-Security-Policy with no third-party scripts, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer` and HSTS. The rate
limits on the Worker keep floods off the link; the limits that protect anything are in endbotd.

**What leaves the PC.** Cloudflare stores the Worker, the static page, the public link key, the address and a display cache
(the display name and whether BDS was last up). The Worker logs no bodies, and deploy turns off Workers logs, logpush and preview addresses. Script
source and logs pass through Worker memory while relayed. The Cloudflare API token is entered at a hidden prompt or through
`CLOUDFLARE_API_TOKEN`, never as an argument, is never written to disk, and is removed from API errors.

### The trusted external site

The trusted site is off by default ([guide](WEB_DEPLOY.md#the-optional-trusted-site), [design](WEB.md#p12-trusted-external-site-optional-off-by-default)).
Enabling it is an explicit trust decision: **whoever controls the site or its private key can sign in as any XUID that holds a
controller grant.** It can never create or widen a grant or a role, because the XUID must already hold a grant bound by an
Xbox-authenticated join, and that is checked again on every request.

Assertions are compact JWS, ES384 only, audience-bound to the web address, at most 120 s long, single use, and bound by an
endbotd nonce to the browser that started the flow, so an assertion copied from the site's logs is useless elsewhere. The nonce
is consumed on the first attempt, valid or not, and dies with an endbotd restart, so no replay cache has to survive one. The
private key stays on the site. Keys rotate by listing two and removing the old one, and removing a key refuses its assertions at
once. After a suspected exposure, `endbot web revoke --site-sessions` deletes every session and passkey that came through the
site.

Keeping someone out means revoking their controller grant, not only their web account: with the site enabled it can vouch
for them again at the next sign-in, and `endbot web revoke` says so.

## Reporting

Do not include keys, tokens, XUIDs, world data, or credentials in a public report. Open a minimal private report to the
maintainer for authentication vulnerabilities before publishing details.
