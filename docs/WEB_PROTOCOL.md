# Web protocol

The relay protocol between the browser, the Worker and endbotd (design: [WEB.md P1, P2](WEB.md)). The machine-readable
form is [`core/src/endbotd/web_protocol.schema.json`](../core/src/endbotd/web_protocol.schema.json); TypeScript types are
generated from it, and `web/test/relay.test.ts` checks the valid and invalid frames in `web/protocol-examples.json`
against it. endbotd keeps no schema validator: its parser treats every field as untrusted.

One integer, `web-protocol`, covers every layer. It is compiled into the Worker bundle and into endbotd. A mismatch is
refused: endbotd logs `Worker speaks web protocol N, endbotd expects M: run "endbot web deploy"`.

Current version: **2**.

## Link: endbotd to the Durable Object

endbotd opens `wss://<origin>/link`. Every frame is one JSON text message of at most 1 MiB.

| Frame | Direction | Fields |
|---|---|---|
| `challenge` | DO → endbotd | `protocol`, `nonce`, `origin` |
| `prove` | endbotd → DO | `protocol`, `endbotdVersion`, `keyId`, `sig` |
| `ready` | DO → endbotd | none: the proof was accepted, and this link replaced any earlier one |
| `open` | DO → endbotd | `conn`, `kind` (`http` or `ws`), `path`, `origin`, `session`, `siteCookie`, `ipBucket` |
| `msg` | both | `conn`, `id`, `body` |
| `close` | both | `conn`, `reason` |
| `cache` | endbotd → DO | `displayName`, `bds` (`up` or `down`) |

- **Keep-alive:** every 30 s, however busy the link is, endbotd sends the plain text `link-ping`. The Workers runtime answers
  `link-pong` itself, without waking the Durable Object or storing anything, and records the time. With no answer time and no
  opening within 90 s, the Durable Object retires the link (close code 4005) and closes its browsers, because a power cut
  sends no close; `/api/display` then reports `bds: down`. endbotd likewise drops a link that has received nothing, not
  even `link-pong`, for 90 s.
- `sig` is the base64url Ed25519 signature of `"endbot-link-v1\n" + origin + "\n" + nonce`. The Durable Object verifies it
  with the key in `ENDBOTD_LINK_KEY` and closes a link that does not prove itself within 5 s (4002) or proves wrongly
  (4003). It holds at most one unproven link. A newly proven link replaces every other (4004). A retired link is never
  used again, even while the runtime still lists its socket as open.
- endbotd checks that `challenge.origin` is its own configured origin before signing, so a proof is useless elsewhere.
- `conn` is a string chosen by the Durable Object, unique for the life of the link.
- `session` is the token from the `__Host-endbot_session` cookie, or null; `siteCookie` the value of `__Host-endbot_site`,
  or null. `ipBucket` is a hash for coarse limits, never an address.
- Frames from an unproven link are never relayed, and an unproven link that sends anything but a valid proof is closed.

### An HTTP request

The Durable Object sends `open` with `kind: "http"`, then one `msg` whose `body` is the JSON request body, `{}` for a GET
or an empty body. endbotd answers with one `msg`, then `close`. The answer's `body` is:

| Field | Meaning |
|---|---|
| `status` | the HTTP status |
| `body` | the JSON response body |
| `location` | the redirect target, with status 303 |
| `session` | a new session token: the Worker sets the cookie and never forwards the token in a body |
| `clearSession` | true: the Worker clears the session cookie |
| `siteCookie` | a pending site flow: the Worker sets `__Host-endbot_site` |

A request with no answer within 15 s gets status 504 from the Worker. With no link up it gets 503.

### A WebSocket

The Durable Object sends `open` with `kind: "ws"` when a browser upgrades, then one `msg` per browser message. Messages
from endbotd go to that browser as they are. `close` from either side ends the connection; endbotd sends it when the
session ends for any reason.

## Browser to endbotd, over the WebSocket

Each message is `{v, id, type, ...}`. `v` is the protocol version. A message with another `v` gets `{type: "reload"}`, and
the page reloads. Every request is authorized again at execution time with the `web` principal.

