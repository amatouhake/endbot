# endbot — the Endbot operator CLI

`endbot` is the single operator-facing command for one Endbot instance
(docs/OPERATIONS.md). Status: 0.1.0 in progress — `doctor`, `start`, `stop`,
`console`, and `controllers reset` are implemented; `setup` and `update` print
"not implemented yet" and exit 2.

## Usage

```text
endbot [--instance PATH] doctor [--live]
endbot [--instance PATH] start
endbot [--instance PATH] stop [--timeout SECONDS]
endbot [--instance PATH] console <line...>
endbot [--instance PATH] controllers reset <gamertag>
endbot [--instance PATH] setup|update                      # placeholders
```

The instance directory is resolved in this order:

1. `--instance PATH`;
2. the `ENDBOT_INSTANCE` environment variable;
3. the directory containing the `endbot` / `endbot.cmd` launcher;
4. the current working directory.

It must contain `endbot.toml` (section 2) and the section 1 layout (`app/current`,
`state/secrets`, `state/profiles`, `state/controllers.json`, `state/generated`,
`state/run`, `backups`).

## endbot doctor

Read-only health report (section 7): one `PASS` / `WARN` / `FAIL` / `SKIP` line per
check, covering `endbot.toml`, the `server.properties` safety invariants
(`online-mode=true`, `allow-cheats=false`), world creative/experiment history
from `level.dat`, the locked BDS and Endstone versions, `[local-bot-auth]` key
pair consistency, the control token, controller enrollment, and (with
`--live`) runtime reachability over the control port.

Exit codes: `0` when no check FAILs (WARN is allowed), `1` when any check
FAILs, `2` for placeholder commands.

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
`endstone.toml` in place), and `tomli` on Python 3.10 only. The package
intentionally does not depend on `endstone` so doctor keeps working when
Endstone is broken; `src/endbot_cli/endstone.lock.json` is a copy of the
repository `endstone.lock` and `tests/test_lock.py` fails when the two drift.

### Developer-only environment overrides

Outside a packaged instance (no `app/` directory) `endbot start` resolves the
toolchain from these variables; each names an absolute path and takes
precedence over `app/current` when set (never set them in production):

- `ENDBOT_PYTHON` — the Python interpreter used to run `-m endstone`
  (packaged default: `app/<v>/python/python.exe` or `app/<v>/python/bin/python3`);
- `ENDBOT_NODE` — the Node.js interpreter running the runtime
  (packaged default: `app/<v>/node/node.exe` or `app/<v>/node/bin/node`);
- `ENDBOT_RUNTIME_DIR` — the runtime directory containing `src/cli.js`
  (packaged default: `app/<v>/runtime`).

`start` fails with a message naming both options when a component cannot be
resolved. The test suite additionally drives the supervisor through
internal-only keyword parameters (short timeouts, fake child commands); those
are not part of the operator interface.
