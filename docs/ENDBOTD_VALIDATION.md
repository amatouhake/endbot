# endbotd validation record (BOT-020 step 6)

This record covers step 6 of [ENDBOTD.md](ENDBOTD.md#steps). It separates autonomous live smoke from the human Windows
client check. Runs use the locked pair: official BDS `1.26.52.3` (Windows build `51798919`), protocol `2193`, patched
Endstone `0.11.13+endbot.1`. No server, world, identity, key, token, diagnostic capture or machine-local path is kept in
the repository.

Status: **in progress**. The release containing BOT-020 is not validated until every item below is recorded as passed
on the final main.

## Early check on the 3c main — 2026-10-29 JST

Before steps 3d, 4 and 5 landed, `scripts/raknet_spike.py --mining --groups --scripting` ran on Windows against main at
`6198594` (step 3c, endbotd as its own supervisor child). The run completed with `ok: true`. Script runs, Group commands
and Survival mining went through endbotd and the link. Local-bot authentication refused the wrong-audience and
auth-disabled variants. The controller policy survived a restart, and the shutdown was clean. World history stayed
clean (no cheats, no experiments, never loaded in Creative). This early check does not replace the final run.

## Final live smoke on the locked pair

To be run on the final main, after 3d, 4 and 5 are merged:

- [ ] Every existing `raknet_spike.py` case: `--scripting`, `--mining`, `--item-use`, plus `--groups`,
      `--inventory` and `--entity`.
- [ ] Kill endbotd during a script run: the plugin refuses commands with "Endbot service is restarting" while it is
      down, the supervisor restarts it, and the run journal ends the run with its log line and owner notice.
- [ ] Kill endbotd during `destroy continuous`: mining stops at the runtime lease and does not resume.
- [ ] Restart BDS's plugin link: the plugin reconnects with a new epoch and presence is rebuilt.
- [ ] `endbot update` from 0.4.0, then `--rollback`, with Groups and scripts changed in between: the store migration
      and its reverse keep every change.
- [ ] `endbot controllers add|remove|list` through endbotd, and its "endbotd is down" answer.
- [ ] World history stays clean after every run.

## Human Windows client check

To be done by the operator with an unmodified Windows client on the final candidate:

- [ ] Commands, forms, script menus and messages look and behave as before endbotd.
- [ ] A command issued while endbotd restarts is refused with a reason, and works again afterwards.
- [ ] Achievement history is unaffected.
