# Deploying the Endbot web

The web lets your controllers sign in from a phone or a PC to see the server, edit scripts and control Bots. It runs as a
Cloudflare Worker in **your own** Cloudflare account, and your server PC opens no inbound port. The design and its
reasons are in [WEB.md](WEB.md); the security boundary is in [SECURITY.md](SECURITY.md#the-web).

> **Not validated yet.** The deploy has only run against a fake Cloudflare API, and passkeys only against a software
> authenticator. The real check is [step 10](WEB.md#steps) of the design. If the Cloudflare API behaves differently from
> what is written here, trust what you see and report it.

## What you need

- A running Endbot instance, on a version whose bundle carries the web.
- A Cloudflare account. Durable Objects are part of the Worker, so check Cloudflare's current pricing for your account.
  If you have never opened Workers in the dashboard, open it once so the account gets its `workers.dev` subdomain.
- For a custom domain only: a zone in that same account.
- Players who use the web need a controller grant, so grant them first ([operations](OPERATIONS.md#runtime-grants-and-revocations)).

No Node, npm or Wrangler is needed on the server PC.

## Create the API token

Your account may hold other Workers, so give the token as little as it needs. Cloudflare can limit an account API token
to chosen Workers, but such a token cannot create a Worker, so create the Worker first:

1. In the dashboard, create a Worker from the Hello World template, named as Endbot's Worker will be (`endbot` unless you
   pass `--worker-name`). Endbot replaces it on the first deploy.
2. Under **Manage Account → Account API Tokens**, create a token with two policies and an expiry date:

   | Scope | Role | Why |
   |---|---|---|
   | Specified Workers: the Worker from step 1 | Editor | Upload the Worker, set its secret and route |
   | All of the account's Workers | Metadata Read-Only | Read the account's `workers.dev` subdomain and its list of Workers; no code |

3. On the first deploy, add `--replace-worker` (see [Deploy](#deploy)), because the Worker exists but this instance did
   not create it.

This was checked on Cloudflare in step 10: deploy, redeploy and `rotate-key` all worked with it. A token without the
second policy fails with `GET /workers/subdomain failed (403)`. Instead of both policies, the Admin role on all Workers
also works and can create the Worker itself, but it can change or delete every Worker in the account.

For a custom domain the token also needs access to that zone. This path is not validated yet; expect Zone → Workers Routes:
Edit and Zone → DNS: Edit, and report what Cloudflare actually asks for.

Endbot never stores the token and never takes it as an argument. It reads the token from a hidden prompt or from
`CLOUDFLARE_API_TOKEN`. You need it for `endbot web deploy`, `endbot web rotate-key` and to redeploy during
`endbot update`. You can delete the token between those times.

You also need the **account id**, which the dashboard shows on the Workers overview. It is not secret, and Endbot keeps it
in `endbot.toml`.

## Choose the address now

The address cannot change for free. A passkey is bound to its domain, so moving to another address makes every registered
passkey unusable, and each player has to link again.

- **`workers.dev`**: `https://endbot.<your-subdomain>.workers.dev`, with nothing else to set up. Your Cloudflare subdomain
  is part of the address.
- **Custom domain**: the address players will keep. This is the one to pick if you may want a custom domain later.

## Deploy

On the server PC:

```bash
./endbot web deploy --account-id <account-id>
```

Or, for a custom domain:

```bash
./endbot web deploy --account-id <account-id> --domain endbot.example.com --zone-id <zone-id>
```

The domain must be lowercase with no port or path. On Windows, use `endbot.cmd`.
`--worker-name <name>` changes the Worker's name, which is `endbot` by default and is part of a `workers.dev` address.

**Each instance needs its own Worker name.** If your account already has a Worker of that name that this instance did not
deploy, the command refuses before it changes anything. Choose another name. `--replace-worker` is only for a Worker
that is yours to replace, such as this instance's own Worker after `endbot.toml` was lost: it replaces that Worker's code
and settings. Cloudflare may still refuse a Worker that has Durable Objects of its own, and then you can delete it in the
dashboard first.

With the token from [Create the API token](#create-the-api-token), the first deploy is:

```bash
./endbot web deploy --account-id <account-id> --replace-worker
```

The command:

1. creates the link key once, in `state/secrets/web-link.key`, and installs only its public half on Cloudflare;
2. uploads the prebuilt Worker and turns off Workers logs, logpush and preview addresses;
3. writes `[web]` into `endbot.toml`;
4. tells a running endbotd to load the settings and waits for the link, or says that the link comes up at the next
   `endbot start`.

Then check it, while Endbot is running:

```bash
./endbot doctor --live
```

`PASS web:` means endbotd reports the link up. To show a server name on the sign-in page, set `[web] display-name` in
`endbot.toml` and restart. That name is the one thing of yours that Cloudflare keeps.

**With `[web] enabled`, BDS stopping no longer stops Endbot.** endbotd keeps running so that the web can show the stopped
server and edit scripts. Type `start` to start BDS again or `exit` to end everything, or use `endbot stop`. If a service
manager restarts Endbot when it exits, use `endbot start --exit-with-bds`
([process lifecycle](OPERATIONS.md#5a-process-lifecycle)).

## Sign in

Each player does this once per device they want a passkey on:

1. In the game, run `/botweb link`. The code has 6 symbols, lasts 5 minutes and works once.
2. Open the web address, enter the code and create a passkey when the browser asks.

To sign in later, a player only needs the passkey. A new passkey always starts with a new code from the game, or at the
[trusted site](#the-optional-trusted-site) in `link` mode. That is on purpose: a signed-in browser cannot add a passkey, so someone at an unattended PC cannot plant one. An account holds at most
10 passkeys. A session lasts 7 days without use and 30 days at most.

What the web can do depends on the player's role and on who owns what, exactly as in the game
([PERMISSIONS.md](PERMISSIONS.md)). The web never offers grants, roles, owner changes, or starting and stopping BDS, even
for your own account. Those stay on the console.

## Updating Endbot

`endbot update` and `endbot update --rollback` redeploy the Worker of the version they switch to. They ask for the token,
or take it from `CLOUDFLARE_API_TOKEN`. Pressing Enter skips the redeploy, and with no terminal and no variable it is
skipped without asking.

A skipped or failed redeploy does not fail the update. The old Worker keeps serving, and if its web protocol differs from
the new endbotd's, the web is offline until you run:

```bash
./endbot web deploy
```

With no other arguments, this keeps the address you already have and does not touch or check its route. If you detached a
custom domain in the dashboard, attach it again by repeating the command with `--domain` and `--zone-id`. After a rollback to a version with no web, the Worker stays
on Cloudflare with nothing connected to it.

## Changing the domain

Moving to another address needs an explicit confirmation:

```bash
./endbot web deploy --domain new.example.com --zone-id <zone-id> --confirm-domain-change
```

Going back to `workers.dev` is the same command with no `--domain`. This deletes every passkey and session and keeps the
accounts, so each player runs `/botweb link` once on the new address. Without the flag, the command refuses before it
changes anything.

Moving away from a custom domain does not remove its DNS record or its Worker route from your zone. Check those in the
dashboard if you no longer want them.

## Removing access

| You want to | Do this | What happens |
|---|---|---|
| Keep someone out of Endbot and the web | `endbot controllers remove <GamerTag>` | Their sessions end at once. The account and passkeys stay for a later regrant, but do nothing until then. Joining the game is separate: this does not edit the BDS allow-list |
| Remove someone's web account only | `endbot web revoke <GamerTag>` (or `xuid:<id>`) | Account, passkeys and sessions are deleted. The grant stays, so they can link again |
| Let a player remove their own | `/botweb unlink` in the game, or the device list on the web | The account, or that one passkey or session, is deleted |
| Respond to a suspected link key exposure | `endbot web rotate-key` | A new key pair replaces the old one on the PC and on Cloudflare. The web is offline for about 2 minutes while Cloudflare starts using it, and the command waits up to 3 |
| Respond to a suspected site key exposure | Remove the key, restart, then `endbot web revoke --site-sessions` | See [rotating keys](#rotating-keys) |

`endbot web revoke` needs Endbot running. To take the web down altogether, set `[web] enabled = false` in `endbot.toml`,
restart, and delete the Worker in the Cloudflare dashboard. The accounts stay in `state/web/` in case you deploy again.

## What Cloudflare stores

- The Worker code and the static page.
- `ENDBOTD_LINK_KEY`, which holds a **public** key and its id, and `ENDBOT_ORIGIN`, the address.
- In the Durable Object: the display name and whether BDS was last up.

Accounts, passkeys, sessions, scripts and logs stay in `state/` and `scripts/` on your PC. On Windows, keep the instance
directory restricted to the account that runs Endbot, because the link key there inherits its permissions. Script source and logs pass through
Worker memory while they are relayed, and the Worker does not log them. Whoever controls your Cloudflare account can replace
the Worker, so protect that account with its own strong sign-in ([what that risks](SECURITY.md#the-web)).

## Troubleshooting

| What you see | Cause and fix |
|---|---|
| `Worker speaks web protocol N, endbotd expects M` in the log or `doctor --live` | The Worker and endbotd are from different versions. Run `endbot web deploy` |
| `no Cloudflare API token` | The prompt had no terminal. Set `CLOUDFLARE_API_TOKEN` |
| `Cloudflare API GET /workers/subdomain failed (403)` | The token lacks the Metadata Read-Only policy from [Create the API token](#create-the-api-token), or use `--domain` |
| `Cloudflare API ... failed (403)` | The message names the call. The token lacks a permission from [Create the API token](#create-the-api-token), it does not cover this Worker's name, or it belongs to another account |
| `this account has no workers.dev subdomain` | Open Workers once in the dashboard, or use `--domain` |
| `the prebuilt Worker was not found` | This install's bundle has no web. Reinstall from a release, or pass `--bundle` |
| `a Worker named ... already exists` | Another Worker has that name, or a first deploy stopped partway. Choose another `--worker-name`, or see [Deploy](#deploy) for `--replace-worker` |
| `the origin would change from ...` | You asked for another address. Read [changing the domain](#changing-the-domain) first |
| `the Worker ... no longer exists on Cloudflare` | It was deleted, so the custom domain must be attached again. Repeat with `--domain` and `--zone-id` |
| `The link is not up yet: ...` | The message names the reason. Check that the PC can reach the address over HTTPS, then run `endbot doctor --live` |
| `The link is not up yet: ... ProofRefused ... (4003)` after `rotate-key` or a deploy | Cloudflare has not started using the new key yet; it took over 3 minutes once. endbotd keeps trying every 10 s for 10 minutes, so wait and check with `endbot doctor --live` |
| `FAIL web: [web] origin ...` or `web-link.key is missing` | Run `endbot web deploy`. `endbot start` still runs, with the web off |
| The sign-in page says "Server stopped" | BDS is stopped, endbotd is stopped, or the link is down, for up to 90 s after a power cut. Run `endbot doctor --live` on the PC |
| The browser does not offer the passkey | The passkey was made for another address, or removed. Run `/botweb link` for a new one |
| "Too many wrong codes on this server" | After 10 wrong codes in 10 minutes, code entry closes for 10 minutes for the whole server, and the console logs it |
| "Too many attempts" | The Worker limits sign-in requests and new connections to 10 per minute per client address. Wait a minute |

## The optional trusted site

Off by default. If you already sign your players in on your own site, for example with Microsoft sign-in, that site can
prove who they are instead of the code from the game.

**This is a trust decision.** Whoever controls the site or its private key can sign in as any player who holds a controller
grant. The site cannot create or widen a grant or a role, because endbotd only accepts an XUID that already holds a grant
bound by an Xbox-authenticated join, and checks that again on every request.

The two modes:

- **`link`**: the site replaces the code from the game once. The player then registers a passkey and uses it from then on.
- **`sign-in`**: the site signs the player in every time, with no passkey.

In both modes `/botweb link` and existing passkeys keep working, so a player with a passkey is not locked out if the site is
down.

### Make the key

The private key never touches the Endbot PC. On the site's side:

```bash
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-384 -out site-private.pem
openssl pkey -in site-private.pem -pubout -out 2026-10.pem
```

Copy only the public `2026-10.pem` into the Endbot instance, for example as `trusted-site/2026-10.pem`.

### Configure Endbot

In `endbot.toml`:

```toml
[web.trusted-site]
enabled = true
name = "Alice's site"                    # shown on the sign-in button
mode = "link"                            # "link" or "sign-in"
issuer = "https://alice.example"         # exactly your site's origin: no path and no trailing slash
start-url = "https://alice.example/endbot/start"
keys = [ { kid = "2026-10", public-key-file = "trusted-site/2026-10.pem" } ]   # at most two
```

A relative key path is resolved from the instance directory. `endbot doctor` checks this section. A broken section is a
`FAIL` and keeps the site path off, while passkeys and `/botweb link` go on working. Restart Endbot after changing it,
because the section is read at start.

### What your site must do

The flow always starts at the Endbot web, which ties it to one browser.

1. **The player goes to `/auth/site/start`.** To start from your own site, link to
   `https://<endbot-address>/auth/site/start`.
2. **Your `start-url` receives the browser**, with the query parameters `nonce` and `return`. Sign the player in your own
   way.
3. **Your site answers with a form that submits itself** to `return`, with one field, `assertion`:

   ```html
   <form method="post" action="https://endbot.example.com/auth/site/return">
     <input type="hidden" name="assertion" value="eyJhbGciOi...">
   </form>
   <script>document.forms[0].submit();</script>
   ```

Rules your site must keep:

- **Check `return`.** It must equal your Endbot address plus `/auth/site/return`. Never post an assertion anywhere else.
- **Finish within 2 minutes of the start.** The nonce and the browser's cookie expire then. A player who needs longer sees
  "Sign-in expired, try again", so a start page that sends a signed-out player through a long sign-in may need a retry.
- **Send a real `Origin` on the POST.** Endbot accepts the POST only when its `Origin` equals `issuer`. Serve the form from
  that origin. Do not serve this page with `Referrer-Policy: no-referrer` or `same-origin`, or in a sandboxed frame, because browsers
  then send `Origin: null`.
- **Sign only what you have verified.** `sub` is the Xbox XUID of the player you signed in.
- **Do not log assertions**, even though a copied assertion is useless outside the browser that started the flow.

### The assertion

A compact JWS, with base64url parts and no padding. Header: `alg` exactly `ES384`, `kid` from your configured keys, `typ`
exactly `endbot-site+jwt`, and no `crit`. The signature is the 96-byte `r || s` form that JWS uses, not DER. Claims, all
required:

| Claim | Value |
|---|---|
| `iss` | your `issuer` |
| `aud` | your Endbot address, exactly (`https://endbot.example.com`) |
| `sub` | the XUID, as a decimal string |
| `gtg` | the GamerTag you know; a mismatch is only logged, never used to decide |
| `nonce` | the `nonce` you received, unchanged |
| `iat`, `exp` | integers in seconds; `exp - iat` at most 120. Clocks may differ by up to 30 s |

A reference signer in Python, using the `cryptography` package:

<!-- reference-signer: plugin/endbot/tests/test_web_site.py runs this block against the real verifier. -->
```python
import base64
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def _part(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_assertion(private_key_pem: bytes, kid: str, issuer: str, endbot_address: str, xuid: str, gamertag: str,
                   nonce: str) -> str:
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    now = int(time.time())
    header = {"alg": "ES384", "kid": kid, "typ": "endbot-site+jwt"}
    claims = {"iss": issuer, "aud": endbot_address, "sub": xuid, "gtg": gamertag, "nonce": nonce,
              "iat": now, "exp": now + 60}
    signed = f"{_part(json.dumps(header).encode())}.{_part(json.dumps(claims).encode())}"
    r, s = decode_dss_signature(key.sign(signed.encode("ascii"), ec.ECDSA(hashes.SHA384())))
    return f"{signed}.{_part(r.to_bytes(48, 'big') + s.to_bytes(48, 'big'))}"
```

### What the player sees

| Problem | The player sees | The console logs |
|---|---|---|
| The flow expired, or the browser's cookie is missing | "Sign-in expired, try again" | Nothing, or a clock hint if the assertion was outside its time |
| Unknown `kid`, bad signature, wrong `alg`, `iss`, `aud` or `Origin`, wrong nonce | "The site's sign-in was refused" | The reason and the `kid`, never the assertion |
| The XUID has no bound controller grant | "This account has no Endbot access" | The XUID |

If the XUID's grant has no binding yet, the player must join the server once first.

### Rotating keys

1. Make the new key pair, add the new public key next to the old one in `keys` (two at most), and restart Endbot.
2. Switch the site to the new `kid`.
3. Remove the old key from `keys` and restart again.

Removing a key refuses every assertion signed with it at once. **On a suspected exposure**, remove the key, restart, and
run:

```bash
./endbot web revoke --site-sessions
```

This deletes every session issued through the site and every passkey registered through it.

To keep someone out, revoke their controller grant. `/botweb unlink` and `endbot web revoke` end the current account and
sessions only, and with the site enabled it can vouch for that player again at the next sign-in.
