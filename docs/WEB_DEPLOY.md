# Deploying the Endbot web

The web lets your controllers sign in from a phone or a PC to see the server, edit scripts and control Bots. It runs as
a Cloudflare Worker in **your own** Cloudflare account, and your server PC opens no inbound port.

The design and its reasons are in [WEB.md](WEB.md), the security boundary in [SECURITY.md](SECURITY.md#the-web), and
what was checked on Cloudflare in [COMPATIBILITY.md](COMPATIBILITY.md#web). If Cloudflare behaves differently from what
is written here, trust what you see and report it.

**Contents**

- [What you need](#what-you-need)
- [Create the API token](#create-the-api-token)
- [Choose the address now](#choose-the-address-now)
- [Deploy](#deploy)
- [Sign in](#sign-in)
- [Updating Endbot](#updating-endbot)
- [Changing the domain](#changing-the-domain)
- [Removing access](#removing-access)
- [The optional trusted site](#the-optional-trusted-site)
- [Troubleshooting](#troubleshooting)
- [What Cloudflare stores](#what-cloudflare-stores)

## What you need

- A running Endbot instance, on a version whose bundle carries the web.
- A Cloudflare account. Durable Objects are part of the Worker, so check Cloudflare's current pricing for your account.
  If you have never opened Workers in the dashboard, open it once so the account gets its `workers.dev` subdomain.
- For a custom domain only: a zone in that same account.
- Players who use the web need a controller grant, so grant them first
  ([operations](OPERATIONS.md#runtime-grants-and-revocations)).

No Node, npm or Wrangler is needed on the server PC.

## Create the API token

Your account may hold other Workers, so give the token as little as it needs. A token limited to chosen Workers cannot
create a Worker, so create the Worker first:

1. In the dashboard, create a Worker from the Hello World template, named as Endbot's Worker will be (`endbot` unless
   you pass `--worker-name`). Endbot replaces it on the first deploy.
2. Under **Manage Account → Account API Tokens**, create a token with these two policies and an expiry date:

   | Scope | Role | Why |
   |---|---|---|
   | Specified Workers: the Worker from step 1 | Editor | Upload the Worker, set its secret and route |
   | All of the account's Workers | Metadata Read-Only | Read the account's `workers.dev` subdomain and its list of Workers; no code |

3. Note the **account id** from the Workers overview. It is not secret; Endbot keeps it in `endbot.toml`.
4. On the first deploy, add `--replace-worker` ([Deploy](#deploy)), because the Worker exists but this instance did not
   create it.

Notes:

- Without the Metadata Read-Only policy, a deploy fails with `GET /workers/subdomain failed (403)`.
- The Admin role on all Workers also works instead of both policies, and can create the Worker itself. It can also
  change or delete every Worker in the account.

**A custom domain needs much more.**

- Attaching a domain (`PUT /workers/domains`) is refused (403) unless the token has **Editor on all of the account's
  Workers**. Editor on the chosen Worker is not enough.
- Add Workers Routes: Edit and DNS: Edit on the zone as well; that combination is known to work.
- Editor on all Workers can change every Worker in the account. For a deploy that attaches or changes a domain, create
  a separate token with a short expiry, use it once, and delete it.
- Later redeploys and `rotate-key` keep the attached domain and work with the narrow token.

**How Endbot uses the token.**

- It never stores the token and never takes it as an argument. It reads it from a hidden prompt or from
  `CLOUDFLARE_API_TOKEN`.
- You need it for `endbot web deploy`, `endbot web rotate-key` and the redeploy during `endbot update`. You can delete
  the token between those times.

## Choose the address now

The address cannot change for free. A passkey is bound to its domain, so moving to another address makes every
registered passkey unusable, and each player has to link again.

| Address | What it looks like | Choose it when |
|---|---|---|
| `workers.dev` | `https://endbot.<your-subdomain>.workers.dev` | You want nothing else to set up. Your Cloudflare subdomain is part of the address |
| Custom domain | Your own host name | You may want a custom domain at any point: this is the address players keep |

## Deploy

1. On the server PC, deploy. With the token from [Create the API token](#create-the-api-token), the first deploy is:

   ```bash
   ./endbot web deploy --account-id <account-id> --replace-worker
   ```

   For a custom domain, add the domain and its zone (keep `--replace-worker` if you created the Worker in
   [Create the API token](#create-the-api-token)):

   ```bash
   ./endbot web deploy --account-id <account-id> --domain endbot.example.com --zone-id <zone-id>
   ```

2. While Endbot is running, check the link:

   ```bash
   ./endbot doctor --live
   ```

   `PASS web:` means endbotd reports the link up.

3. Optional: to show a server name on the sign-in page, set `[web] display-name` in `endbot.toml` and restart. That
   name is the one thing of yours that Cloudflare keeps.

On Windows, use `endbot.cmd`.

| Option | Meaning |
|---|---|
| `--account-id <id>` | The Cloudflare account. Stored in `endbot.toml` after the first deploy |
| `--domain <host>` | A custom domain instead of `workers.dev`: lowercase, with no port or path. A later plain deploy keeps it |
| `--zone-id <id>` | The zone of `--domain` |
| `--worker-name <name>` | The Worker's name, `endbot` by default. It is part of a `workers.dev` address |
| `--replace-worker` | Replace a Worker of that name that this instance did not deploy (see below) |
| `--confirm-domain-change` | Accept a new address, which deletes every passkey and session ([Changing the domain](#changing-the-domain)) |
| `--bundle <path>` | Use another prebuilt web bundle instead of the installed one |

What the command does:

1. Creates the link key once, in `state/secrets/web-link.key`, and installs only its public half on Cloudflare.
2. Uploads the prebuilt Worker and turns off Workers logs, logpush and preview addresses.
3. Writes `[web]` into `endbot.toml`.
4. Tells a running endbotd to load the settings and waits for the link, or says that the link comes up at the next
   `endbot start`.

**Each instance needs its own Worker name.**

- If the account already has a Worker of that name that this instance did not deploy, the command refuses before it
  changes anything. Choose another name.
- `--replace-worker` is only for a Worker that is yours to replace: the Hello World Worker from the token steps, or
  this instance's own Worker after `endbot.toml` was lost. It replaces that Worker's code and settings.
- Cloudflare may still refuse to replace a Worker that has Durable Objects of its own. Delete it in the dashboard
  first.

**With `[web] enabled`, BDS stopping no longer stops Endbot.** endbotd keeps running so that the web can show the
stopped server and edit scripts.

- Type `start` to start BDS again, or `exit` to end everything; `endbot stop` also stops everything.
- If a service manager restarts Endbot when it exits, use `endbot start --exit-with-bds`
  ([with the web enabled](OPERATIONS.md#with-the-web-enabled)).

## Sign in

Each player does this once per device they want a passkey on:

1. In the game, run `/botweb link`. The code has 6 symbols, lasts 5 minutes and works once.
2. Open the web address, enter the code, and create a passkey when the browser asks.

After that, a player signs in with the passkey alone.

| Rule | Value |
|---|---|
| Passkeys per account | At most 10 |
| Session lifetime | 7 days without use, 30 days at most |
| Adding a passkey | Always starts with a new code from the game, or the [trusted site](#the-optional-trusted-site) in `link` mode |

A signed-in browser cannot add a passkey. That is on purpose: someone at an unattended PC cannot plant one.

What the web can do depends on the player's role and on who owns what, exactly as in the game
([PERMISSIONS.md](PERMISSIONS.md)). The web never offers grants, roles, owner changes, or starting and stopping BDS,
even for your own account. Those stay on the console.

## Updating Endbot

`endbot update` and `endbot update --rollback` redeploy the Worker of the version they switch to.

- They ask for the token, or take it from `CLOUDFLARE_API_TOKEN`.
- Pressing Enter skips the redeploy. With no terminal and no variable, it is skipped without asking.
- A skipped or failed redeploy does not fail the update. The old Worker keeps serving.

If the old Worker's web protocol differs from the new endbotd's, the web is offline until you run:

```bash
./endbot web deploy
```

With no other arguments, this keeps the address you already have and does not touch or check its route.

- If you detached a custom domain in the dashboard, attach it again by repeating the command with `--domain` and
  `--zone-id`.
- After a rollback to a version with no web, the Worker stays on Cloudflare with nothing connected to it.

## Changing the domain

Moving to another address needs an explicit confirmation:

```bash
./endbot web deploy --domain new.example.com --zone-id <zone-id> --confirm-domain-change
```

Going back to `workers.dev` is the same command with no `--domain`. Without the flag, the command refuses before it
changes anything.

What a change does:

- It deletes every passkey and session and keeps the accounts. Each player runs `/botweb link` once on the new
  address.
- It does not remove the old custom domain's DNS record or Worker route from your zone. Check those in the dashboard
  if you no longer want them.

## Removing access

| You want to | Do this | What happens |
|---|---|---|
| Keep someone out of Endbot and the web | `endbot controllers remove <GamerTag>` | Their sessions end at once. The account and passkeys stay for a later regrant, but do nothing until then. Joining the game is separate: this does not edit the BDS allow-list |
| Remove someone's web account only | `endbot web revoke <GamerTag>` (or `xuid:<id>`) | Account, passkeys and sessions are deleted. The grant stays, so they can link again |
| Let a player remove their own | `/botweb unlink` in the game, or the device list on the web | The account, or that one passkey or session, is deleted |
| Respond to a suspected link key exposure | `endbot web rotate-key` | A new key pair replaces the old one on the PC and on Cloudflare. The web is offline for about 2 minutes while Cloudflare starts using it; the command waits up to 3 |
| Respond to a suspected site key exposure | Remove the key, restart, then `endbot web revoke --site-sessions` | See [rotating keys](#rotating-keys) |
| Take the web down altogether | Set `[web] enabled = false` in `endbot.toml`, restart, and delete the Worker in the Cloudflare dashboard | The accounts stay in `state/web/` in case you deploy again |

`endbot web revoke` needs Endbot running.

## The optional trusted site

Off by default. If you already sign your players in on your own site, for example with Microsoft sign-in, that site
can prove who they are instead of the code from the game.

**This is a trust decision.** Whoever controls the site or its private key can sign in as any player who holds a
controller grant. The site cannot create or widen a grant or a role: endbotd only accepts an XUID that already holds a
grant bound by an Xbox-authenticated join, and checks that again on every request.

| Mode | What the site does |
|---|---|
| `link` | Replaces the code from the game once. The player then registers a passkey and uses it from then on |
| `sign-in` | Signs the player in every time, with no passkey |

In both modes `/botweb link` and existing passkeys keep working, so a player with a passkey is not locked out if the
site is down.

### Make the key

The private key never touches the Endbot PC. On the site's side:

```bash
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-384 -out site-private.pem
openssl pkey -in site-private.pem -pubout -out 2026-10.pem
```

Copy only the public `2026-10.pem` into the Endbot instance, for example as `trusted-site/2026-10.pem`.

### Configure Endbot

1. In `endbot.toml`, add:

   ```toml
   [web.trusted-site]
   enabled = true
   name = "Alice's site"                    # shown on the sign-in button
   mode = "link"                            # "link" or "sign-in"
   issuer = "https://alice.example"         # exactly your site's origin: no path and no trailing slash
   start-url = "https://alice.example/endbot/start"
   keys = [ { kid = "2026-10", public-key-file = "trusted-site/2026-10.pem" } ]   # at most two
   ```

2. Run `endbot doctor`. It checks this section.
3. Restart Endbot. The section is read at start, so restart after every change.

A relative key path is resolved from the instance directory. A broken section is a `FAIL` in `endbot doctor` and keeps
the site path off, while passkeys and `/botweb link` go on working.

### What your site must do

The flow always starts at the Endbot web, which ties it to one browser.

1. **The player goes to `/auth/site/start`.** To start from your own site, link to
   `https://<endbot-address>/auth/site/start`.
2. **Your `start-url` receives the browser**, with the query parameters `nonce` and `return`. Sign the player in your
   own way.
3. **Your site answers with a form that submits itself** to `return`, with one field, `assertion`:

   ```html
   <form method="post" action="https://endbot.example.com/auth/site/return">
     <input type="hidden" name="assertion" value="eyJhbGciOi...">
   </form>
   <script>document.forms[0].submit();</script>
   ```

Rules your site must keep:

- **Check `return`.** It must equal your Endbot address plus `/auth/site/return`. Never post an assertion anywhere
  else.
- **Finish within 2 minutes of the start.** The nonce and the browser's cookie expire then, and the player sees
  "Sign-in expired, try again". A start page that sends a signed-out player through a long sign-in may need a retry.
- **Send a real `Origin` on the POST.** Endbot accepts the POST only when its `Origin` equals `issuer`, so serve the
  form from that origin. Do not serve it with `Referrer-Policy: no-referrer` or `same-origin`, or in a sandboxed
  frame: browsers then send `Origin: null`.
- **Sign only what you have verified.** `sub` is the Xbox XUID of the player you signed in.
- **Do not log assertions**, even though a copied assertion is useless outside the browser that started the flow.

### The assertion

A compact JWS, with base64url parts and no padding. The signature is the 96-byte `r || s` form that JWS uses, not DER.

| Header field | Value |
|---|---|
| `alg` | Exactly `ES384` |
| `kid` | One of your configured keys |
| `typ` | Exactly `endbot-site+jwt` |
| `crit` | Must be absent |

Claims, all required:

| Claim | Value |
|---|---|
| `iss` | Your `issuer` |
| `aud` | Your Endbot address, exactly (`https://endbot.example.com`) |
| `sub` | The XUID, as a decimal string |
| `gtg` | The GamerTag you know; a mismatch is only logged, never used to decide |
| `nonce` | The `nonce` you received, unchanged |
| `iat`, `exp` | Integers in seconds; `exp - iat` at most 120. Clocks may differ by up to 30 s |

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

Removing a key refuses every assertion signed with it at once.

**On a suspected exposure:**

1. Remove the key from `keys`.
2. Restart Endbot.
3. Run:

   ```bash
   ./endbot web revoke --site-sessions
   ```

   This deletes every session issued through the site and every passkey registered through it.

To keep someone out, revoke their controller grant. `/botweb unlink` and `endbot web revoke` end the current account
and sessions only; with the site enabled, it can vouch for that player again at the next sign-in.

## Troubleshooting

**Deploy and update.**

| What you see | Cause and fix |
|---|---|
| `no Cloudflare API token` | The prompt had no terminal. Set `CLOUDFLARE_API_TOKEN` |
| `Cloudflare API GET /workers/subdomain failed (403)` | The token lacks the Metadata Read-Only policy from [Create the API token](#create-the-api-token). Add it, or use `--domain` |
| `Cloudflare API ... failed (403)` | The message names the call. The token lacks a permission from [Create the API token](#create-the-api-token), does not cover this Worker's name, or belongs to another account |
| `this account has no workers.dev subdomain` | Open Workers once in the dashboard, or use `--domain` |
| `the prebuilt Worker was not found` | This install's bundle has no web. Reinstall from a release, or pass `--bundle` |
| `a Worker named ... already exists` | Another Worker has that name, or a first deploy stopped partway. Choose another `--worker-name`, or see [Deploy](#deploy) for `--replace-worker` |
| `the origin would change from ...` | You asked for another address. Read [Changing the domain](#changing-the-domain) first |
| `the Worker ... no longer exists on Cloudflare` | It was deleted, so the custom domain must be attached again. Repeat with `--domain` and `--zone-id` |

**The link.**

| What you see | Cause and fix |
|---|---|
| `The link is not up yet: ...` | The message names the reason. Right after a deploy, the check can still reach the old Worker for about 30 s; the link then comes up by itself, so do not deploy again. Otherwise check that the PC can reach the address over HTTPS, then run `endbot doctor --live` |
| `The link is not up yet: ... ProofRefused ... (4003)` after `rotate-key` or a deploy | Cloudflare has not started using the new key yet, which can take more than 3 minutes. endbotd keeps trying every 10 s for 10 minutes, so wait and check with `endbot doctor --live` |
| `Worker speaks web protocol N, endbotd expects M` in the log or `doctor --live` | The Worker and endbotd are from different versions. Run `endbot web deploy` |
| `FAIL web: [web] origin ...` or `FAIL web: ... web-link.key is missing or not a regular file` | Run `endbot web deploy`. `endbot start` still runs, with the web off |

**In the browser.**

| What you see | Cause and fix |
|---|---|
| The sign-in page says "Server stopped" | BDS is stopped, endbotd is stopped, or the link is down (for up to 90 s after a power cut). A Worker and endbotd from different versions look the same on the page; the log names that case. Run `endbot doctor --live` on the PC |
| A page opened just after the server PC lost power keeps loading | The relay takes about a minute to retire the dead link. After that, the page shows the server as offline at once |
| The page looks unchanged after an update | A tab left open across an update keeps running the previous version's page. Reload it |
| Registering a second passkey fails in the browser, with nothing in Endbot's log | The authenticator already holds a passkey for this address, and WebAuthn refuses a duplicate. Remove the old passkey from the authenticator first |
| The browser does not offer the passkey | The passkey was made for another address, or removed. Run `/botweb link` for a new one |
| "Too many wrong codes on this server" | 10 wrong codes in 10 minutes from one client address close code entry for that address for 10 minutes. 60 wrong codes in 10 minutes from anywhere close it for the whole server for 10 minutes, and the console logs that |
| "Too many attempts" | The Worker limits each client address to 10 sign-in requests and 30 new connections per minute. Wait a minute; an open page connects again by itself |

## What Cloudflare stores

- The Worker code and the static page.
- `ENDBOTD_LINK_KEY`, which holds a **public** key and its id, and `ENDBOT_ORIGIN`, the address.
- In the Durable Object: the display name and whether BDS was last up.

Everything else stays on your PC:

- Accounts, passkeys, sessions, scripts and logs stay in `state/` and `scripts/`.
- On Windows, keep the instance directory restricted to the account that runs Endbot, because the link key there
  inherits its permissions.
- Script source and logs pass through Worker memory while they are relayed. The Worker does not log them.

Whoever controls your Cloudflare account can replace the Worker, so protect that account with its own strong sign-in
([what that risks](SECURITY.md#the-web)).
