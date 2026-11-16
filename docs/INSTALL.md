# Installing Endbot

How to get an Endbot server running from a release bundle: a new server, or an existing BDS server you adopt. Running
it afterwards (start and stop, granting access, configuration, updates) is in [OPERATIONS.md](OPERATIONS.md).

You install no Python, Node.js, venv, pip or npm. The platform bundle carries everything Endbot needs: a private
CPython with the patched Endstone, the Endbot plugin and CLI, a private Node.js, the prebuilt runtime and the Luau
script runner.

No release file contains the official Bedrock Dedicated Server (BDS). Endstone's normal acquisition path downloads the
pinned BDS (`1.26.60.6`, protocol `2225`) during `endbot setup`.

**Contents**

- [Before you start](#before-you-start)
- [Download and verify](#download-and-verify)
- [Extract](#extract)
- [New server](#new-server)
- [Adopting an existing server](#adopting-an-existing-server)
- [How setup behaves](#how-setup-behaves)
- [After setup](#after-setup)
- [Optional UI pack](#optional-ui-pack)

## Before you start

Endbot only runs a server that keeps `online-mode=true`, `allow-cheats=false`, the normal Microsoft/Xbox sign-in,
and a world with no experiments (Beta APIs and GameTest included) and no creative or cheat history. Setup refuses a
server or world that breaks these instead of changing it.

You need:

- a Windows x64 or Linux x86_64 machine;
- the Xbox GamerTag of each player who will control Bots;
- to adopt an existing server: a backup of its `worlds/`, and its `server.properties` set to `online-mode=true`,
  `allow-cheats=false` and `transport=nethernet` (setup never edits an existing server's settings).

## Download and verify

1. From the release, download the bundle for your platform and `SHA256SUMS`:

   | Platform | Bundle |
   | --- | --- |
   | Windows x64 | `endbot-<version>-windows-x86_64.zip` |
   | Linux x86_64 | `endbot-<version>-linux-x86_64.tar.gz` |

2. Check the archive against `SHA256SUMS`:

   ```bash
   sha256sum -c SHA256SUMS --ignore-missing                              # Linux
   certutil -hashfile endbot-<version>-windows-x86_64.zip SHA256         # Windows: compare with SHA256SUMS by hand
   ```

3. Optional: check that the file was built by this repository's release workflow, and from which commit, rather than
   uploaded by hand. This needs the GitHub CLI:

   ```bash
   gh attestation verify endbot-<version>-<platform>.zip --repo amatouhake/endbot
   ```

## Extract

The archive holds one directory, `endbot-<version>/`, with:

- `app/<version>/` and `app/current`: the application;
- `endbot` (Linux) or `endbot.cmd` (Windows): the launcher;
- `licenses/`: licenses and third-party notices.

That directory becomes your instance directory, where Endbot keeps everything it owns. Extract it where the server
should live (for example `/srv/endbot` or `%USERPROFILE%\endbot`) and rename it if you like. Choose the final place
before setup: a set-up instance cannot simply be moved ([directory layout](OPERATIONS.md#directory-layout)).

Run every `endbot` command below from the instance directory, or pass `--instance <dir>`. The examples use the Linux
launcher `./endbot`; on Windows run `.\endbot.cmd`.

## New server

1. Print the plan. Nothing is changed yet:

   ```bash
   ./endbot setup --fresh --controller <GamerTag>
   ```

   Repeat `--controller` for each controller.

2. Apply it. This creates `<instance>/server` and downloads the locked BDS:

   ```bash
   ./endbot setup --fresh --controller <GamerTag> --apply
   ```

3. Start the server in the foreground:

   ```bash
   ./endbot start
   ```

   BDS creates the world on this first start, and the runtime creates the owner keys and the control token in
   `state/secrets/`.

4. Join the server **once** with each controller GamerTag, so it binds to that account's XUID. Until then the GamerTag
   is only pending ([granting access](OPERATIONS.md#controllers-in-endbottoml)).

5. In game, give a Bot a name and bring it online: `/bot Alice spawn`.

What a fresh setup does:

- creates `<instance>/server` and lets Endstone download the locked BDS;
- sets `online-mode=true`, `allow-cheats=false` and `transport=nethernet` in `server.properties`, keeping its comments
  and other keys;
- leaves the world to BDS, which creates it on the first start with no experiments;
- adds every `--controller` GamerTag to `server/allowlist.json` (see below);
- creates `state/` and writes `endbot.toml` with your GamerTags.

A fresh BDS turns on `allow-list=true` with an empty list, which would reject the controllers. Setup therefore adds each
GamerTag as `{"ignoresPlayerLimit": false, "name": "<GamerTag>"}`, the same entry the console's `allowlist add` writes;
BDS fills in the XUID on the first join.

- Existing entries and their order are kept, and a name already listed (case-insensitively) is not added again.
- The file is written atomically.
- An `allowlist.json` that is not a JSON list of entry objects fails the plan and is never overwritten.

## Adopting an existing server

Use this for a vanilla BDS directory, or one set up with an earlier Endstone.

1. Back up `worlds/`.
2. Make sure `server.properties` has `online-mode=true`, `allow-cheats=false` and `transport=nethernet`. Setup checks
   them but never changes them.
3. Print the plan:

   ```bash
   ./endbot setup --existing <path> --controller <GamerTag>
   ```

4. Apply it, confirming that you have a world backup, or letting setup copy `worlds/` into its backup:

   ```bash
   ./endbot setup --existing <path> --controller <GamerTag> --i-have-a-world-backup --apply
   ./endbot setup --existing <path> --controller <GamerTag> --backup-worlds --apply
   ```

5. If the server has `allow-list=true`, add each controller to the allow-list. Setup prints the fix for each one:
   `./endbot console allowlist add <GamerTag>` after `./endbot start`, or edit `allowlist.json` while the server is
   stopped.
6. Start with `./endbot start` and join once with each controller GamerTag.

What setup checks on an existing server:

| Finding | Result |
| --- | --- |
| `online-mode=false`, `allow-cheats=true`, or `transport` missing or not `nethernet` in `server.properties` | The plan FAILs. Set the values yourself and run setup again. |
| `transport=raknet` | The plan FAILs: BDS 1.26.60 refuses players on RakNet. Set `transport=nethernet` first. |
| A world with creative, cheat or experiment history | The plan FAILs. Endbot does not change world flags. |
| Unknown experiment keys, `cheatsEnabled`, or a non-survival game type in the world | Warning |
| `version.txt` newer than the locked BDS | Refused. Install a newer Endbot, or restore the locked server. |
| `version.txt` older than the locked BDS, or missing | Endstone updates the server (below) |
| `version.txt` matches and the server executable is present | No BDS change |

When Endstone updates the server, it overwrites the server executable and the vanilla `behavior_packs/`,
`resource_packs/` and `definitions/`. Worlds, `server.properties` values, `allowlist.json` and `permissions.json` are
kept.

What setup changes on an existing server:

- backs up the operator-owned BDS files first ([backups](OPERATIONS.md#backups)); `worlds/` only with
  `--backup-worlds`;
- updates BDS if needed;
- creates `state/` and writes `endbot.toml`, with `[server] path` pointing at the existing directory;
- moves Groups and scripts from an earlier Endbot plugin into `state/`
  ([store layout moves](OPERATIONS.md#store-layout-moves)).

It never edits `server.properties` or `allowlist.json`. The `[local-bot-auth]` table in `endstone.toml` and
`plugins/endbot/config.toml` are generated on the first `endbot start`.

## How setup behaves

- **Plan first.** Without `--apply`, setup prints every planned change and applies nothing: files to create or modify
  with a reason, backups to take, and whether BDS will be downloaded or overwritten. `--apply` is the confirmation;
  the CLI never prompts.
- **Refusals.** Setup refuses when `endbot.toml` already exists (edit it, or use `endbot update`) and while a
  supervisor is running.
- **Report.** After `--apply`, setup runs `endbot doctor` and prints its report and the next steps. The owner key and
  control token checks are expected to warn until the first `endbot start`.

## After setup

| To | See |
| --- | --- |
| Start, stop and read logs | [Start and stop](OPERATIONS.md#start-and-stop) |
| Grant or revoke controllers | [Granting access](OPERATIONS.md#granting-access) |
| Change settings, such as Bot limits or the reply language | [Configuration](OPERATIONS.md#configuration) |
| Turn on Luau scripts | `[scripting] enabled = true` in `endbot.toml`, then restart ([SCRIPTING_SETUP.md](SCRIPTING_SETUP.md)) |
| Set up the web | [WEB_DEPLOY.md](WEB_DEPLOY.md) |
| Install a newer release | [Updating and rolling back](OPERATIONS.md#updating-and-rolling-back) |
| Check the instance | [Checking health](OPERATIONS.md#checking-health) |

## Optional UI pack

The release also carries `endbot-ui-<pack version>.mcpack`, an optional client-side resource pack. It enlarges server
forms and turns Endbot's marked inputs into a multi-line field ([UI_PACK.md](UI_PACK.md)). A server does not need it.

- A player who wants it imports the file on their own device and activates it under **Settings → Global Resources**.
  The server's pack stack stays empty.
- The pack has its own version, so its number differs from the Endbot version and moves only when the pack changes.
- It is not claimed to be achievement-safe: its mobile field, an achievement unlock with it active, and serving it as
  a world resource pack are unverified ([COMPATIBILITY.md](COMPATIBILITY.md)).