| `type` | Request | Answer |
|---|---|---|
| `query` | `what`: `status`, `bots`, `groups`, `runs`, `log` | `{id, type: "result", code, data}` |
| `query` | `what`: `bot`, `name` | the Bot's status as fields in `data`; it stays out of the history |
| `subscribe` | `topic`: `status`, `log`, or `trigger:<owner>/<name>` | `{id, type: "result", code}`, then `{type: "event", topic, data}` |
| `unsubscribe` | `topic` | `{id, type: "result", code}` |
| `op` | `operation`: `{op, target, params}`, one registry operation | `{id, type: "result", code, messages, data}` |
| `group.create`, `group.delete` | `name`, without `#` | `code`, `messages` |
| `group.add`, `group.remove` | `name`, `bot` | `code`, `messages` |
| `script.list`, `script.templates` | none | `{id, type: "result", code, data}` |
| `script.get`, `script.declaration`, `script.delete` | `name` (`revision` on delete) | as above |
| `script.compile` | `name`, `source` | `code`, and `line` when an error names one |
| `script.save` | `name`, `source`, `revision` (null for new) | `code`, `data`; `conflict` carries `current` |
| `script.run` | `name`, `revision`, `requires`, `values` | `code`, `messages` |
| `script.stop` | `run` | `code`, `messages` |
| `script.copy`, `script.rename` | `name`, `new`, `revision` | `code`, `messages` |
| `script.visibility` | `name`, `visibility`, `revision` | `code`, `messages` |
| `script.restore` | `name`, `index`, `revision` | `code`, `messages` |
| `event.kinds` | none | `{id, type: "result", code, data}`; `data.kinds` is the registry `events` object |
| `trigger.list` | none | `data`: `{triggers, canArm, limit}` |
| `trigger.get` | `name`, optional `owner` | `data`: `{trigger, requires}` |
| `trigger.create` | `script`, `kind`, `filter`, `arguments` (list of strings), optional `name`, `visibility`, `policy` | `code`, `data`: `{trigger}`, `messages` |
| `trigger.update` | `name`, optional `owner`, `policy` and/or `visibility` | `code`, `data`: `{trigger}` |
| `trigger.enable`, `trigger.disable`, `trigger.remove` | `name`, optional `owner` | `code`, `data`, `messages` |

A trigger in `data` is a plain JSON object: `name`, `owner` (the owner key), `ownerName`, `visibility`, `kind`, `filter`
(areas as their clause text), `script`, `arguments`, `status`, `reason`, `policy`, `stats`, `approved.declaration`, and
`own` (whether the principal owns it). `approved.playerId` and `approved.grantRevision` are never sent. A `trigger.get`
also carries the script's current declaration as `requires`, for the confirmation before enabling.

A `trigger:<owner>/<name>` subscription pushes `{type: "event", topic, data: {trigger}}` after every stored state or
stats change of that trigger, to pages whose current principal may still see it; a revoked session gets nothing.
A removed trigger is pushed once as `data: {removed: true, name, owner}`. Notices are sent from their own thread,
never from the one that starts runs, and a page that cannot keep up may miss some; `trigger.list` and `trigger.get`
always answer the current state.

endbotd also pushes `{type: "event", topic: "account", data: {kind: "passkey_added"}}` to every open page of an account
that subscribed to anything, so a passkey added elsewhere is seen at once.

A `status` event carries the same `data` as `query status` and is sent when that data changes, wherever the change came
from. A `log` event carries one entry: `{time, kind, level, lines}`, with `run` and `script` for a script, or `command` and
`code` for one of the player's own commands (`kind` is `command`). A saved script's source is not repeated in `command`.

`code` is `ok` or an Outcome code. Unknown types answer `unsupported`. A refused session answers `signed_out`, and endbotd
closes the connection.

## HTTP routes

All are `POST` with `Content-Type: application/json`, except `GET /auth/site/start`. See [WEB.md P4, P5, P12](WEB.md).

| Path | Body | Answer body |
|---|---|---|
| `/auth/info` | none | `{site: {name, mode} or null}` |
| `/auth/link/options` | `{code}` | `{ceremony, options}` |
| `/auth/link/verify` | `{ceremony, credential, label}` | `{gamertag}`, with a session |
| `/auth/login/options` | none | `{ceremony, options}` |
| `/auth/login/verify` | `{ceremony, credential, label}` | `{gamertag}`, with a session |
| `/auth/logout` | none | `{}`, clears the session |
| `/auth/devices` | none | `{sessions, passkeys}` |
| `/auth/session/remove` | `{id}` | `{}` |
| `/auth/passkey/remove` | `{id}` | `{}` |
| `/auth/site/start` | none, `GET` | 303 to the site |
| `/auth/site/return` | form field `assertion` | 303 to `/`, `/?site_ceremony=` or `/?site_error=` |
| `/auth/site/options` | `{ceremony}` | `{ceremony, options}` |

Refusals carry `{code}`: `invalid_request`, `invalid_code`, `link_closed`, `no_access`, `expired`, `passkey_refused`,
`passkey_limit`,
`signed_out`, `reauthenticate`, `not_found`, `busy`, `unavailable`, `site_expired`, `site_refused`.
