# endbot — the Endbot operator CLI

`endbot` is the single operator-facing command for one Endbot instance
(docs/OPERATIONS.md). Status: 0.1.0 in progress — only `endbot doctor` is
implemented; `setup`, `start`, `stop`, `update`, and `controllers` print
"not implemented yet" and exit 2.

## Usage

```text
endbot [--instance PATH] doctor [--live]
endbot [--instance PATH] setup|start|stop|update|controllers   # placeholders
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

## Development

```sh
PYTHONPATH=cli/endbot/src python -m unittest discover -s cli/endbot/tests -v
ruff check cli/endbot
python -m build --wheel --outdir dist/cli cli/endbot
```

Runtime dependencies are `cryptography` (key pair checks) and `tomli` on
Python 3.10 only. The package intentionally does not depend on `endstone` so
doctor keeps working when Endstone is broken; `src/endbot_cli/endstone.lock.json`
is a copy of the repository `endstone.lock` and `tests/test_lock.py` fails when
the two drift.
