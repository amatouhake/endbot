# endbotd validation record (BOT-020 step 6)

This record covers step 6 of [ENDBOTD.md](ENDBOTD.md#steps). It separates autonomous live smoke from the human Windows
client check. Runs use the locked pair: official BDS `1.26.52.3` (Windows build `51798919`), protocol `2193`, patched
Endstone `0.11.13+endbot.1`. No server, world, identity, key, token, diagnostic capture or machine-local path is kept in
the repository.

Status: **in progress**. The release containing BOT-020 is not validated until every item below is recorded as passed
on the final main. The first human Windows client pass (2026-10-30) failed on the script menus; the fix is in #150 and
needs a rebuilt candidate and a second pass.

## Early check on the 3c main — 2026-10-29 JST

Before steps 3d, 4 and 5 landed, `scripts/raknet_spike.py --mining --groups --scripting` ran on Windows against main at
`6198594` (step 3c, endbotd as its own supervisor child). The run completed with `ok: true`. Script runs, Group commands
and Survival mining went through endbotd and the link. Local-bot authentication refused the wrong-audience and
auth-disabled variants. The controller policy survived a restart, and the shutdown was clean. World history stayed
clean (no cheats, no experiments, never loaded in Creative). This early check does not replace the final run.

## Final live smoke on the locked pair — 2026-10-29 JST

Run on Windows over RakNet against main at `30752f4` (all of BOT-020, including the batched mining raycast), with the
probe read fix from #149. Each check below passed in at least one complete run; the runs that did not complete stopped
on probe timing or world randomness, never on endbotd (details below).

- [x] `--endbotd-failures` (#146), three times on the final main:
  - **Kill endbotd during a script run:** while it was down the plugin refused `/bot` with "Endbot service is
    restarting"; endbotd restarted and relinked, commands worked again, and the run journal (3d) ended the run with
    "Script … ended: Endbot service restarted". A console run has no owner to notify in chat; the owner notice is
    covered by the 3d unit tests.
  - **Kill endbotd during `destroy continuous`:** no block broke after the kill, the runtime lease ended the loop
    13.3 s later (200 ticks, about 62.5 ms per tick on Windows), and mining did not resume after the restart.
  - **Link restart:** the plugin relinked, the Bot was still listed and a command worked.
- [x] `--scripting`, `--mining`, `--item-use` and `--groups` in the same run as the failure scenarios, so they ran
      against an endbotd that had been killed and restarted three times.
- [x] `--inventory` and `--entity` in complete runs of their own, including timed movement, radius commands, negative
      local-bot authentication (wrong audience, auth disabled) and the world-history check.
- [x] `endbot controllers add|remove` through endbotd in every run (the spike's controller-CLI correlation check). The
      "endbotd is not running" answer is covered by the step 5 tests on Windows and Linux.
- [x] `endbot update` from the v0.4.0 release bundle to the candidate bundle (release-candidate run 40852002396 on
      `8b075f8`), then `--rollback`, with `scripts/e2e_store_migration.py` (#148): the first candidate start migrated
      both stores into `state/` with a backup and doctor `stores` PASS; a Group member removal and a script edit made on
      the candidate came back with the rollback and showed on 0.4.0. P5 does not touch the stores.
- [x] World history stayed clean in every complete run (no cheats, no experiments, never loaded in Creative).

Release-candidate run 40852002396 also passed the clean-consumer bundle e2e with endbotd on Linux and Windows over
RakNet and NetherNet.

**Flaky checks seen, not caused by endbotd:** a timed move that drifted 0.09 blocks after expiry (limit 0.05), a jump
BDS did not observe, a product drop that took longer than 10 s, and a fed cow that wandered out of the crosshair before
`interact`. Each failed once and passed on the next run of the same case. They are runtime timing and world randomness
checks that predate BOT-020. The probe's own state-file read race is fixed in #149.

## Human Windows client check — 2026-10-30 JST

Run by the operator with an unmodified Windows client against the Windows bundle of candidate run 40862411934
(`30752f4`): `endbot-0.4.0-windows-x86_64.zip`, sha256 `3c7e134c…bb447c`, `gh attestation verify` exit 0. The instance
was a fresh setup (`endbot setup --fresh --controller <GamerTag> --transport raknet`), Survival with achievements on,
`[scripting] enabled = true` and `max-players = 3`; `endbot doctor --live` passed every check except the expected
`controllers: pending` before the first join.

- [ ] Commands, forms, script menus and messages look and behave as before endbotd. **Failed.** `/bot` and `/botgroup`
      menus opened and behaved as before, but every `/botscript` menu was dropped: `/botscript`, `/botscript menu` and
      `/bot menu` → *Scripts...* delivered nothing. endbotd logged
      `A form view could not be sent to the plugin: function cannot cross the endbotd link` seven times, once per
      attempt. Cause: script menus were the only screens endbotd built itself, and a script screen carries a form
      builder (`ModalScreen.build`, a closure) that `endbot_core.wire_views` refuses; bot and group outcomes cross as
      data and the plugin builds their screens, which is why only the script menus were affected. Fix: #150 crosses a
      script outcome as `forms.ScriptMenuView` and the plugin builds the screen from it (`menu_views.script_view`),
      following [ENDBOTD.md](ENDBOTD.md) P3. **Not yet checked on candidate bytes.**
- [x] A command issued while endbotd restarts is refused with a reason, and works again afterwards. endbotd was killed
      three times in a row (07:34:43, 07:34:48 and 07:34:52 JST). The plugin logged
      `Endbot cannot link to endbotd (unavailable: endbotd unavailable: ConnectionRefusedError)` and relinked at
      07:34:57. Commands typed during that gap were refused with `Endbot service is restarting`; the same command ran
      normally before and after it.
- [x] Achievement history is unaffected. The client's achievement list and history were the same before and after the
      session.

A pre-check of #150 ran on the same instance with the three changed files replaced in place and a restart: every
`/botscript` menu opened, the operator created `mine_at_spot` from the menu (`state/scripts.json` was written), and the
run ended `mine_at_spot ended: completed` with no `cannot cross` warnings. That is not the gate — it ran on patched
bundle files, not on the rebuilt candidate.

Next: after #150 merges, rebuild the candidate and repeat all three items on the new bytes.
