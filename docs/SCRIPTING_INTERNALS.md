# Scripting internals

For developers. This backs the player-facing [Luau scripting reference](SCRIPTING.md) and uses the same headings, so
each section here explains how the behavior described there is implemented; the last section,
[Runner process and OS confinement](#runner-process-and-os-confinement), backs the operator's
[SCRIPTING_SETUP.md](SCRIPTING_SETUP.md). Material already stated elsewhere is not
repeated:

- Enabling, settings, limits, what OS confinement applies and the protocol frame bounds:
  [SCRIPTING_SETUP.md](SCRIPTING_SETUP.md). How the runner is started and confined is
  [below](#runner-process-and-os-confinement).
- The declaration model (`requires {}`, inspection, roles): [PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script).
- Watch buffering and delivery, trigger storage, approval and restarts: [EVENTS.md](EVENTS.md) (E5 to E9).
- Building the runner: [runner/README.md](../runner/README.md).
- Store locations before and after endbotd: [ENDBOTD.md](ENDBOTD.md).

## Commands

Native `/botscript` operation hints follow `/botgroup`'s approach and keep the pinned BDS baseline's completion
limitations.

Separate run/author Endstone permissions (`endbot.script.run`/`endbot.script.author`) are not implemented. Authoring
is gated by the controller role instead (`Principal` in `core/src/endbot_core/principal.py`,
[PERMISSIONS.md](PERMISSIONS.md#roles)).

### Ownership, visibility and revisions

- The owner is recorded by XUID, else by player UUID (the owner key).
- The console's `owner <name> <GamerTag>` changes the owner key and the owner's name; visibility, revision and
  history stay.

### The script menu

#### Edit lines

- The line editor exists because a one-line field is easy to change in place, unlike a whole prefilled source in the
  client's two-line field, which could not be cleared.
- Save compiles the complete source and inspects its declaration without accepting the body.
- `save` is an internal form action; typing it cannot update source.
- Stale edit, delete and run forms reject source revision changes.

### Script files

- A file script is never written to `scripts.json`.
- The `reload` declaration checks run on the command worker, like saves. That is why `/bot` commands wait during a
  reload, up to about 5 seconds for each file that hangs before `params`.
- The pre-endbotd location (`server/plugins/endbot/scripts/`) is in the ENDBOTD.md store table.

### Names and storage

- Scripts are stored atomically in versioned `state/scripts.json` (the plugin data folder before endbotd).
- A disk write error leaves the previous file and the in-memory version intact.

## Writing a script

### Parameters

- `params` yields the declaration before the host accepts the body; inspection executes the script only up to this
  declaration.
- The runner/host protocol carries `schema` as a JSON array, `[]` for no parameters. Each entry is
  `{"name":"spacing","type":"number","default":2}`; a choice adds `"choices":[...]`.
- The plugin validates this array again before showing forms or executing.

### Capabilities

- The `params` request carries the `requires {}` list as `requires` (`[]` when there is no call). Inspection returns
  it with the schema as a set.
- A run started from an inspection carries the declaration in its guard; the rest of the rule (S3) is in
  [PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script).

### Passing arguments

- Form selections pin the Group UUID and member order and the Bot UUID, rejecting deletion or recreation before
  submission.
- Declared Bot arguments keep their UUID pin on subsequent requests. Explicitly Group-pinned calls keep the Group's
  own identity guard.

### Structured status and mining waits

- Mining values come from runtime status, including its retained last mining job.
- Position, look, health and inventory come from BDS through the Endstone SDK at the time of the call. Hunger and
  saturation come from the attributes BDS sends the Bot.
- Status travels as JSON; null JSON fields become nil in Luau.

### World queries

World queries (`target`, `block`, `blocks`) read BDS on the server thread.

### Areas

Area values are built only by the `area` functions, so a table or a string is refused before anything reaches the
host.

### Targeting blocks

Block properties come from minecraft-data for the locked Bedrock version (`scripts/generate_block_properties.py`).

### Jobs and waits

- `waitMining` polling runs on the host, every `max(0.25, 1 / operations-per-second)` wall seconds.
- No command worker is held while a wait sleeps.

## Generated API reference

- The method table and the event table in SCRIPTING.md are generated from `core/src/endbot_core/operations.json`,
  the same registry read by the `/bot` parser, the typed argv adapter and the runner tests.
- Update both tables with `node runner/api-reference.mjs`; verify with `node runner/api-reference.mjs --check`
  (`runner/runner.test.mjs` checks them too). Keep the `<!-- BEGIN GENERATED ... -->` markers in SCRIPTING.md.
- The ordinary parser remains the semantic validator for ranges and option combinations.
- `group:members()` pins member UUIDs and membership order.

## Event kinds

The registry, delivery path and guarantees are in [EVENTS.md](EVENTS.md) and [EVENT_MEASUREMENTS.md](EVENT_MEASUREMENTS.md).

### Watches

Buffering, the pushed wait and the limits: [EVENTS.md E5](EVENTS.md#e5-watches).

### Triggers

Storage, approval, pausing, policies and restarts: [EVENTS.md](EVENTS.md) E6 to E9.

## Run lifetime and cleanup

**Authority.**

- All operations enter the existing ordered command service with the invoker's authority and the normal command
  audit.
- Every runtime request is authorized again. Group calls keep their UUID pins and membership guards.
- Revocation changes a separate grant revision, so revoke and regrant cannot revive an old run.

**Cleanup ownership.**

- Ownership tracks the Bot UUID and the movement, action, flag and mining channels, for scripts, commands and Group
  batches alike. A later controller input on a channel replaces the run's ownership of it.
- Cleanup pins UUIDs and mining loop ids. One-shot uncharged actions are not continuing jobs.
- Independent external runtime clients are outside the plugin's input ownership tracking.
- Unreachable runtime transport can prevent cleanup. Cleanup stops attempting further channels after an
  unavailable-transport error, rather than queueing one timeout per member.
- Shutdown cleanup uses the existing bounded runtime control timeout.
- If an input's acknowledgement is lost, the plugin conservatively keeps its pinned cleanup ownership, because
  acceptance is unknown. A later input from another caller still replaces that ownership.

## Runner process and OS confinement

What each platform applies, and the messages an operator sees, are in
[SCRIPTING_SETUP.md](SCRIPTING_SETUP.md#os-resource-confinement). This is how `core/src/endbot_core/script_confine.py`,
`script_process.py` and `runner/main.cpp` do it.

### Windows

- The host creates and configures the Job Object first, starts the trusted executable with `Popen`, then assigns the
  process handle at once, before starting pipe readers or writing the load frame.
- Native loader and startup code can run before assignment; **this startup race is not eliminated**. The runner blocks
  on its first NDJSON frame, so no supplied script runs in that interval.
- Job creation, configuration or assignment failure (including an incompatible enclosing job) kills and reaps the
  runner and fails closed, never retrying unconfined.
- The job handle is non-inheritable. Runs start with `BELOW_NORMAL_PRIORITY_CLASS`; a declaration check
  (`foreground`) starts at normal priority, because below normal gets no CPU while every core is busy (BOT-032).
- References: Microsoft's [job limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)
  and [assignment semantics](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject).

### Linux

The host passes the limits as runner command-line arguments; Python runs no callback between fork and exec. The
native runner applies them at the start of `main`, before reading stdin:

```text
--max-address-space BYTES --max-cpu-seconds N --parent-pid PID --nice 10|0
```

- Pass all four flags together, or none (the standalone harness, or `os-confinement = false`).
- Values are unsigned decimal integers. Memory `0` disables `RLIMIT_AS` and CPU `0` disables `RLIMIT_CPU`. Memory must
  be below 18446744073709551615 and fit the native rlimit type; CPU and PID at most 2147483647; PID positive.
- `--nice` is `10` for a run, which calls `nice(+10)`, or `0` for a declaration check (`foreground`), which keeps the
  inherited priority, for the same reason as on Windows (BOT-032). The runner refuses every other value.
- After `prctl(PR_SET_PDEATHSIG, SIGKILL)` the runner compares `getppid()` with `--parent-pid` before reading stdin.
  The signal also fires when the creating thread exits.
- Unknown, missing, duplicate, partial or invalid arguments, and any confinement failure, emit one NDJSON `error` with
  `phase = "confinement"` and a message beginning `confinement failed:`, then exit nonzero. The host reports this as a
  confinement failure and never retries unconfined.
- Windows validates these flags if supplied but ignores their effects; the host omits them there.
- References: [Linux resource limits](https://man7.org/linux/man-pages/man2/getrlimit.2.html) and
  [parent-death signal semantics](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html).

### Exit attribution

`Confinement.failure` maps an exit to a cap only when the evidence is explicit:

- Linux `-SIGXCPU`, or a Windows nonzero exit with job user time at the cap: CPU limit.
- Windows `STATUS_NO_MEMORY` (`0xC0000017`) or `STATUS_COMMITMENT_LIMIT` (`0xC000012D`), or `ENOMEM` from `Popen`:
  allocation failure.
- Everything else, including `SIGKILL`, `SIGSEGV` and native `bad_alloc` or abort, is a plain runner failure. A runner
  the host killed (watchdog or stop) is never attributed to a cap.

### Tests

- Fast CI builds and tests the runner on Ubuntu. Release-candidate jobs build and test both platform runners, and the
  bundle self-tests smoke-run a params-only script without BDS.
- Mocked and real-OS confinement probes are in `plugin/endbot/tests/test_script_confine.py`. A real probe runs only on
  its matching OS, and the native runner CPU probe skips when no matching build is available.
- Live checks against BDS and clients are in [validation status](COMPATIBILITY.md#scripting).
- Tests never establish world achievement history, actual Xbox achievement unlocks or another Endstone/BDS pair's
  support. Keep `online-mode=true`, `allow-cheats=false`, no experiments, Beta APIs or GameTest, and the normal
  Microsoft/Xbox authentication.
