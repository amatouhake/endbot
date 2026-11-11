# Web validation record (BOT-021 step 10)

This records the real runs of the web, which is [step 10](WEB.md#steps) of the design: Cloudflare, real passkeys, a
phone and a PC. Each was a manual check by the operator, guided by Claude. They do not replace the release's own
gates.

## First run (0.7.0-rc.1 to rc.3)

### Setup

- **When:** 2026-11-07 to 2026-11-08 JST.
- **Candidates:** release candidates `0.7.0-rc.1`, `rc.2` and `rc.3`, built by `release-candidate.yml` from scratch
  branches that were never merged. Each was installed with `endbot update <zip>` on one Windows 11 test instance.
- **Server:** BDS `1.26.60.6` with Endstone `0.11.15+endbot.1`, as locked.
- **Cloudflare:** the operator's own account, which also holds unrelated Workers. Two Hello World Workers were made
  first: one for the Endbot web and one for the refusal test. The web used the account's `workers.dev` subdomain.
- **Token:** an account API token limited to those two Workers (Editor), plus Workers Metadata Read-Only on all of the
  account's Workers, with a 7-day expiry.
- **Devices:** a PC browser, and a phone with passkeys in its own authenticator.

### Checked

| Check | Result |
|---|---|
| Token scope | Editor on the chosen Workers alone fails at `GET /workers/subdomain` (403). Adding Metadata Read-Only on all Workers makes deploy, redeploy and `rotate-key` work. The Worker list then shows only the Workers the token may see. The guide's hint for this 403 came from this check (#230) |
| A Worker Endbot did not create | Refused, and the existing Worker was left as it was (#228). `--replace-worker` took over the Hello World Worker on the first deploy |
| Redeploy | `endbot web deploy` worked from a second terminal while Endbot ran, and the dashboard stayed usable. `endbot update` redeployed the Worker on rc.1 → rc.2 and rc.2 → rc.3 |
| Link on a phone and a PC | Done from `/botweb link` codes. A second passkey from a second code worked, and the game showed the passkey notice. That notice was in English; since #230 it uses the player's language |
| Cross-device QR sign-in | Worked |
| Rate limits | The phone showed the rate-limit warning when the limit was reached |
| Revocation | Revoking the grant sent both open sessions (phone and PC) back to the sign-in page, and a passkey sign-in was then refused with a clear "no Endbot permission" message. Revoking during a script run ended the run ("script operation permission was revoked"); the script stayed and did not restart. After the grant came back, sign-in worked without a new `/botweb link`, with the same passkeys. History updated live, with in-game commands and web operations side by side |
| BDS stopped | Status, history and script editing kept working. Group and Bot operations used to be offered and then failed with a Windows error; since #230 they are hidden with a note. The sign-in page used to need a reload to see the server start or stop; since #230 it asks again every 30 s |
| A Worker with a wrong link key | Refused (close code 4003). The link came back after `rotate-key` put a matching key in place |
| Network loss of the server PC | On rc.1 the relay kept routing to the dead link, and the web did not recover by itself. Fixed in #229 by retiring silent links (close code 4005), a Durable Object alarm and an endbotd watchdog. On rc.3 the phone showed the loss after 75 s. The link was back 4 s after the network returned, and the dashboard about 32 s later, which is the browser's retry backoff. The header dot turned grey while reconnecting. A narrow screen hides the header text by design |
| `endbot web rotate-key` | On rc.2 the new key took about 2 minutes to reach the Durable Object, and the faster retry was answered 429. #231 changed the retry to every 10 s, and a 429 counts as a refusal. On rc.3 there was no 429, and the link came up by itself after 3 min 23 s, past the 3-minute window then in place. #232 widens that window to 10 minutes and says so in the command |
| Screens | The tabs, script editing and output area, Bot and Group screens and the history were reviewed on rc.2. The fixes from that review are in #230 and #231 |

## Release pass (0.7.0-rc.5 and rc.6)

### Setup

- **When:** 2026-11-11 to 2026-11-12 JST, together with the [events checks](EVENTS_VALIDATION.md#release-pass).
- **Candidates:** `0.7.0-rc.5` (the Conan pin of #257) and `0.7.0-rc.6` (`main` at #264), built the same way. The
  first run's instance was updated from rc.3, so the update, the protocol mismatch and the rollback ran on one
  instance with real passkeys and triggers.
- **Server, Cloudflare and devices:** as in the first run, with a Windows PC and an Android phone. For the power cut
  the phone used its mobile network.
- **Token:** the same token. For the custom domain it was widened for the test and narrowed again afterwards (see
  below).
- **Trusted site:** a test-only signer Worker in the same account, protected by a password, holding two ES384 keys.
  It existed only for this pass and was deleted afterwards.

### Checked

| Check | Result |
|---|---|
| Protocol mismatch | rc.5 endbotd (web protocol 2) against the rc.3 Worker (protocol 1). endbotd logged `Worker speaks web protocol 1, endbotd expects 2: run "endbot web deploy"`, and `doctor --live` failed `web` with the same words. The phone showed the server as stopped, not the version mismatch. After `endbot web deploy` the link came up 32 s later |
| `endbot web rotate-key` with #232 | The command said it retries every 10 s for 10 minutes. The link came up by itself after 5 min 02 s, inside that window. Without #232 it would have failed |
| Power cut of the server PC | The tab that was signed in showed that the server cannot be reached 63 s after the cut. A tab opened during the first minute kept loading until the relay retired the dead link; from then on a fresh sign-in page showed the server offline at once, and its buttons answered at once that the server cannot be reached. After power came back, the link was up 11 s after `endbot start`, the world loaded, and `doctor --live` passed |
| `endbot update` and `--rollback` | rc.5 → rc.6 redeployed the Worker. `--rollback` to rc.5 redeployed the rc.5 Worker and moved the stores back to `stores 1` with a backup; rc.5 moved them into `state/` again on its next start. A `--force` update back to rc.6 redeployed rc.6. The triggers kept their state and counters through all three |
| Domain change refused without confirmation | `--domain` without `--confirm-domain-change` stopped before anything changed, saying the origin would change and every passkey would stop working |
| Custom domain | On a new subdomain of a zone in the same account. With Workers Routes Edit and DNS Edit on the zone, Editor on the chosen Workers and Metadata Read-Only on all Workers, `PUT /workers/domains` was refused (403). Endbot put `endbot.toml` back and had not replaced the Worker's script, so the `workers.dev` web kept working. With Editor on all of the account's Workers it worked. Whether the zone permissions are also needed was not tested separately. Every passkey and session was deleted, accounts stayed, the link was up 16 s later after one failed DNS lookup, and the `workers.dev` address answered 404. A new `/botweb link` worked on the PC and the phone |
| Back to `workers.dev` | With `--confirm-domain-change` and no `--domain`. Passkeys and sessions were deleted again, the link came up 32 s later, and a new `/botweb link` worked. The custom domain stayed attached to the Worker until it was removed in the dashboard, as the guide says |
| Trusted site: link mode | A sign-in at the site, then a passkey registered and used again after signing out. The device list showed it as coming through the site |
| Trusted site: sign-in mode | Signed in through the site without a passkey. The sign-in page kept the passkey and `/botweb link` entries |
| Trusted site: two keys | Both `k1` and `k2` signed in. With `k1` removed, a `k1` assertion was refused and `k2` still worked |
| Trusted site: replay and foreign browser | A replayed assertion and an assertion sent from another browser were refused as expired. The same assertion then worked from the browser it was issued to, so the refusal came from the cookie binding. Refusals are not logged, and no assertion appears in the log |
| Trusted site: XUID without a grant | Refused with "this account has no Endbot permission" on the page and the reason in endbotd's log |
| Trusted site: `endbot web revoke` | Ended the session and removed the passkey that came through the site. The phone, signed in with a `/botweb link` passkey, stayed signed in. Signing in through the site afterwards worked |
| Rate limits (#259) | Signing out and in three times within a minute showed the "too many attempts, wait one minute" message from the `/auth/*` limit. WebSocket upgrades now have their own bucket, so a signed-in page no longer falls back to the sign-in page |
| `endbot web deploy` with no console (#258) | With standard input from `NUL` it failed at once, naming the missing token, instead of waiting for a prompt |
| Trigger screens | See the [events record](EVENTS_VALIDATION.md#release-pass) |

## Not run

These need a second Xbox account, and stay open for a later pass:

- a trigger made private disappearing from another viewer's open list;
- the web refusing a change to another player's trigger.

## Open issues and caveats

- **New Group missing from the Bots tab.** Seen in the first run: a Group created on the web did not appear in the
  Bots tab until a reload. An E2E test around Groups was also flaky once on #231 ("This Group no longer exists" not
  visible within 5 s). The cause is not known yet.
- **Open tabs keep the old UI.** A tab left open across an update keeps running the previous version's page until it
  is reloaded. Seen again on rc.6.
- **A protocol mismatch looks like a stopped server.** The page says the server is stopped; the log and `doctor --live`
  name the mismatch and the command.
- **The check right after a deploy is too early.** After `endbot web deploy` changes the Worker, the immediate check
  can still reach the old Worker for about 30 s and say the link is not up, suggesting another deploy. The link comes
  up by itself.
- **A tab opened just after the server PC dies waits.** Until the relay retires the dead link, about a minute, a newly
  opened page keeps loading instead of showing the server offline.
- **A second passkey on the same authenticator.** Registering a passkey on an authenticator that still holds an older
  passkey for the same site fails in the browser (WebAuthn refuses the duplicate) and Endbot logs nothing. Remove the
  old passkey from the authenticator first.
- **The first trusted-site sign-in after adding a key.** Once, the page opened at the dashboard's address but showed
  the sign-in page until a reload. It did not happen again.
- **The next step `endbot update` prints.** It printed `endbot doctor, then endbot start`, which PowerShell does not
  find. Fixed since: on Windows it now prints `.\endbot.cmd doctor, then .\endbot.cmd start`, and `./endbot`
  elsewhere.
