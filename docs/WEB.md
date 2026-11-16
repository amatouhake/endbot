# The web

A page for controllers, hosted on a Cloudflare Worker that each operator deploys in their own account. Shipped in
0.7.0. This document describes the pairing scheme, the protocol, the account store and the supervisor policy, with
the reason for each choice. Code comments cite the decisions and settled points by number (P1 to P12, S1 to S12).
To set it up, read the [deployment guide](WEB_DEPLOY.md); the messages are in [WEB_PROTOCOL.md](WEB_PROTOCOL.md), the
security boundary in [SECURITY.md](SECURITY.md#the-web), and what was checked on Cloudflare in
[COMPATIBILITY.md](COMPATIBILITY.md#web).

It follows from [Decision 3](DIRECTION.md#decision-3-the-web-is-hosted-on-cloudflare-workers-deployed-by-each-operator)
and [Decision 4](DIRECTION.md#decision-4-web-accounts-use-passkeys-and-are-linked-to-an-xuid-once-in-game). The stack
is fixed by [web technology](DIRECTION.md#web-technology); permissions are [PERMISSIONS.md](PERMISSIONS.md); the web
link terminates in endbotd ([ENDBOTD.md](ENDBOTD.md)).

## Overview

```text
browser ──HTTPS──▶ Worker (Hono: static UI, cookie, Origin check, rate limits)
                     │ fetch / WebSocket
                     ▼
                Durable Object "server" (relay; display cache only)
                     ▲
                     │ one outbound WSS link, endbotd proves its link key
                endbotd (accounts, passkeys, sessions, authorization, operations)
                     │ loopback
                     ▼
                plugin / runtime
```

- **The Worker and Durable Object are a relay.** They serve the UI, set and clear the cookie, check Origin, apply
  coarse rate limits and forward bytes. They verify no passkey, hold no account and make no authorization decision.
- **endbotd terminates every web request**: it issues and verifies WebAuthn ceremonies, issues sessions, builds the
  `web` principal and runs the same `execute` path as in game.
- **The browser speaks the endbotd protocol** through the relay; the Durable Object only multiplexes it.

## Decisions

### P1. Pairing: `endbot web deploy` installs endbotd's public link key

`endbot web deploy` runs on the server PC. It:

1. creates (once) the link key pair, Ed25519, private key in `state/secrets/web-link.key` (owner-only, symlink
   rejected, like the other secrets);
2. uploads the prebuilt Worker bundle, its static assets and Durable Object migrations through the Cloudflare API;
3. sets the Worker secret `ENDBOTD_LINK_KEY` (public key + key id) and the plain variable `ENDBOT_ORIGIN`;
4. writes `[web]` (origin, account id, worker name) into `endbot.toml`, regenerates `endbotd.toml`; the key id is derived
   from the public key;
5. waits until endbotd reports the link up, or says why not.

This refines Decision 3's "pairing secret": because deploy runs next to endbotd, it can install the public key
directly. There is no bootstrap window in which whoever holds a pairing secret could pair first, and nothing secret
leaves the PC (S1).

**Link handshake.** endbotd opens `wss://<origin>/link`. The Durable Object sends
`{t: "challenge", protocol, nonce, origin}`; endbotd answers
`{t: "prove", protocol, endbotdVersion, keyId, sig}` with `sig = Ed25519("endbot-link-v1\n" + origin + "\n" + nonce)`.
A link that does not prove itself within 5 s is closed; at most one unproven link is held at a time. A newly proven
link replaces every other. TLS to the operator's own domain authenticates the Worker to endbotd.

**A link that goes quiet is retired from both ends.** A PC that loses power or network sends no close, and the
Durable Object can go on listing such a socket as open even after closing it, so a replaced or silent link is marked
retired and never used again. The Durable Object retires a proven link it has not heard from for 90 s, found by an alarm
set for that moment or by the next request, and closes that link's browsers so that open pages stop showing a live
server. Requests meanwhile answer `unavailable` at once. endbotd drops a link that has heard nothing back, not even the
keep-alive answer, for 90 s and connects again. Without this, after a network loss the relay kept routing to the
dead link, and the web stayed down after the PC came back.

**Rotation** is `endbot web rotate-key`: a new key pair, the secret replaced through the API, then the key file
replaced. The old private key is deleted at once, because the Worker no longer accepts it. One mechanism serves routine
rotation and suspected exposure. Because the secret is set only through the operator's Cloudflare account, rotation
needs an API token (P9). Cloudflare has taken between 2 and 5 minutes to hand the
new secret to the Durable Object, and a good proof is refused until then, so the web is offline for about that long.
For 10 minutes after it starts endbotd retries a refused proof every 10 s instead of backing off, staying under the
Durable Object's 10 link openings a minute (a faster retry is answered 429, which then counts the same). The
command waits up to 3 minutes for the link; if the proof is still refused then, it says the link should come up by
itself and that `endbot doctor --live` shows when. Otherwise endbotd's retry backs off to at most 30 s, so a PC whose network comes back is linked again within
about half a minute.

### P2. One relay protocol, versioned with the bundle

```text
link (endbotd ↔ DO)       challenge / prove, then multiplexed frames:
  open    DO → endbotd    {conn, kind: http|ws, path, origin, session?, ipBucket}
  msg     both            {conn, id, body}               body is a browser ↔ endbotd message
  close   both            {conn, reason}
  cache   endbotd → DO    {displayName, bds: up|down}    the display cache (P8)

browser ↔ endbotd        JSON with {v, id, type, ...}:
  auth.*       link code, register, login, logout                 (HTTP, P4)
  op           {operation} → Outcome                                (WebSocket)
  query        status, Bot list, Groups, own runs, own log tail    (WebSocket)
  subscribe    status | log | run:<id>  → pushed Events             (WebSocket)
  script.*     list, get, save {revision}, compile, declaration     (WebSocket)
```

- One integer, `web-protocol`, covers both layers. It is compiled into the Worker bundle and into endbotd; the UI is
  part of the same bundle, so UI and Worker always match. endbotd refuses a mismatched link, logs
  "Worker speaks web protocol N, endbotd expects M: run `endbot web deploy`", and `endbot doctor` reports it.
- TypeScript types are generated from the registry and the protocol schema in CI, as Decision 3 states.
- Frames are bounded (1 MiB, as the loopback protocols), every request has a deadline, errors are Outcome codes.
- An open browser tab that meets a newer protocol after a redeploy gets `{type: "reload"}` and reloads.

### P3. Accounts, passkeys and sessions live in endbotd's store

`state/web/` (owner-only):

| File | Holds |
|---|---|
| `accounts.json` | account id, XUID, the player's UUID and GamerTag at the latest proof, random 32-byte WebAuthn user handle, created |
| `passkeys.json` | credential id, COSE public key, sign count, RP ID, transports, created, last used, label |
| `sessions.json` | SHA-256 of the session token, account, how it was created (`passkey` or `site`), created, last seen, device label |

- Link codes and WebAuthn challenges live in memory only; an endbotd restart voids them, which costs a retry.
- The user handle is random, never the XUID, so an authenticator or a synced passkey provider does not learn it.
  The passkey's display name is the GamerTag.
- Only XUIDs with a controller grant get an account (Decision 4). A deleted grant does not delete the account (P6).
- Update and rollback: the files are new, so a pre-web version ignores them; `endbot update` backs them up with the
  other state.

### P4. WebAuthn as seen through the relay

- **RP ID** is the host of the configured origin (`endbot-alice.alice.workers.dev` or the custom domain). The origin
  and RP ID come from endbotd's own configuration, never from a header the Worker sends.
- **endbotd verifies** `clientDataJSON.type`, the challenge it issued, `clientDataJSON.origin` equal to the configured
  origin, `rpIdHash`, the UP and UV flags, the signature and the sign counter (a decrease is logged and refused for
  that credential). These values are signed by the authenticator, so a modified Worker cannot make an assertion for
  another origin or account; it can only relay what a visiting browser signs (the risk Decision 3 accepts).
- Options: discoverable credentials required, `userVerification: "required"`, attestation `none`, ES256, EdDSA and
  RS256. RS256 is there for Windows Hello: a PC whose TPM offers only RSA would otherwise be left out of the prompt.
  Login is usernameless.
- Challenges are single use, bound to the ceremony (register, login) and its account or link code, and
  expire after 2 minutes.
- **Ceremonies are HTTP**, because a WebSocket cannot set a cookie: `POST /auth/<step>`; the Worker relays the body as
  an `http` conn and, when endbotd returns a new session, sets the cookie.
- **Link flow:** `/botweb link` → endbotd issues a code (`K7Q-4M2`: 6 symbols from a 32-symbol alphabet, 5 minutes,
  single use, at most one live code per XUID) → the browser posts the code → endbotd returns registration options for
  that XUID's account → the passkey is registered and a session issued. The same path adds a passkey to an existing
  account.
- **A new passkey starts in the game (S8).** A signed-in browser cannot issue a code, so another device always takes
  `/botweb link`, or the trusted site where the operator enabled it. Adding a passkey notifies the account's sessions and,
  when online, the player in chat. An account holds at most 10 passkeys; removing one makes room.

**Changing the domain invalidates every passkey.** A passkey is bound to its RP ID, so moving from the `workers.dev`
subdomain to a custom domain, or between custom domains, makes every registered passkey unusable. `endbot web deploy`
with a different origin says so and requires `--confirm-domain-change`; it then deletes all passkeys and sessions and
keeps the accounts, so each player re-registers with `/botweb link` (one join). endbotd never accepts a credential
whose stored RP ID differs from the current one.

### P5. Sessions, cookies and CSRF

- The session token is 256 random bits; endbotd stores only its SHA-256. The cookie is
  `__Host-endbot_session=<token>; Secure; HttpOnly; SameSite=Strict; Path=/`. The `__Host-` prefix keeps any other
  host, including other Workers under the same `workers.dev` account subdomain, from setting it.
- Lifetimes: 7 days idle, 30 days absolute (S3). `last seen` is written at most once an hour.
- **CSRF:** `SameSite=Strict`; the Worker refuses every non-GET request and every WebSocket upgrade whose `Origin` is
  not exactly the configured origin, and state-changing HTTP calls also need `Content-Type: application/json`, which
  forces a CORS preflight that the Worker never grants. The WebSocket upgrade check also closes cross-site WebSocket
  hijacking.
- Every relayed `op`, `query` and `script.*` request is authorized by endbotd at execution time with the `web`
  principal: grant valid at the current grant revision, account linked, session valid, then roles, ownership
  and declarations. A session check at WebSocket open is not enough on its own.
- Headers: `Content-Security-Policy: default-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`,
  `Referrer-Policy: no-referrer`, HSTS. No third-party scripts or fonts.
- Logout deletes the session in endbotd and clears the cookie. The web device list removes a passkey or a session;
  removing a passkey needs a recent assertion.

### P6. Revocation reaches the web at once

| Event | Effect |
|---|---|
| Controller grant revoked (grant revision bumps), or removed from `endbot.toml` and seen at the next start | endbotd deletes the XUID's sessions and closes their connections (`close` frames). Account and passkeys stay; after a regrant the player signs in again |
| Role changed | Sessions stay; every later request uses the new role |
| `/botweb unlink`, `endbot web revoke <GamerTag>` | Account, passkeys and sessions deleted; connections closed |
| Passkey or session removed on the web | That one item deleted; a removed session's connections closed. Removing a passkey also ends the sessions signed in or registered with it (and closes their connections); a trusted-site session (P12) has no passkey and is not affected |
| Domain change (P4) | All passkeys and sessions deleted; accounts kept |

Because every request rechecks the grant revision, a revoke takes effect even if a `close` frame is lost.

### P7. Rate limits at both ends

- **Worker / Durable Object (coarse, in memory):** per client IP (`CF-Connecting-IP`, hashed, never stored) token
  count of 10 per minute for `/auth/*`, 30 per minute for WebSocket upgrades, a separate one for endbotd's own `/link`,
  and 60 per minute for the sign-in page's `/api/display`; at most 64 browser connections and 16 unauthenticated ones
  relayed at a time. Counters are not persisted. They keep floods off the link; they are not the
  security control. (Until the 0.7.0 release pass, upgrades shared the `/auth/*` count: signing out and in twice in a
  minute then refused the page's own socket.) A page whose socket is refused asks `/auth/devices` once; while the
  session is still good, or that request is rate limited too, it says why it is waiting (the rate limit, the session's
  socket cap, or not known) and tries again every 15 s instead of showing the sign-in page.
- **endbotd (authoritative):** wrong link codes: 10 per 10 minutes from one client address (the relay's hashed
  bucket) close code entry for that address for 10 minutes, so one address cannot stop everyone linking; 60 per 10
  minutes server-wide, whatever the bucket (the relay is not trusted to report it), close it for everyone for 10
  minutes and the console logs it (a 30-bit code with 5-minute life is then far out of reach); a link code is used
  only once there is room for its ceremony; at most 256 HTTP requests from the relay wait unanswered, for at most 30 s; at most 100
  outstanding challenges; per session 4 connections and 20 requests per second; script saves are bounded by the
  existing size limits. Existing per-controller limits (runs, Bots, triggers) apply unchanged.

### P8. What Cloudflare stores

- Static UI and the Worker code; the secret `ENDBOTD_LINK_KEY` (a public key) and `ENDBOT_ORIGIN`.
- Durable Object storage: the display cache only, `{displayName, bds, since}`, shown on the sign-in page. With no proven
  link, or one silent for 90 s, it reports `bds: down`, since nothing vouches for the last value. endbotd's keep-alive
  costs no storage or wake-up: the Workers runtime answers it and records the time. While a link is up, one alarm per
  minute or so checks that it is still heard from; each is billed as a Durable Object request.
  `displayName` is `[web] display-name`, empty by default.
- Nothing else: no account, passkey, session, script source, Outcome or log. The Worker logs no request or response
  body; deploy sets Workers observability and logpush off, and disables version preview URLs so the UI is served from
  one origin only. Script source and logs pass through Worker memory while relayed.

### P9. The Cloudflare API token is entered by the operator and never stored

- `endbot web deploy`, `rotate-key` and the redeploy during `endbot update` read the token from a hidden prompt, or
  from `CLOUDFLARE_API_TOKEN` for non-interactive use. It is never taken from argv (visible in process lists),
  never written to disk, never logged; API errors are printed with the `Authorization` header removed.
- The deployment guide lists the token permissions ([Create the API token](WEB_DEPLOY.md#create-the-api-token)):
  Editor on the chosen Worker plus account-wide Workers Metadata Read-Only; attaching a custom domain needs Editor on
  all of the account's Workers. The account id is not secret and is kept in `endbot.toml`.
- **`endbot update` with web enabled:** after the app switch it asks for the token and redeploys the matching bundle.
  Without a token it completes the update and says that the web stays offline until `endbot web deploy` runs; endbotd
  refuses the old Worker (P2). `--rollback` does the same with the older bundle.
- **The version switched to does the deploy.** The update runs that version's own `endbot web deploy` with the token in
  its environment, so the deploy code and the Worker it uploads always belong together. By then the switch has happened,
  so a failed deploy is a warning and the update still succeeds. A version with no web in its bundle is not asked to
  deploy, and says so.
- **A plain `endbot web deploy` keeps a custom domain.** The zone id is not stored and an update has nothing to ask about
  the route, so with no `--domain` the origin, the attached domain and the routes stay as they are. Going back to
  `workers.dev` takes `--confirm-domain-change`, as any new origin does (P4). If the Worker was deleted on Cloudflare, the
  deploy refuses and asks for `--domain` and `--zone-id` again.
- **A deploy never replaces a Worker it did not make.** An account often holds other Workers, and an upload replaces a
  Worker's code and bindings outright. So the deploy reads the account's list of Workers first. A Worker of that name
  that this instance's `endbot.toml` does not record is refused before anything changes, unless the operator passes
  `--replace-worker`. A list that cannot be read also stops the deploy.

### P10. endbotd keeps running while BDS is stopped

The exception to [ENDBOTD.md S2](ENDBOTD.md#settled-points):

- With `[web] enabled`, when BDS exits (console `stop` or a crash) the supervisor stops the runtime, keeps endbotd,
  logs the exit code and accepts two supervisor console commands: `start` (start runtime, then BDS, in the usual
  order) and `exit` (stop endbotd and exit with BDS's last code). `endbot stop` stops everything.
- Without the web, BDS exit stops everything. `--exit-with-bds` restores that behavior for operators who run
  Endbot under a service manager that restarts on exit (S2).
- While BDS is stopped, the web shows "server stopped", status and logs, and edits scripts. Bot operations fail with
  the temporary-failure code. Starting BDS stays on the console: controllers are not operators.
- endbotd already survives a missing plugin and runtime (ENDBOTD P2, P4, P6); only the supervisor changes.

### P11. What the web shows

- **Status:** server and BDS state in the header at all times; the Bots and Groups the principal may see, and their own
  runs and jobs, on the screens that act on them. Changes reach an open page by themselves, wherever they came from.
- **History:** the principal's own run logs, job ends and the Outcomes of their own commands, from chat or the web, as in
  chat. The full server or endbotd log is not on the web; that is console material.
- **Groups:** creating, deleting and changing members through `/botgroup`, and operating a whole Group.
- **Scripts:** the scripts the role may read or edit, saved through the in-game save path with compile,
  declaration, ownership and revision checks; a stale revision is a conflict with the current source shown.
  Diagnostics come from endbotd's compile.
- **Bot control:** every operation the in-game command offers to that principal, built from the registry, with the
  same wording from the message catalog (principle 1). Running a script shows its declaration first.
- **Triggers:** managed from the web as in game, with the declaration shown before enabling ([EVENTS.md E10](EVENTS.md)).
- **Never on the web:** grants, revocations, role changes, owner reassignment, BDS start and stop, `endbot` CLI
  operations. The web principal is never the console, even for the operator's own XUID.

### P12. Trusted external site (optional, off by default)

The site from Decision 4: an operator who already signs players in on their own site (for example with Microsoft
OAuth) lets it prove the XUID instead of the in-game code. endbotd verifies the site's signature; the Worker only
relays.

**Configuration** in `endbot.toml`, copied into `endbotd.toml`:

```toml
[web.trusted-site]
enabled = false
name = "Alice's site"                    # shown on the sign-in button
mode = "link"                            # "link" or "sign-in"
issuer = "https://alice.example"         # the iss claim, and the only Origin accepted on the return POST
start-url = "https://alice.example/endbot/start"
keys = [ { kid = "2026-10", public-key-file = "trusted-site/2026-10.pem" } ]   # P-384 public keys
```

Key files live under the instance directory, not in the repository. A key is a P-384 public key in PEM;
`endbot doctor` checks every listed file, the curve, that `kid`s are unique, that `issuer` and `start-url` are
`https://`, that `issuer` is exactly an origin as a browser spells it (endbotd compares it byte for byte with `Origin`,
and keeps the site off otherwise), and that no more than two keys are listed. The private key never touches the Endbot PC; the deployment
guide shows how to make one with OpenSSL and gives a short reference signer.

**Rotation:** add the new key next to the old one and restart endbotd (config is read at start), switch the site to the
new `kid`, then remove the old key and restart again. Removing a key refuses every assertion signed with it at once.
On suspected exposure: remove the key, restart, and run `endbot web revoke --site-sessions`, which deletes every
session issued through the site and every passkey registered through it (P5 marks each with how it was created).

**Flow.** It always starts at the Endbot web, so endbotd can bind the assertion to one browser:

```text
1. browser  GET  /auth/site/start            Worker → endbotd: new nonce (128 bits, single use, 2 minutes)
            ◀── 303 to start-url?nonce=…&return=<origin>/auth/site/return
                 + cookie __Host-endbot_site=<nonce id>; Secure; HttpOnly; SameSite=None; Path=/; Max-Age=120
2. site     signs the user in its own way, then serves an auto-submitted form:
            POST <origin>/auth/site/return   assertion=<compact JWS>
3. Worker   accepts this POST only with an Origin and the site cookie; relays both to endbotd, which compares the
            Origin with the issuer (the Worker does not know the issuer, and is not trusted to check it)
4. endbotd  verifies (below), consumes the nonce, then links, registers a passkey, or issues a session (mode)
            ◀── 303 to /?site_ceremony=<id> and a fresh site cookie (link: the page fetches the options once with
                 POST /auth/site/options, which needs that cookie),
                 / with the session cookie (sign-in), or /?site_error=<code>
```

A link started on the operator's site ("open the Endbot web") sends the user to `/auth/site/start` first; the site
needs no second integration. The site cookie is `SameSite=None` because a cross-site POST would not carry a stricter
one, and `Path=/` because browsers refuse a `__Host-` cookie with any other path; it identifies a pending nonce and grants
nothing. The session cookie (P5) is unchanged.

**The assertion** is a compact JWS. Header: `alg` exactly `ES384`, a `kid` from the configured keys, `typ`
`endbot-site+jwt`; `crit` and any other `alg` are refused. Claims, all required:

| Claim | Check |
|---|---|
| `iss` | equals `issuer` |
| `aud` | equals the configured web origin exactly |
| `sub` | the XUID, a non-empty decimal string |
| `gtg` | the GamerTag the site knows; informational (below) |
| `nonce` | the pending nonce bound to this browser's site cookie; consumed on the first attempt, valid or not |
| `iat`, `exp` | `exp - iat` at most 120 s; 30 s clock skew; not expired |

The endbotd-issued nonce replaces Decision 4's `jti`: it is single use, expires with the flow, dies with an endbotd
restart (so no replay cache has to survive one), and binds the assertion to the browser that started the flow, so an
assertion copied out of the site's logs cannot be used elsewhere (S6).

**The grant rule holds unchanged.** endbotd accepts an assertion only when `sub` is an XUID that already holds a
controller grant bound by an Xbox-authenticated join, at the current grant revision, and the same check runs again on
every request (P5). A site claim never binds a pending GamerTag to an XUID, never creates a grant and never changes a
role. `gtg` is not used for authorization; a mismatch with the bound GamerTag is logged to the console.

**Modes:**

- **link:** the assertion replaces the in-game code. The browser continues into passkey registration for that XUID's
  account (created if missing); later sign-ins use the passkey. A session is issued only after a passkey is registered.
- **sign-in:** the assertion issues a session directly (marked `via: site`) for that XUID's account, created if
  missing; no passkey is needed. Lifetimes are those of P5. Removing a passkey needs a site sign-in
  less than 5 minutes old, as P5 asks for a recent passkey assertion.

In both modes `/botweb link` and passkeys keep working, so an outage of the site never locks a player out who has a
passkey.

**Revocation** follows P6. A controller revocation ends sessions and the site path is refused until a regrant. As
Decision 4 says, `/botweb unlink` and `endbot web revoke <GamerTag>` end the current account and sessions only: with
the site enabled it can vouch again at the next sign-in. To keep someone out, revoke their controller grant or have
the site stop vouching. `endbot web revoke` prints this when the site is enabled.

**Rate limits** (P7 applies too): `/auth/site/*` shares the Worker's `/auth/*` buckets; endbotd holds at most 100
pending nonces and each nonce allows one verification, so junk POSTs without a started flow cost one lookup and lock
nobody out. Verification failures after a nonce match are logged to the console (rate-limited log lines), naming the
reason but never the assertion.

**Failure modes:**

| Failure | Browser sees | Console log |
|---|---|---|
| Site disabled, or its config invalid | no site button; `/auth/site/*` returns 404 | doctor FAIL on invalid config; endbotd keeps the site path off |
| Missing or wrong site cookie, nonce unknown or expired | "Sign-in expired, try again" | — |
| Unknown `kid`, bad signature, wrong `alg`, `iss` or `aud` | "The site's sign-in was refused" | the reason and the `kid` |
| Expired assertion | "Sign-in expired, try again" | clock skew hint |
| XUID without a controller grant | "This account has no Endbot access" | the XUID |
| Site down | the site's own error page | — |

**SECURITY.md must state:** the site is off by default; whoever controls the site or its private key can sign in as
any XUID that holds a controller grant, so enabling it is an explicit trust decision; it can never create or widen a
grant or role; assertions are ES384, audience-bound, at most 120 s, single use and bound to the browser by the nonce;
the private key stays on the site; keys rotate by listing two and removing the old one; and keeping someone out means
revoking their grant, not only their web account.

## Settled points

- **S1. Pairing installs endbotd's public link key directly** instead of a pairing secret (P1), refining Decision 3.
- **S2. endbotd keeps running when BDS exits** with the web enabled; `--exit-with-bds` restores the exit with BDS (P10).
- **S3. Sessions** last 7 days idle and 30 days absolute; removing a passkey needs a passkey
  assertion less than 5 minutes old (P5).
- **S4. The trusted external site is part of the web** (P12), optional and off by default.
- **S5. The Cloudflare API token is never stored**; it is prompted at deploy, rotate-key and update (P9).
- **S6. The site echoes an endbotd-issued nonce** instead of minting its own `jti` (P12): a leaked assertion is useless
  outside the browser that started the flow, and no replay cache must survive restarts.
- **S7. UI direction**: a plain, readable admin interface, not a Minecraft-styled one, equally usable
  on phones and PCs (responsive layout, no phone-only or desktop-only screens).
- **S8. No "add this device" on the web** (replacing that part of P4). The in-game code ties an account to the player who joined with Xbox authentication. A signed-in browser that
  could mint more would let anyone at an unattended PC plant a lasting passkey, and a notice does not prevent that. The
  cost is small: a code from the game can be entered on any device.
- **S9. The page is one screen.** On a PC the page itself
  never scrolls: a fixed header (server and its state, tabs, account menu) over a list and a detail pane that scroll
  alone. A phone shows the list and the detail as two screens with a way back. What is chosen lives in the address, so a
  reload and Back keep it, and a tab keeps its draft while another is open. Results have their own place, so nothing
  moves when one arrives. Questions use the page's own dialogs, never the browser's.
- **S10. Bots and Groups are operated from one list.** As in
  game, a Group is one more thing `/bot` can address, so the Bots tab lists Bots and then `#Groups` with the same operations.
  The Groups tab keeps what `/botgroup` does: creating, deleting and changing members.
- **S11. New is beside Search on every list.** A Bot or Group is named in a dialog and exists when it closes,
  and a refused name is corrected there. A script needs its content, so it stays a draft until it is saved; the list says
  "not saved", its button says Create, and Cancel leaves nothing behind.
- **S12. Operations are by category, with no guessed favourites.** The in-game menu's categories hold every
  operation within two clicks and no scrolling. Recent shows what this browser actually used, instead of a list chosen for
  the operator. Modes are buttons, each asking only for its own clauses. An offline Bot offers what the in-game menu offers,
  and what the in-game forms leave to the command, such as `tp ... facing`, stays there.

## Notes on the implementation

- **Deploy uses the Cloudflare API alone**, static assets included; no wrangler is bundled. The link key's id is
  derived from the public key, so `[web]` stores no key id.
- **The script editor lives in a shadow root**, where CodeMirror can add its styles without an inline `<style>`, so
  the Content-Security-Policy of P5 stays as it is.
- **An area is typed as its clauses**, as in chat (`within 6 except ~ ~-1 ~`), and is offered only where the grammar
  takes one.
- **A test runs the deployment guide's reference signer** against the real verifier, so the guide's trusted-site
  example cannot drift from P12.
- **`endbot update` and `--rollback` redeploy the Worker** of the version they switch to (P9).

## Not included

Localhost mode and LAN access ([#282](https://github.com/amatouhake/endbot/issues/282), [other hosts](DIRECTION.md#other-hosts)); Microsoft sign-in in Endbot
itself (the trusted site can provide it); site-to-site API access; offline editing or any Cloudflare-side store beyond the display cache; web administration of
grants, roles or owners; BDS start and stop from the web; other deployment targets.
