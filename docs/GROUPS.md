# Shared Bot groups

A Group is a named, ordered list of Bots that `/bot` can address as one target. `#miners` means every member;
`#miners[2]` means the second member only.

Groups have an owner and are shared or private ([PERMISSIONS.md](PERMISSIONS.md#ownership)). Every command here works
from an authorized controller or from the server console (at the console, leave out the leading slash).

## Try it

```text
/bot Alice spawn
/bot Bob spawn
/botgroup create #miners
/botgroup add #miners Alice
/botgroup add #miners Bob
/botgroup list #miners
/bot #miners stop
/bot #miners[1] look 0 90
/bot #miners destroy count 64
/bot #miners destroy stop
/botgroup result #miners
```

Wait until the Bots are online, and give them their tools and targets as usual before gameplay actions. A reply
confirms that the operation was accepted, not that it succeeded in the world.

## Managing Groups

`/botgroup` creates and edits Groups. Group names always start with `#`, including when you create one.

```text
/botgroup create #group
/botgroup add #group <Bot>
/botgroup remove #group <Bot>|#group[index]
/botgroup delete #group
/botgroup list [#group]
/botgroup result #group [history]
/botgroup menu [#group [page]]
/botgroup help
```

| Action | What it does |
| --- | --- |
| `create` | Makes an empty Group. |
| `add` | Appends a Bot at the end of the list. |
| `remove` | Removes a member by Bot name, or by index (`#miners[1]`) to remove the entry of a profile that no longer exists. Later members move up one index. |
| `delete` | Deletes the Group. Its Bots stay registered. |
| `list` | Without a Group: the shared Groups. With a Group: its members. |
| `result` | Every member's result of a saved operation ([below](#operation-results)). |
| `menu` | Opens the Group screens ([below](#menus)). `/botgroup` alone does the same. |
| `help` | Shows the management syntax. |

Setting a Group's visibility and owner (`/botgroup visibility`, `/botgroup owner`) is described in
[COMMANDS.md](COMMANDS.md#shared-group-menus-and-results).

Example:

```text
/botgroup remove #miners Alice
/botgroup remove #miners #miners[1]
```

Notes:

- A Group name without `#` is refused, and so is the subcommand `members`: use `list #group`.
- From the OS command line, quote the whole command: `endbot console "bot #miners stop"`.

## Operating a Group

Use the Group as the target of an ordinary `/bot` command.

```text
/bot #group <operation> [arguments]
/bot #group[index] <operation> [arguments]
```

| Target | Who it reaches |
| --- | --- |
| `#miners` | Every member, in list order |
| `#miners[2]` | Only the second member; the reply is the normal single-Bot reply, with the index and Bot name |

Example:

```text
/bot #miners move forward for 40
/bot #miners[1] tp me
```

Notes:

- Counts and durations apply to each Bot separately. `destroy count 64` lets every member mine 64 blocks; it does
  not split 64 across the Group.
- A member that fails does not stop the others.
- Whole-group `rename` and `forget` are refused. Use one member (`#miners[1]`) or the Bot's name.
- Members owned by someone else who made them private are refused one by one; the rest of the Group still runs
  ([PERMISSIONS.md](PERMISSIONS.md)).
- From a menu, the operations are the same as for one Bot, with the same rules for `~`: teleport, spawn and
  interaction coordinates are relative to you; look-at and mining coordinates are relative to each Bot.

### Which operations reach members together

| Operations | How they run |
| --- | --- |
| `move`, `look` (including `look at`), `jump`, `attack`, `use`, `sprint`, `sneak`, `stop`, `hotbar`, `drop` | Sent to all members at once. Each Bot still has its own network and input timing, so members are not guaranteed to act on the same game tick. |
| Lifecycle (`spawn`, `despawn`, `reconnect`, ...), teleport, mining and the rest | One member after another, in list order. |

### Offline and missing members

- What happens to an offline member depends on the operation: `stop` is accepted, an online-only input such as
  `jump` fails for that member alone.
- A member whose profile was removed keeps its place in the list and fails on its own. `spawn` does not create a
  new Bot for it; remove the entry with `/botgroup remove #miners #miners[index]`.
- The member list is fixed when an operation starts.

### Losing permission while it runs

- If your grant is revoked, or the plugin shuts down, before a together-operation is sent, none of it is sent.
- During a one-after-another operation, the remaining members are not run.
- A together-operation that was already accepted is not undone. Continuous mining stops as it does for a single
  Bot.

## Replies

A whole-group command that changes something replies in at most **two lines**, however many members failed.

| Part | Meaning |
| --- | --- |
| accepted / failed / not run | How many members were in each state |
| acceptance unknown | The request was sent but no answer came back. It may or may not have run, and it is not retried. |
| Failure reasons | Grouped by category, at most three, the rest counted as "other" |

The reply means "accepted", not "done in the world". For each member's own result, use
[`/botgroup result`](#operation-results).

Notes:

- These changes are also written to the server log as a short English summary with your name. Menus, queries and
  `result` are not logged.
- Join and quit messages and mining notices are separate messages and are not part of the two lines.

## Queries

Queries print every member's answer at once.

```text
/bot #group status
/bot #group hotbar
/bot #group radius
```

Each line starts with `#group[index]` and the Bot name. A member that fails shows its error on its own line. An
indexed target (`#miners[2] status`) uses the same format.

Notes:

- Queries are not cut to ten rows or two lines.
- Queries are not logged and do not change [`result`](#operation-results) history. After `jump`, a `status` check
  still leaves `jump` as result 1.
- Giving a value (`hotbar 3`, `radius 8`, `radius default`) is a change, not a query: it gets the two-line reply,
  is logged and is saved as a result.

## Operation results

`/botgroup result` prints every member's result of one saved operation.

```text
/botgroup result #group [history]
```

| `history` | Operation shown |
| --- | --- |
| omitted or `1` | The latest |
| `2` | The one before |
| `3` | The one before that, and so on |

The output shows the target, the operation, the counts and each member's outcome and reason. It replays nothing and
does not ask the Bots again: it shows what happened when the operation was sent.

Example:

```text
/botgroup result #miners 2
```

Notes:

- Only whole-group commands that changed something are saved. Queries, management confirmations and menu
  navigation are not.
- Each controller keeps up to 20 results per Group, with at most 128 on the server; the oldest go first. Results
  live in memory and are cleared when the server restarts.
- Your results are your own: another controller's operation cannot replace them. The console and the OS command
  line share one slot.
- Results keep the language they were made in. Changing members or renaming a Bot does not change saved rows. A
  deleted and re-created Group does not show the old Group's results.
- In the menu, the older/newer buttons stay on the results you were viewing even when a new operation arrives. If a
  result was dropped in the meantime, reopen the history.

## Menus

`/bot` (or `/bot menu`) lists your Bots and your Groups as `#group (count)`. Tap a Group to open its operation
screen.

| Command | Screen |
| --- | --- |
| `/bot #miners menu` | Operations for the whole Group |
| `/bot #miners[1] menu` | The ordinary menu of that one member |
| `/botgroup` or `/botgroup menu` | The Group list, and creating a Group |
| `/botgroup menu #miners [page]` | Managing one Group |
| `/botgroup menu #miners result [history]` | Saved results, with older/newer buttons |

### Operation screen

The Group operation screen has the same categories as a single Bot: stop, movement, look, teleport, gameplay,
inventory and Bot settings, plus your operation history. Teleport, resume and spawn (also at coordinates),
reconnect and despawn, chunk radius, status and block interaction are all there.

- It works for Groups with online and offline members, and for empty Groups. Failures show in the reply as usual.
- Rename and forget are not offered; use a member's own menu.
- Settings forms start from defaults, not from one member's current value.
- **Group management...** opens the management screen for this Group.

### Management screen

`/botgroup menu #miners` shows the members in order with their connection state. From here you can add and remove
members and delete the Group (with a confirmation).

- Members are shown ten per page: `/botgroup menu #miners 2` is page 2.
- Tap a member to open its ordinary Bot menu. The way back to management is kept as you go deeper.
- **Group operations...** opens the operation screen. Its **Back to group management** button returns here.
- Opened directly with `/bot #group menu`, the operation screen goes back to the Bot and Group list instead.
- Add and remove forms name the Group and submit with **Add** and **Remove**. The delete form names the Group in its
  title, text and button. The Bots stay registered.

### How forms behave

- Every form submission runs the same `/bot` or `/botgroup` command, with the same permissions, and shows that
  command in chat.
- If the members change, or the Group is deleted and re-created, an old form is refused: open it again.
- A member's form keeps controlling that same Bot. A new Bot that took the old name is not controlled by it.
- Closing or canceling a form does nothing.

## Rules and limits

- **Names:** case-insensitive, up to 16 ASCII letters, digits or `_`, starting with a letter or `_`. The spelling you
  used is kept for display.
- **Order:** removing a member moves later members up one index; adding again appends at the end. Offline or
  forgotten members keep their place. There is no reordering command.
- **Renaming:** a Group cannot be renamed. A Bot that is renamed stays a member; a new Bot with the old name is not
  a member.
- **Teleport:** each member teleports on its own, exactly as a single Bot would. There is no formation, no
  collision-safe spreading and no shared fixed destination when you are moving. Use indexed commands where placement
  matters.
- **Language:** forms, results and normal replies use your game language. Some management messages stay in English.

## Known limits

- `/botgroup` shows a generic `<action: string>` / `<arguments: message>` usage instead of useful suggestions. The
  commands still work; completion is just weak, as for `/bot`
  ([#283](https://github.com/amatouhake/endbot/issues/283)).
- Members of a together-operation are not guaranteed to act on the same game tick (see
  [above](#which-operations-reach-members-together)).

What was checked on real servers and clients: [validation status](COMPATIBILITY.md#bot-operations). How Groups are
built: [BOT_CONTROL.md](BOT_CONTROL.md#groups).
