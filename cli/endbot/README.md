# endbot — the Endbot operator CLI

`endbot` is the single operator-facing command for one Endbot instance
(docs/OPERATIONS.md). Status: 0.1.0 in progress — `setup`, `update`,
`doctor`, `start`, `stop`, `console`, and `controllers reset` are implemented.

## Usage

```text
endbot [--instance PATH] setup (--fresh | --existing PATH) [--controller GAMERTAG ...]
       [--apply] [--backup-worlds | --i-have-a-world-backup]
endbot [--instance PATH] update ARCHIVE [--sums PATH] [--force] [--backup-worlds]
endbot [--instance PATH] update --rollback
endbot [--instance PATH] doctor [--live]
endbot [--instance PATH] start
endbot [--instance PATH] stop [--timeout SECONDS]
endbot [--instance PATH] console <line...>
endbot [--instance PATH] controllers reset <gamertag>
```

The instance directory is resolved in this order:

1. `--instance PATH`;
2. the `ENDBOT_INSTANCE` environment variable;
3. the directory containing the `endbot` / `endbot.cmd` launcher;
4. the current working directory.

It must contain `endbot.toml` (section 2) and the section 1 layout (`app/current`,
`state/secrets`, `state/profiles`, `state/controllers.json`, `state/generated`,
`state/run`, `backups`). `endbot setup` creates that layout for a new
instance; everything else assumes setup has run.

## endbot setup (section 6)

Creates or adopts a BDS server directory for one instance. The command is
always dry-run first: it prints every planned change (files to create or
modify with a short reason, backups to take, and whether BDS will be
downloaded or overwritten) and touches nothing; `--apply` is the confirmation,
so the CLI stays scriptable and never prompts.

- `--fresh` creates `<instance>/server` and downloads the locked BDS **through
  Endstone's own acquisition path** (Endbot never bundles, vendors, or
  redistributes BDS and never starts it during setup).
