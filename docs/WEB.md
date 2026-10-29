# Web design (BOT-021)

Status: **Design draft** (2026-10-29 JST). The former open points O1–O5 are settled (see [settled points](#settled-points));
the trusted external site (P12) and open point O6 await the operator's agreement. Nothing here is implemented.

This document turns [Decision 3](DIRECTION.md#decision-3-the-web-is-hosted-on-cloudflare-workers-deployed-by-each-operator)
and [Decision 4](DIRECTION.md#decision-4-web-accounts-use-passkeys-and-are-linked-to-an-xuid-once-in-game) into a
concrete pairing scheme, protocol, account store, supervisor change and step plan. The stack is fixed by
[web technology](DIRECTION.md#web-technology); permissions are [PERMISSIONS.md](PERMISSIONS.md) (BOT-024); the web
link terminates in endbotd ([ENDBOTD.md](ENDBOTD.md)). BOT-020, BOT-021, BOT-022 and BOT-024 ship in one release with
one validation pass.

## Where we start

After BOT-020 and BOT-024:

- **endbotd owns authorization**, the stores (`state/`), runner orchestration, jobs and the Event log. Every surface
  calls `execute(Operation, Principal) -> Outcome`; Outcomes carry `code` and `data`; the message catalog is JSON.
- **The registry** (`operations.json` + `operations.schema.json`) declares every operation, its result shape and, from
  BOT-024, the capability it needs. `Principal.kind` already reserves `web`.
- **Authorization has two layers**: a valid XUID grant at the current grant revision (persisted in
  `state/grant-revisions.json`), then a per-channel condition. For `web` that condition is "valid link and session",
  which nothing implements yet.
- **Roles** operator (the console only), scripter and controller exist; revocation already ends runs and jobs.
- **The supervisor** stops runtime and endbotd when BDS exits (ENDBOTD S2).
- **No network-facing code** exists; every listener is loopback.

## Target

```text
browser ──HTTPS──▶ Worker (Hono: static UI, cookie, Origin check, rate limits)
                     │ fetch / WebSocket
                     ▼
                Durable Object "server" (relay; display cache only)
                     ▲
                     │ one outbound WSS link, endbotd proves its link key
                endbotd (accounts, passkeys, sessions, authorization, operations)
                     │ loopback (unchanged)
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
4. writes `[web]` (origin, account id, worker name, key id) into `endbot.toml`, regenerates `endbotd.toml`;
5. waits until endbotd reports the link up, or says why not.

This refines Decision 3's "pairing secret": because deploy runs next to endbotd, it can install the public key
directly. There is no bootstrap window in which whoever holds a pairing secret could pair first, and nothing secret
leaves the PC (open point O1).

**Link handshake.** endbotd opens `wss://<origin>/link`. The Durable Object sends
`{t: "challenge", protocol, nonce, origin}`; endbotd answers
`{t: "prove", protocol, endbotdVersion, keyId, sig}` with `sig = Ed25519("endbot-link-v1\n" + origin + "\n" + nonce)`.
A link that does not prove itself within 5 s is closed; at most one unproven link is held at a time. A newly proven
link replaces the old one. TLS to the operator's own domain authenticates the Worker to endbotd.

**Rotation** is `endbot web rotate-key`: a new key pair, the secret replaced through the API, the old key file kept
until the new link is up, then deleted. One mechanism serves routine rotation and suspected exposure. Because the
secret is set only through the operator's Cloudflare account, rotation needs an API token (P9).

### P2. One relay protocol, versioned with the bundle

```text
link (endbotd ↔ DO)       challenge / prove, then multiplexed frames:
  open    DO → endbotd    {conn, kind: http|ws, path, origin, session?, ipBucket}
  msg     both            {conn, id, body}               body is a browser ↔ endbotd message
  close   both            {conn, reason}
  cache   endbotd → DO    {displayName, bds: up|down}    the display cache (P8)

browser ↔ endbotd        JSON with {v, id, type, ...}:
  auth.*       link code, register, login, add-device, logout     (HTTP, P4)
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
| `accounts.json` | account id, XUID, random 32-byte WebAuthn user handle, created |
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
- Options: discoverable credentials required, `userVerification: "required"`, attestation `none`, ES256 and EdDSA.
  Login is usernameless.
- Challenges are single use, bound to the ceremony (register, login, add-device) and its account or link code, and
  expire after 2 minutes.
- **Ceremonies are HTTP**, because a WebSocket cannot set a cookie: `POST /auth/<step>`; the Worker relays the body as
  an `http` conn and, when endbotd returns a new session, sets the cookie.
- **Link flow:** `/botweb link` → endbotd issues a code (`K7Q-4M2`: 6 symbols from a 32-symbol alphabet, 5 minutes,
  single use, at most one live code per XUID) → the browser posts the code → endbotd returns registration options for
  that XUID's account → the passkey is registered and a session issued. The same path adds a passkey to an existing
  account.
- **Add this device:** a signed-in session with a passkey assertion less than 5 minutes old requests a code; the new
  device posts it and registers its own passkey. Adding a passkey notifies the account's sessions and, when online,
  the player in chat.

**Changing the domain invalidates every passkey.** A passkey is bound to its RP ID, so moving from the `workers.dev`
subdomain to a custom domain, or between custom domains, makes every registered passkey unusable. `endbot web deploy`
with a different origin says so and requires `--confirm-domain-change`; it then deletes all passkeys and sessions and
keeps the accounts, so each player re-registers with `/botweb link` (one join). endbotd never accepts a credential
whose stored RP ID differs from the current one.

### P5. Sessions, cookies and CSRF

- The session token is 256 random bits; endbotd stores only its SHA-256. The cookie is
  `__Host-endbot_session=<token>; Secure; HttpOnly; SameSite=Strict; Path=/`. The `__Host-` prefix keeps any other
  host, including other Workers under the same `workers.dev` account subdomain, from setting it.
- Lifetimes: 7 days idle, 30 days absolute (open point O3). `last seen` is written at most once an hour.
- **CSRF:** `SameSite=Strict`; the Worker refuses every non-GET request and every WebSocket upgrade whose `Origin` is
  not exactly the configured origin, and state-changing HTTP calls also need `Content-Type: application/json`, which
  forces a CORS preflight that the Worker never grants. The WebSocket upgrade check also closes cross-site WebSocket
  hijacking.
- Every relayed `op`, `query` and `script.*` request is authorized by endbotd at execution time with the `web`
  principal: grant valid at the current grant revision, account linked, session valid, then BOT-024 roles, ownership
  and declarations. A session check at WebSocket open is not enough on its own.
- Headers: `Content-Security-Policy: default-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`,
  `Referrer-Policy: no-referrer`, HSTS. No third-party scripts or fonts.
- Logout deletes the session in endbotd and clears the cookie. The web device list removes a passkey or a session;
  removing a passkey needs a recent assertion, as add-device does.

### P6. Revocation reaches the web at once

| Event | Effect |
|---|---|
| Controller grant revoked (grant revision bumps) | endbotd deletes the XUID's sessions and closes their connections (`close` frames). Account and passkeys stay; after a regrant the player signs in again |
| Role changed | Sessions stay; every later request uses the new role |
| `/botweb unlink`, `endbot web revoke <GamerTag>` | Account, passkeys and sessions deleted; connections closed |
| Passkey or session removed on the web | That one item deleted; a removed session's connections closed |
| Domain change (P4) | All passkeys and sessions deleted; accounts kept |

Because every request rechecks the grant revision, a revoke takes effect even if a `close` frame is lost.

### P7. Rate limits at both ends

- **Worker / Durable Object (coarse, in memory):** per client IP (`CF-Connecting-IP`, hashed, never stored) token
  buckets on `/auth/*` (10 per minute) and WebSocket upgrades (10 per minute); at most 64 browser connections and 16
  unauthenticated ones relayed at a time. Counters are not persisted. They keep floods off the link; they are not the
  security control.
- **endbotd (authoritative):** wrong link codes: 10 per 10 minutes server-wide, after which code entry is closed for
  10 minutes and the console logs it (a 30-bit code with 5-minute life is then far out of reach); at most 100
  outstanding challenges; per session 4 connections and 20 requests per second; script saves are bounded by the
  existing size limits. Existing per-controller limits (runs, Bots, triggers) apply unchanged.

### P8. What Cloudflare stores

- Static UI and the Worker code; the secret `ENDBOTD_LINK_KEY` (a public key) and `ENDBOT_ORIGIN`.
- Durable Object storage: the display cache only, `{displayName, online, bds, since}`, shown on the sign-in page.
  `displayName` is `[web] display-name`, empty by default.
- Nothing else: no account, passkey, session, script source, Outcome or log. The Worker logs no request or response
  body; deploy sets Workers observability and logpush off, and disables version preview URLs so the UI is served from
  one origin only. Script source and logs pass through Worker memory while relayed.

### P9. The Cloudflare API token is entered by the operator and never stored

- `endbot web deploy`, `rotate-key` and the redeploy during `endbot update` read the token from a hidden prompt, or
  from `CLOUDFLARE_API_TOKEN` for non-interactive use. It is never taken from argv (visible in process lists),
  never written to disk, never logged; API errors are printed with the `Authorization` header removed.
- The deployment guide lists the minimal token permissions: Account → Workers Scripts: Edit; for a custom domain also
  Zone → Workers Routes: Edit and DNS: Edit on that zone. The account id is not secret and is kept in `endbot.toml`.
- **`endbot update` with web enabled:** after the app switch it asks for the token and redeploys the matching bundle.
  Without a token it completes the update and says that the web stays offline until `endbot web deploy` runs; endbotd
  refuses the old Worker (P2). `--rollback` does the same with the older bundle.

### P10. endbotd keeps running while BDS is stopped

The supervisor policy change ENDBOTD S2 deferred to BOT-021:

- With `[web] enabled`, when BDS exits (console `stop` or a crash) the supervisor stops the runtime, keeps endbotd,
  logs the exit code and accepts two supervisor console commands: `start` (start runtime, then BDS, in the usual
  order) and `exit` (stop endbotd and exit with BDS's last code). `endbot stop` stops everything, as today.
- Without the web, BDS exit stops everything, as today. `--exit-with-bds` restores that behavior for operators who run
  Endbot under a service manager that restarts on exit (open point O2).
- While BDS is stopped, the web shows "server stopped", status and logs, and edits scripts. Bot operations fail with
  the temporary-failure code. Starting BDS stays on the console: controllers are not operators.
- endbotd already survives a missing plugin and runtime (ENDBOTD P2, P4, P6); only the supervisor changes.

### P11. What the web shows

- **Status:** server and BDS state, the Bot list and Groups the principal may see, the principal's own runs and jobs.
- **Logs:** the principal's own run logs and the Outcomes of their own operations, as in chat. The full server or
  endbotd log is not on the web; that is console material.
- **Scripts:** the scripts the role may read or edit (BOT-024), saved through the in-game save path with compile,
  declaration, ownership and revision checks; a stale revision is a conflict with the current source shown.
  Diagnostics come from endbotd's compile.
- **Bot control:** every operation the in-game command offers to that principal, built from the registry, with the
  same wording from the message catalog (principle 1). Running a script shows its declaration first.
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
`https://`, and that no more than two keys are listed. The private key never touches the Endbot PC; the deployment
guide shows how to make one with OpenSSL and gives a short reference signer.

**Rotation:** add the new key next to the old one and restart endbotd (config is read at start), switch the site to the
new `kid`, then remove the old key and restart again. Removing a key refuses every assertion signed with it at once.
On suspected exposure: remove the key, restart, and run `endbot web revoke --site-sessions`, which deletes every
session issued through the site (P5 marks each session with how it was created).

**Flow.** It always starts at the Endbot web, so endbotd can bind the assertion to one browser:

```text
1. browser  GET  /auth/site/start            Worker → endbotd: new nonce (128 bits, single use, 2 minutes)
            ◀── 303 to start-url?nonce=…&return=<origin>/auth/site/return
                 + cookie __Host-endbot_site=<nonce id>; Secure; HttpOnly; SameSite=None; Path=/auth/site; Max-Age=120
2. site     signs the user in its own way, then serves an auto-submitted form:
            POST <origin>/auth/site/return   assertion=<compact JWS>
3. Worker   accepts this POST only with Origin == issuer and the site cookie; relays both to endbotd
4. endbotd  verifies (below), consumes the nonce, then links, registers a passkey, or issues a session (mode)
```

A link started on the operator's site ("open the Endbot web") sends the user to `/auth/site/start` first; the site
needs no second integration. The site cookie is `SameSite=None` because a cross-site POST would not carry a stricter
one; it identifies a pending nonce and grants nothing. The session cookie (P5) is unchanged.

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
assertion copied out of the site's logs cannot be used elsewhere (open point O6).

**The grant rule holds unchanged.** endbotd accepts an assertion only when `sub` is an XUID that already holds a
controller grant bound by an Xbox-authenticated join, at the current grant revision, and the same check runs again on
every request (P5). A site claim never binds a pending GamerTag to an XUID, never creates a grant and never changes a
role. `gtg` is not used for authorization; a mismatch with the bound GamerTag is logged to the console.

**Modes:**

- **link:** the assertion replaces the in-game code. The browser continues into passkey registration for that XUID's
  account (created if missing); later sign-ins use the passkey. A session is issued only after a passkey is registered.
- **sign-in:** the assertion issues a session directly (marked `via: site`) for that XUID's account, created if
  missing; no passkey is needed. Lifetimes are those of P5. Adding a device or removing a passkey needs a site sign-in
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

Decided 2026-10-29 JST.

- **S1. Pairing installs endbotd's public link key directly** instead of a pairing secret (P1), refining Decision 3.
- **S2. endbotd keeps running when BDS exits** with the web enabled; `--exit-with-bds` restores today's exit (P10).
- **S3. Sessions** last 7 days idle and 30 days absolute; adding a device or removing a passkey needs a passkey
  assertion less than 5 minutes old (P5).
- **S4. The trusted external site is in this release** (P12), optional and off by default; the operator will use it.
- **S5. The Cloudflare API token is never stored**; it is prompted at deploy, rotate-key and update (P9).

## Open points

- **O6. The site must echo an endbotd nonce** (P12). Recommended: yes. Your site then takes `nonce` and `return` from
  the start redirect and puts `nonce` into the assertion, instead of minting its own `jti`; in exchange a leaked
  assertion is useless outside the browser that started the flow and no replay cache must survive restarts.
  Alternative: Decision 4's site-minted `jti` with a persisted replay cache, which lets the site POST without the
  start redirect but accepts a leaked assertion for up to 120 s.

## Steps

Each step is split into PRs that keep main releasable. **Claude** marks the security boundary and protocol core,
written by Claude; **delegable** marks well-specified pieces a cheaper implementer can take, with Claude reviewing.
Steps 1–4 need BOT-020 step 3c (the endbotd process); step 2 needs the BOT-024 web principal checks.

1. **Supervisor policy (P10).** `start`/`exit` console commands, `--exit-with-bds`, runtime stopped with BDS.
   Delegable.
2. **endbotd web core, no network.**
   - **2a. Account store and link codes** (P3): `state/web/`, `/botweb link|unlink|status`, `endbot web revoke`,
     revocation hooks (P6). Claude.
   - **2b. WebAuthn and sessions** (P4, P5): the pinned Python WebAuthn library, ceremonies, session tokens, the `web`
     principal's channel condition, rate limits in endbotd (P7). Claude. Tests use a software authenticator.
   - **2c. Trusted external site** (P12): `[web.trusted-site]` config and doctor checks, nonces, ES384 JWS
     verification (the existing local-bot-auth verifier's rules where they apply), both modes,
     `endbot web revoke --site-sessions`, failure codes. Claude only (security boundary). Negative tests for every
     row of the claim table and the failure table.
3. **Link and relay.**
   - **3a. Protocol schema** (P2): `docs/WEB_PROTOCOL.md`, a JSON Schema, TypeScript generation in CI. Claude.
   - **3b. endbotd link client**: `websockets`, handshake and signing, reconnect with backoff, multiplexing, version
     refusal, display cache. Claude.
   - **3c. Worker and Durable Object** (Hono, Hibernation API): routing, cookie, Origin checks, headers, coarse rate
     limits, relay, the `/auth/site/*` routes with their own cookie and Origin rule. Claude writes the auth, cookie,
     site and relay paths; the static-asset serving and Vitest
     (`@cloudflare/vitest-pool-workers`) coverage are delegable.
4. **Deploy.** `endbot web deploy|rotate-key` through the Cloudflare API, token handling (P9), domain-change
   confirmation (P4), doctor checks (link up, protocol match, origin configured). Delegable against this document,
   Claude reviewing the token handling. First confirm that static assets upload through the API alone; fall back to a
   pinned wrangler if not (Decision 3).
5. **UI: sign-in, link, devices, status and logs** (P11). Svelte 5 + Vite, SimpleWebAuthn, message catalog.
   Delegable. Playwright with a virtual authenticator against the local Workers runtime.
6. **Script editing** (BOT-013 goals): `script.*` messages over existing endbotd script operations, CodeMirror 6,
   in-house Luau highlighting, completion from the registry and `api.luau`, line-numbered compile errors, revision
   diffs. The endbotd side is delegable (it calls existing authorized operations); so is the editor.
7. **Bot control** from the registry, running scripts with declarations shown. Delegable.
8. **`endbot update` integration** (P9): redeploy after update and rollback, the offline-web message. Delegable.
9. **Documentation.** `docs/WEB_DEPLOY.md` (account, token permissions, `workers.dev` or custom domain, the
   domain-change warning, revocation, what Cloudflare stores, troubleshooting); SECURITY.md gains the web boundary.
   `WEB_DEPLOY.md` also covers the trusted site: key generation, the start/return contract, a reference signer and
   rotation. The deployment guide is delegable; the SECURITY.md section and the site contract are Claude.
10. **Validation**, inside the combined release pass. Live deploy to the operator's Cloudflare account, the operator
    entering the token. Check: link on a phone and a PC, add-device, cross-device QR sign-in, revoke during an open
    session and during a run, domain change forcing re-registration, mismatched protocol refused, rate limits, BDS
    stopped with web status and script editing working, a Worker with a wrong link key refused. With the operator's
    real site: both modes, key rotation with two keys, a replayed or foreign-browser assertion refused, an XUID
    without a grant refused, `endbot web revoke` while the site still vouches. Recorded in a docs PR.

## Not in BOT-021

Localhost mode and LAN access (after the web, [other hosts](DIRECTION.md#other-hosts)); Microsoft sign-in in Endbot
itself (the trusted site can provide it); site-to-site API access; offline editing or any Cloudflare-side store beyond the display cache; web administration of
grants, roles or owners; BDS start and stop from the web; event kinds and triggers on the web (BOT-022 adds them to
the same protocol); other deployment targets.
