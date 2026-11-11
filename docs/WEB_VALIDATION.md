# Web validation record (BOT-021 step 10)

This records the first real run of the web, which is [step 10](WEB.md#steps) of the design: Cloudflare, real
passkeys, a phone and a PC. It was a manual check by the operator, guided by Claude. It does not replace the release's
own gates.

## Setup

- **When:** 2026-11-07 to 2026-11-08 JST.
- **Candidates:** release candidates `0.7.0-rc.1`, `rc.2` and `rc.3`, built by `release-candidate.yml` from scratch
  branches that were never merged. Each was installed with `endbot update <zip>` on one Windows 11 test instance.
- **Server:** BDS `1.26.60.6` with Endstone `0.11.15+endbot.1`, as locked.
- **Cloudflare:** the operator's own account, which also holds unrelated Workers. Two Hello World Workers were made
  first: one for the Endbot web and one for the refusal test. The web used the account's `workers.dev` subdomain.
- **Token:** an account API token limited to those two Workers (Editor), plus Workers Metadata Read-Only on all of the
  account's Workers, with a 7-day expiry.
- **Devices:** a PC browser, and a phone with passkeys in its own authenticator.

## Checked

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
| `endbot web rotate-key` | On rc.2 the new key took about 2 minutes to reach the Durable Object, and the faster retry was answered 429. #231 changed the retry to every 10 s, and a 429 counts as a refusal. On rc.3 there was no 429, and the link came up by itself after 3 min 23 s, past the 3-minute window then in place. #232 widens that window to 10 minutes and says so in the command. #232 has not run on Cloudflare yet |
| Screens | The tabs, script editing and output area, Bot and Group screens and the history were reviewed on rc.2. The fixes from that review are in #230 and #231 |

## Not run

These step 10 items were not checked, and stay open for a later pass:

- a protocol mismatch refused;
- a domain change forcing re-registration, and a custom domain at all, including the zone permissions the token
  needs;
- `endbot update --rollback` redeploying the previous Worker;
- the sign-in page after the PC loses power (only network loss was checked);
- every trusted-site item (P12): both modes, key rotation with two keys, a replayed or foreign-browser assertion, an
  XUID without a grant, `endbot web revoke` while the site still vouches;
- `rotate-key` with #232 in place.

## Open issues found

- **New Group missing from the Bots tab.** A Group created on the web did not appear in the Bots tab until a reload. An
  E2E test around Groups was also flaky once on #231 ("This Group no longer exists" not visible within 5 s). The cause
  is not known yet.
- **Open tabs keep the old UI.** A tab left open across an update keeps running the previous version's page until it
  is reloaded. The Worker serves the new assets only to a fresh load.
- **The next step `endbot update` prints.** It prints `endbot doctor, then endbot start`. In PowerShell the command is
  `.\endbot.cmd start`, so a bare `endbot start` is not found. Fixed since: on Windows it now prints `.\endbot.cmd doctor, then
  .\endbot.cmd start`, and `./endbot` elsewhere.