- `--existing PATH` adopts a vanilla BDS (or earlier Endstone) directory. The
  plan reports the `version.txt` state (matching the lock: no BDS change;
  older or missing: Endstone overwrites the server executable and the vanilla
  `behavior_packs/`, `resource_packs/`, `definitions/` entries it ships while
  keeping worlds, `server.properties` values, `allowlist.json`, and
  `permissions.json`; newer than the lock: the plan FAILs), verifies the
  safety invariants (`online-mode=true`, `allow-cheats=false` — an existing
  server's settings are checked, never silently flipped) and the world
  history via the doctor `world` check (creative/cheat/experiment history
  FAILs the plan; Endbot never changes world flags), and lists the backup it
  will take.
- Backups land in `backups/<UTC timestamp>/` (`server.properties`,
  `allowlist.json`, `permissions.json`, `endstone.toml`,
  `packetlimitconfig.json`, the pack trees, and the server executable) with a
  `manifest.json` listing every backed-up file's SHA-256. `worlds/` is copied
  only with `--backup-worlds`; otherwise `--apply` requires
  `--i-have-a-world-backup`.
- `--controller GAMERTAG` (repeatable) writes the section 3 controller list
  into `endbot.toml`. Join once with each GamerTag to bind it.

Both paths end with a non-live `endbot doctor` report and the next steps. The
owner keys and control token do not exist yet at that point: the first
`endbot start` generates them, and the report says so as WARN lines. `setup`
refuses to run when `endbot.toml` already exists (edit it instead) or when a
supervisor is running.

## endbot update (section 6a)

Installs a new application version from a platform bundle archive (`.zip` on
Windows, `.tar.gz` on Linux) produced by the release pipeline. The archive
holds `app/<newversion>/` and the `endbot` / `endbot.cmd` launcher at its
root; `update` extracts exactly those into the instance and never touches
`state/`, `endbot.toml`, the server directory, or the current application.

- Integrity: `--sums PATH` (or `SHA256SUMS` beside the archive when present)
  is verified against the archive; a mismatch, or a sums file without an entry
  for the archive, refuses the update.
- The NEW version's doctor runs against the instance with the new
  interpreter. A `bds-version` FAIL is handled specially: the section 6
  backup runs (worlds only with `--backup-worlds`), then the new version's
  Endstone acquisition step moves the server to the new lock. Any other
  doctor FAIL keeps the old version current and reports.
- `app/current` (a text file) is switched atomically to the new version; the
  previous version directory is kept. The launcher is replaced from the
  archive. An already extracted `app/<newversion>/` is refused unless
  `--force`; the active version's directory is never replaced.
- `endbot update --rollback` switches `app/current` back to the most recent
  previous version directory present in `app/`.

`update` refuses to run while a supervisor is running.

## endbot doctor

Read-only health report (section 7): one `PASS` / `WARN` / `FAIL` / `SKIP` line per
check, covering `endbot.toml`, the `server.properties` safety invariants
(`online-mode=true`, `allow-cheats=false`), world creative/experiment history
from `level.dat`, the locked BDS and Endstone versions, `[local-bot-auth]` key
pair consistency, the control token, controller enrollment, and (with
`--live`) runtime reachability over the control port.

Exit codes: `0` when no check FAILs (WARN is allowed), `1` when any check
FAILs.

Doctor never writes anything. World achievement history and actual Xbox
achievement unlocks remain explicit manual validation gates (docs/ACHIEVEMENTS.md).

## endbot start / stop / console (section 5a)

`endbot start` regenerates `state/generated/endbot-runtime.json`,
`<server>/plugins/endbot/config.toml`, and the `[local-bot-auth]` table in
`<server>/endstone.toml` (section 2), runs the fail-fast doctor checks (any
FAIL, and any `<server>/version.txt` mismatch with `endstone.lock`, refuses the
start and points at `endbot update`), then supervises the Endbot runtime and
Endstone/BDS in the foreground. The very first start creates the control token
and owner key pair in `state/secrets/` (the runtime does this) before
`[local-bot-auth]` is re-checked and BDS starts.

While it runs:

- lines typed on the supervisor's terminal go to the BDS console, and Ctrl+C
  stops everything gracefully;
- `endbot console <line>` (another terminal) queues one BDS console line in
  `state/run/console.request`;
- `endbot stop` (another terminal) writes `state/run/stop.request` and waits
  (default 120 s, `--timeout`) for the supervisor to exit;
- `endbot controllers reset <gamertag>` (another terminal, supervisor stopped)
  returns one controller binding to pending for re-enrollment (section 3).

`stop`, `console`, and `controllers reset` refuse clearly when no live
supervisor exists and clean up a stale `state/run/supervisor.pid`. Exit codes:
`0` clean, `1` refused/failed/UNCLEAN stop. Per-start logs are kept for the
last 10 starts in `state/run/logs/`; exit codes land in
`state/run/last-exit.json`. The supervision and restart policy is documented in
docs/OPERATIONS.md section 5a.

## Development

```sh
PYTHONPATH=cli/endbot/src python -m unittest discover -s cli/endbot/tests -v
ruff check cli/endbot
python -m build --wheel --outdir dist/cli cli/endbot
```

Runtime dependencies are `cryptography` (key pair checks), `tomlkit` (editing
`endstone.toml` and writing `endbot.toml`), and `tomli` on Python 3.10 only.
The package intentionally does not depend on `endstone` so doctor keeps
working when Endstone is broken: BDS acquisition is isolated in
`endbot_cli/bds.py`, which drives the pinned Endstone bootstrap through
`<python> -m endbot_cli.bds` in the bundled/active interpreter.
`src/endbot_cli/endstone.lock.json` is a copy of the repository `endstone.lock`
and `tests/test_lock.py` fails when the two drift;
`tests/test_bds_contract.py` asserts the Endstone private API used there
(skipped when endstone is not installed) so an Endstone bump fails loudly.

### Developer-only environment overrides

Outside a packaged instance (no `app/` directory) `endbot start` resolves the
toolchain from these variables; each names an absolute path and takes
precedence over `app/current` when set (never set them in production):

- `ENDBOT_PYTHON` — the Python interpreter used to run Endstone (`-m endbot_cli.endstone_entry`)
  (packaged default: `app/<v>/python/python.exe` or `app/<v>/python/bin/python3`);
- `ENDBOT_NODE` — the Node.js interpreter running the runtime
  (packaged default: `app/<v>/node/node.exe` or `app/<v>/node/bin/node`);
- `ENDBOT_RUNTIME_DIR` — the runtime directory containing `src/cli.js`
  (packaged default: `app/<v>/runtime`).

`start` fails with a message naming both options when a component cannot be
resolved. The test suite additionally drives the supervisor through
internal-only keyword parameters (short timeouts, fake child commands); those
are not part of the operator interface.
