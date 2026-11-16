# Security model

What Endbot trusts, what it enforces and where, and what the operator must do in return. How to report a
vulnerability is at the end ([Reporting](#reporting)).

## Boundaries at a glance

| Boundary | Trusted | Enforced by | The operator must |
| --- | --- | --- | --- |
| [Bot login](#trust-boundary) | The server owner's P-384 key | The patched Endstone login check | Keep the owner private key secret |
| [Commands](#command-authorization) | Xbox-authenticated controllers, the server console | endbotd, on every command | Grant only people they mean to; join once after setup |
| [Events and triggers](#events-watches-and-triggers) | Nothing a script receives | endbotd's bounds and checks at each trigger start | Choose the `[events]` and `[limits]` values |
| [Operators](#operators-and-achievements) | BDS operators, fully | Upstream Endstone permissions (not Endbot) | Keep the operator list to people they trust |
| [Runtime control](#runtime-control) | Same-host processes holding the control token | Loopback-only listener and a 256-bit token | Run under restricted service accounts; never expose the port |
| [Runtime → BDS](#runtime--bds-connection) | The loopback path and the Bot token | The runtime's host and server-name checks | Isolate host accounts |
| [The web](#the-web) | endbotd only; the Worker is an untrusted relay | endbotd, on every relayed request | Protect the Cloudflare account |
| [The trusted site](#the-trusted-external-site) | The site and its key, for any XUID with a grant | endbotd's assertion checks | Enable it only as an explicit trust decision |

## Trust boundary

Normal players authenticate exactly as they do in upstream BDS. Endbot adds one path: local server-owner trust for its
own Bots. It is not a general online-auth bypass.

The path is off unless `[local-bot-auth] enabled` is true in `endstone.toml` and a P-384 public key is configured.
`endbot setup` writes the table; everything else in `endstone.toml` is left alone.

| `[local-bot-auth]` key | Endstone default | `endbot setup` writes | Effect |
| --- | --- | --- | --- |
| `enabled` | `false` | `true` | Turns the local path on |
| `issuer` | `endbot://local-bot` | same | `iss` must match exactly |
| `audience` | `endstone://local-bot` | same | `aud` must match |
| `public-key-file` | `owner-public.pem` | absolute path to the instance's owner public key | The P-384 key signatures are checked against |
| `clock-skew-seconds` | `5` | `5` | Allowed skew for `iat` and `nbf`; 0 to 300 |
| `max-token-lifetime-seconds` | `120` | `120` | Longest accepted token; 1 to 3600 |

The patched Endstone refuses an empty issuer, audience or key file, and a time limit outside its range; it never
widens a check to fit them.

### Acceptance policy

A local identity is accepted only when all of these hold:

- the feature is enabled and `iss` exactly matches;
- `alg` is ES384 and the owner signature verifies under the configured P-384 key;
- `aud`, `iat`, `nbf` and `exp` satisfy the configured audience, skew and maximum lifetime;
- `sub` and `jti` are RFC 4122 UUIDs, the name is non-empty and at most 64 bytes, and no XUID claim is asserted;
- the `jti` has not already been used during its validity window;
- `cpk` is a P-384 public key that verifies client data containing the same name and UUID.

Every login that is not accepted, including one with a wrong issuer, goes to the original BDS validator. It connects
only if Microsoft validation succeeds; wrong-issuer traffic is never treated as locally trusted.

Automated patched-Endstone tests cover valid acceptance, replay, disabled configuration, malformed tokens, wrong owner
signature, wrong issuer, expiry, wrong client key and name binding. Prior live validation also rejected modified
signatures and mismatched `cpk`. Release qualification must repeat the live negative controls
([change gate](COMPATIBILITY.md#compatibility-change-gate), step 5).

### The owner private key

The private owner key belongs to the runtime operator.

The runtime creates it with mode `0600` on POSIX and rejects a symlink at the private-key path.

The operator must:

1. keep it outside the repository and any server web root;
2. restrict it to the service account;
3. back it up as a secret;
4. if exposure is suspected, rotate it in both the runtime and the server configuration;
5. never log compact tokens or private-key material.

## Command authorization

### Who may use `/bot`

- `endbot.command.control` defaults to false.
- endbotd decides who holds it and the plugin applies what endbotd sends: authenticated bound controllers, players in
  the pre-bound UUID/XUID allowlists unless explicitly revoked, and the server console.
- Every player must have a valid Xbox XUID. Local Bots cannot gain access through a UUID entry.
- Being an operator gives no Endbot access. Endbot grants no operator or vanilla permissions.
- Invalid allowlist, binding or runtime policy data stops endbotd from starting, so every Endbot command is refused;
  it never grants broadly.
- endbotd checks effective authorization for every command itself, independent of permission attachments other
  plugins supply.

### Who may administer access

- Administration uses the separate `endbot.command.controllers` permission plus an explicit console-sender check.
  Even a controller or operator holding that permission from elsewhere cannot administer access from a player session.
- `endbot controllers add|remove|list` and the console's `/botcontrollers` go to endbotd, which alone writes
  `controller-policy.json`, atomically.
- Identity revocations override the config and legacy allowlists, survive restarts and name changes, and are cleared
  only by an explicit regrant.
- An unknown offline grant binds only on a normal authenticated join.
- Execution rechecks authorization and its grant revision on the server thread, so a queued command cannot regain
  validity after a revoke, a regrant or a reconnect. Operations that already started may finish; see
  [runtime semantics](OPERATIONS.md#what-a-grant-change-does).

`endbot controllers` connects straight to endbotd over its loopback link with the instance's endbotd token, and endbotd
performs and answers the request.

- A request whose answer is lost reports an unknown outcome. Mutations are never retried automatically.
- endbotd refuses a request id already used on the connection.
- This adds no network listener beyond endbotd's loopback link, and no player administration API.

### Controllers named by GamerTag

Controllers are configured by Xbox GamerTag but authorized by XUID.

- A configured GamerTag stays pending until a player with that name joins through Microsoft/Xbox authentication with a
  non-empty decimal XUID. endbotd then records the GamerTag → XUID/UUID binding in the controllers file.
- A bound GamerTag is never re-bound to another XUID. A different account that later takes the GamerTag gets nothing;
  the bound owner keeps control after a GamerTag change.
- Local Bots have no XUID and can never bind.
- Removing a GamerTag from the configuration revokes its bootstrap binding the next time the configuration is loaded
  (the next start). An explicit runtime grant stays until it is revoked.

Until the owner's first join, whoever holds a pending GamerTag could claim it. Join once right after setup;
`endbot doctor` lists pending entries.

### Roles

endbotd keeps each grant's role ([PERMISSIONS.md](PERMISSIONS.md#roles)) in `state/controller-roles.json`, by XUID.

| Situation | Role |
| --- | --- |
| A grant with no entry in the file | scripter |
| A new grant made without `--role` | controller |
| An existing grant changed without `--role` | keeps its role |
| A revoked grant | entry removed |

endbotd reads the role for every command it receives, so a change applies to the next command without a restart or a
grant revision change.

A **controller** is refused script authoring with the authorization outcome, whether the request is typed, sent by a
form or comes from the web, and its menus leave those actions out. Authoring means:

- create, save, book import, copy, rename and restore;
- making a script shared;
- opening the editor.

A controller may still run scripts, and make private or delete the scripts it may change (its own, and unowned scripts
saved before ownership existed); both only narrow what others can use. The console is never refused, and the check
fails closed for any caller that is not a known player or the console.

After a rollback to an Endbot that does not know the file, it is ignored and every controller can author again.

### Bot and Group ownership

endbotd keeps owners ([PERMISSIONS.md](PERMISSIONS.md#ownership)) in `state/ownership.json`.

- A player's new Bot or Group is owned by that player, with the `[permissions]` default visibility. One a script run
  creates is owned by the run's invoker.
- Console creations and everything created before ownership are unowned and shared.

Every command that names a Bot, whether typed, from a form, from a script or from the web, passes one check before it
reaches the runtime:

| Target | Result for someone other than the owner |
| --- | --- |
| A **private** Bot | Refused with the authorization outcome; it stays listed, since it is visible in game |
| A shared Bot | Allowed, except `forget` and `rename`, which stay with the owner |
| A Group operation | Each member checked before anything is sent; only the members the invoker may not operate are refused |
| A **private** Group | Left out of lists and menus and resolves as not found; its name stays taken, and creating it only says the name is not available |
| A script's waits on a Bot | The same check; a continuing mining job ends when its Bot becomes private to someone else |

- The console is never refused.
- Owners may share or make private their own Bots and Groups.
- Unowned items stay shared until the console assigns an owner. Only the console reassigns owners.
- After a rollback to an Endbot that does not know the file, private Bots and Groups are shared again.

### Script capabilities

A script states the capabilities it uses beyond the basic set (operating Bots, reading the world, waiting, logging) in
`requires {}`, once and before `params {}` ([PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script)).

- The capabilities beyond the basic set are `observe.chat` and `observe.presence`.
- An unknown name is an inspection error, so such a script is never saved, loaded or run.
- A run started from an inspection (the run form, the parameterless menu start, a trigger) carries the declaration
  that inspection read. It stops before its first operation if its own declaration differs, so a conditional
  `requires {}` cannot widen what was shown.

### Limits per player

| Setting | Default | Range | Enforced as |
| --- | --- | --- | --- |
| `[limits] max-bots-per-controller` | `0` (no limit) | 0 or more | A player's new Bot beyond it is refused with the conflict outcome before the runtime creates a profile |
| `[limits] max-watches-per-controller` | `32` | 0 to 1024 | Watches one controller may hold |
| `[limits] max-watched-blocks-per-controller` | `1024` | 0 to 16384 | Cells one controller watches across its watches and triggers |
| `[limits] max-triggers-per-controller` | `8` | 0 to 256 | Triggers armed per controller |

For the Bot limit, a Bot a script run creates counts for the run's invoker, reconnecting an existing Bot is never
limited, and the console is never limited. The event limits are in the next section.

## Events, watches and triggers

Event kinds are observation only: nothing a script receives can cancel or change what happened ([EVENTS.md](EVENTS.md)).

- Every listed kind is public in the PERMISSIONS sense; the registry test refuses any other visibility.
- `block.change` names a Bot that changed a block but never a human player, because a name next to a coordinate would
  reveal where that player is.
- Kinds beyond the basic set need `observe.chat` or `observe.presence` in the script's `requires {}`, which every form
  shows before a run or a trigger is armed.

The plugin reports only what endbotd's interest set asks for. endbotd matches occurrences to watches and triggers
itself, and occurrences carry no owner.

### Bounds

These keep a script from loading the server thread:

| Bound | Value |
| --- | --- |
| Blocks in one watched area | at most 512 (fixed) |
| Blocks watched by the whole server | `[events] max-watched-blocks`, default 2048, at most 16384; a quarter read per tick |
| Watches per run | at most 16 (fixed) |
| Watches per controller | `[limits] max-watches-per-controller` (32) |
| Cells per controller, across watches and triggers | `[limits] max-watched-blocks-per-controller` (1024) |
| Armed triggers per controller | `[limits] max-triggers-per-controller` (8) |

### Triggers

A trigger starts runs as its owner, never as whoever caused the occurrence. Arming (creating or enabling) records the
script revision, its declaration, a hash of its parameters and the owner's grant revision.

Each start checks that:

- the owner still holds that grant (a revoke disables the trigger, and a regrant does not re-arm it);
- the owner may still run the script;
- the declaration has not grown and the parameters have not changed;
- for someone else's script, it is still the approved revision. Otherwise the trigger pauses until the owner enables
  it again, which re-approves, so another controller cannot change the code a trigger runs with its owner's authority.

Also:

- runs count toward the owner's concurrent-run limit;
- the per-minute start cap throttles a trigger that feeds itself to that many starts a minute, and tells the owner
  once;
- no trigger starts while the plugin is not linked.

## Operators and achievements

From Endstone `v0.11.13`, permissions alone decide who may run a command: upstream marks every command as not
requiring cheats.

- An operator can run `/give`, `/gamemode`, `/tp` and similar commands while `allow-cheats=false`, and the world keeps
  achievements enabled.
- So `allow-cheats=false` no longer means "nobody can cheat"; it means only operators can.
- This is upstream behavior that Endbot does not patch: the Endstone delta stays limited to local-bot authentication.
- Bots are never operators, and controllers get no operator permission from Endbot.
- `endbot doctor` WARNs when `permissions.json` lists operators, or when `default-player-permission-level=operator`
  makes every player one.

If fair achievement history matters, keep the operator list empty or to people you trust not to cheat.

## Runtime control

The runtime's control endpoint is defense in depth for same-host IPC, not an Internet-facing authorization system.

- It listens only on `127.0.0.1`, `::1` or `localhost`; both the runtime and the plugin reject any other configured
  host.
- Every bounded JSON request is authenticated with a 256-bit token kept in a separate private file and compared in
  constant time.
- A missing, corrupt or wrong token fails closed.

The operator must run BDS and the runtime under restricted service accounts, and must not expose the control port
through a proxy or port-forward.

Profiles hold a UUID, a Minecraft name and the desired online state, with no credentials and no copy of world
inventory.

- Profile files reject symlinks and corruption. Creation completes a file before publishing it, like the runtime
  identity files.
- A rename keeps the UUID.
- The plugin rejects a rename that collides with an online real player. Endstone exposes no reliable complete history
  of offline GamerTags, so avoid known offline player names too.

## Runtime → BDS connection

The runtime connects its Bots only to a BDS on the same host: the configuration loader and every session refuse a
`serverHost` other than `127.0.0.1`, `::1` or `localhost`.

The runtime does not pin the server's NetherNet identity. BDS generates a new NetherNet DTLS identity on every start,
so a persistent pin would fail after every restart, and the upstream NetherNet client exposes no hook to verify it.

LAN discovery can surface other NetherNet hosts on the same machine. The runtime logs in only to the host advertising
the configured `server-name` and world display name (`level.dat` `LevelName`, falling back to `level-name` before the
world exists). It never falls back to the first host that answers, so another local program is not sent Bot login
tokens merely by answering discovery first.

The transport is NetherNet only. BDS 1.26.60 refuses players on RakNet, so the CLI rejects `transport = "raknet"`, and
doctor requires `transport=nethernet` in BDS.

### What carries the trust instead

Trust in this connection rests on the loopback path and on the local-bot token. A same-host process that impersonates
BDS could observe a Bot's inputs and feed it a fake world. It cannot turn a captured token into a login on the real
server, because the token:

- is owner-signed for one audience;
- lives at most the configured maximum lifetime;
- carries a single-use `jti`;
- is bound to the Bot's client key (`cpk`), whose private key never leaves the runtime.

A process able to take over BDS's loopback port already has privileges that can read the runtime's secrets directly,
so host account isolation ([Runtime control](#runtime-control)) is the control that matters.

## The web

The web is optional and off until an operator runs `endbot web deploy` ([deployment guide](WEB_DEPLOY.md),
[design](WEB.md)). What was checked on Cloudflare, and what was not run and so is not validated yet, is in
[COMPATIBILITY.md](COMPATIBILITY.md#web).

### The relay boundary

The Worker and its Durable Object in the operator's Cloudflare account are an untrusted relay. endbotd on the server PC
is the authority.

| Component | Does | Does not |
| --- | --- | --- |
| endbotd | Issues and verifies passkey ceremonies, issues sessions, and authorizes every relayed request at execution time through the same permission path as the game, with the current grant revision | — |
| Worker | Relays | Verify passkeys, hold accounts or make authorization decisions |

- The server PC opens no inbound port. endbotd opens one outbound WebSocket and proves itself with an Ed25519 link key.
- The private link key stays in `state/secrets/web-link.key`: mode `0600` on POSIX; on Windows it inherits the
  instance directory's ACL, so restrict that directory. A symlink is refused.
- Only the public half is on Cloudflare. TLS to the operator's own address authenticates the Worker to endbotd.
- `endbot web rotate-key` replaces the key.

### What a compromised Worker or Cloudflare account can do

It can:

- read what passes through while it is compromised, including script source, logs and the session cookies of browsers
  that visit it;
- misuse those browsers' sessions, within those players' own roles;
- serve a page asking a visiting player to approve a passkey prompt for the same address.

It cannot:

- make an authenticator sign for another address or account;
- sign in as a player who does not visit;
- link a new XUID or widen a role;
- reach grants, role changes, owner reassignment or BDS start and stop, none of which exist on the web.

Protect the Cloudflare account accordingly.

### Who can have an account

Only an XUID that holds a controller grant bound by an Xbox-authenticated join. The web principal is never the console,
even for the operator's own XUID.

Linking uses a code issued in game to that player:

- 6 symbols, valid 5 minutes, one use;
- code entry closes for 10 minutes after 10 wrong codes from one address, or after 60 server-wide.

A new passkey always starts in the game or at the trusted site, never from a signed-in browser.

### Passkeys and sessions

endbotd checks, against its own configuration rather than anything the Worker sends:

- the challenge: single use, bound to its ceremony, 2 minutes;
- the origin and RP ID;
- user presence and verification;
- the signature and the sign counter.

| Item | Rule |
| --- | --- |
| Session token | 256 random bits; endbotd stores only the SHA-256 |
| Cookie | `__Host-` prefixed, `Secure`, `HttpOnly`, `SameSite=Strict` |
| Session lifetime | 7 days idle, 30 days at most |
| Removing a passkey | Needs a recent assertion |
| Change of address | Deletes all passkeys and sessions; keeps the accounts |

### Revocation

| Action | Effect |
| --- | --- |
| Removing a controller grant | Ends that player's sessions and closes their connections at once. Every request rechecks the grant revision, so a lost close message does not extend access |
| Removing a GamerTag from `endbot.toml` | Takes effect at the next start, which ends its sessions |
| `/botweb unlink`, `endbot web revoke` | Delete the account, its passkeys and its sessions |
| A role change | Applies to the next request |

### Requests from browsers

- The Worker refuses non-GET requests and WebSocket upgrades whose `Origin` is not exactly the configured address.
- State-changing HTTP calls need `Content-Type: application/json`.
- The one exception is the trusted site's form POST to `/auth/site/return`, which comes from the site's origin. The
  Worker requires an `Origin` and the site cookie and relays it; endbotd compares that `Origin` with the configured
  issuer.
- The pages carry a strict Content-Security-Policy with no third-party scripts, `frame-ancestors 'none'`,
  `Referrer-Policy: no-referrer` and HSTS.
- The rate limits on the Worker keep floods off the link. The limits that protect anything are in endbotd.

### What leaves the PC

- Cloudflare stores the Worker, the static page, the public link key, the address and a display cache (the display
  name and whether BDS was last up).
- The Worker logs no bodies. Deploy turns off Workers logs, logpush and preview addresses.
- Script source and logs pass through Worker memory while relayed.
- The Cloudflare API token is entered at a hidden prompt or through `CLOUDFLARE_API_TOKEN`, never as an argument. It is
  never written to disk and is removed from API errors.

### The trusted external site

The trusted site is off by default ([guide](WEB_DEPLOY.md#the-optional-trusted-site),
[design](WEB.md#p12-trusted-external-site-optional-off-by-default)).

Enabling it is an explicit trust decision: **whoever controls the site or its private key can sign in as any XUID that
holds a controller grant.** It can never create or widen a grant or a role, because the XUID must already hold a grant
bound by an Xbox-authenticated join, and that is checked again on every request.

Assertions are:

- compact JWS, ES384 only;
- audience-bound to the web address;
- at most 120 s long, and single use;
- bound by an endbotd nonce to the browser that started the flow, so an assertion copied from the site's logs is
  useless elsewhere.

The nonce is consumed on the first attempt, valid or not, and dies with an endbotd restart, so no replay cache has to
survive one. In link mode the passkey ceremony that follows is named in the address, but its options go only to the
browser holding the fresh cookie the return set, once.

Keys:

- The private key stays on the site.
- Keys rotate by listing two and removing the old one. Removing a key refuses its assertions at once.
- After a suspected exposure, `endbot web revoke --site-sessions` deletes every session and passkey that came through
  the site.

Keeping someone out means revoking their controller grant, not only their web account: with the site enabled, it can
vouch for them again at the next sign-in, and `endbot web revoke` says so.

## Reporting

Do not put keys, tokens, XUIDs, world data or credentials in a public report. For an authentication vulnerability,
open a minimal private report to the maintainer before publishing details.
