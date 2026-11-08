# BOT-022 step 6b: runtime and server event kinds

Implemented `bot.online`, `bot.offline`, `bot.died`, `job.finished` and `server.started` on
`feat/bot-022-6b-runtime-kinds`.

- Registry: added the five kinds to `core/src/endbot_core/operations.json` (`bot.died` is `plugin`-sourced, the
  other four are `endbotd`-sourced), with the requested filters, payloads, `read.world` capability and `each`
  guarantees. Regenerated `docs/SCRIPTING.md` with `node runner/api-reference.mjs` and added
  `docs/EVENTS_VALIDATION.md` (`## Runtime and server kinds`, "Not yet run").
- endbotd: `core/src/endbotd/runtime_kinds.py` polls `endbot_core.control.bot_profiles` once a second only while
  a `bot.online`/`bot.offline` subscription exists. The first poll is the baseline; later transitions raise
  `bot.online`/`bot.offline` (`reason` is the new state or `removed`). `job.ended` Events from the command
  service become `job.finished` occurrences (`destroy`/`destroy continuous` map to the job kind `mining`).
  `OccurrenceHub.link_changed` tracks the plugin's boot id and raises `server.started` once per new boot.
- Link/plugin: `PluginLink` makes one `boot` id per enable and sends it in `hello`; the first link of a plugin
  process also marks `firstLink`, so endbotd raises `server.started` for a new BDS process but not for a reconnect
  or an endbotd restart under a running BDS. `BlockChangeSource` also carries `bot.died`: the MONITOR
  `PlayerDeathEvent` listener reports only players without an XUID, only when the interest set names `bot.died`,
  on the same per-tick `events` frame.
- Tests: added `plugin/endbot/tests/test_runtime_kinds.py`; extended `test_block_source.py`, `test_link.py`,
  `test_operations.py`, `test_plugin_adapter.py`, `test_command_declarations.py`, `test_occurrences.py` and
  `test_endbotd_service.py`.

## Commits

- `da995f0 feat: bot.online, bot.offline, bot.died, job.finished and server.started (BOT-022 step 6b)`
- `5ce7421 fix: server.started only for the first link of a plugin process (BOT-022 step 6b)`

`TASK-022-6b.md` and this file are not committed.

## Test commands

Run with `C:\Users\kenke\Documents\endbot\build\pr180-env\Scripts\python.exe`:

- `PYTHONPATH=plugin/endbot/src;core/src python -m unittest discover -s plugin/endbot/tests`
  - **1019 tests, OK (skipped=238)**.
  - This Windows sandbox denies the DACL Python creates for `mkdir(0o700)`/`mkdtemp`, so the run used the same
    command with a temporary shim prepended: `PYTHONPATH=.test-support;plugin/endbot/src;core/src ...`. The shim
    passes `0o777` to `os.mkdir` (removed afterwards). Without it, the pre-existing `test_web_accounts` `mkstemp`
    path retries candidate names until the process is stopped; the product code is unchanged.
- `ruff check scripts tests plugin/endbot cli/endbot` (ruff 0.17.1): **All checks passed**.
- `node runner/api-reference.mjs` and `node runner/api-reference.mjs --check`: passed.
- Targeted runs on the final code: `test_link.py` (27 tests), `test_runtime_kinds.py` (11),
  `test_endbotd_service.py` (16), plus `test_block_source.py` (10), `test_occurrences.py` (10),
  `test_operations.py` (39), `test_plugin_adapter.py` (13), `test_command_declarations.py` (5),
  `test_watches.py` (12), `test_trigger_engine.py` (10) — all OK.

`node --test runner/runner.test.mjs` could not run in this sandbox (`spawn EPERM` for its child processes); the
required generated-reference check above passed.
