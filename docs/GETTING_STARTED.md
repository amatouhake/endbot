# Getting started with Endbot

Endbot adds Bots to the server: players that you control with `/bot` or from a menu. This page takes about five
minutes. It covers your first Bot and a few things worth trying. Every operation and option is in
[COMMANDS.md](COMMANDS.md).

## Before you start

- **You need a grant.** The server's operator decides who may control Bots. Without a grant, the game does not
  recognize `/bot` as a command. Ask the operator.
- **Join with your normal Xbox sign-in.** Nothing has to be installed. The
  [UI resource pack](UI_PACK.md) is optional: it makes the menus larger and gives scripts a multi-line editor.
- **Your game language is used.** A client set to Japanese sees Japanese replies and menus. Commands stay the same
  in every language.

## 1. Make your first Bot

Type `/bot` in chat. A menu opens with the Bots you can control. Tap **+ New Bot here**, enter a name (letters,
digits and `_`, up to 16 characters) and submit. The Bot connects and appears next to you after a few seconds.

The same thing as a command:

```text
/bot Alice spawn
```

## 2. Make it do something

Type `/bot Alice` (or tap the Bot in the `/bot` list) to open its menu. The buttons follow Minecraft's control
settings: **Movement...**, **Look...**, **Teleport...**, **Gameplay...**, **Inventory...**, and **Bot...** for its
connection.

Every button you tap is shown once in chat as the command it ran, in gray. The menu teaches the commands: when you
know one, you can type it next time.

```text
/bot Alice tp me                 come to me
/bot Alice jump                  jump once
/bot Alice move forward for 40   walk forward for 2 seconds
/bot Alice attack continuous     keep attacking
```

Times are in **ticks**: 20 ticks is one second.

## 3. Stop it

```text
/bot Alice stop
```

`stop` ends everything the Bot is doing: movement, actions and mining. The Bot stays online. In the menu it is
**Stop everything** at the top.

To take the Bot off the server, use `/bot Alice despawn`. It stays registered, and `/bot Alice spawn` brings it back.

## Things to try

### Chop a tree

Give the Bot an axe (drop one near it), then stand it next to a tree:

```text
/bot Alice hold iron_axe
/bot Alice destroy oak_log count 5
```

The Bot turns to the nearest oak log it can reach and breaks five. It does not walk, so bring it within reach first
(`tp me`). Mining is ordinary Survival breaking: the right tool is faster, and the logs drop as items.

### Feed animals

Hold the food in the Bot's hand and stand it near the animals:

```text
/bot Alice hold wheat
/bot Alice interact @cow count 3
```

The Bot feeds three different cows, one at a time.

### Control several Bots at once

Put Bots in a group and address the group with `#`:

```text
/botgroup create #miners
/botgroup add #miners Alice
/botgroup add #miners Bob
/bot #miners tp me
/bot #miners stop
```

`#miners[1]` is the first member alone. Groups have a menu too: **Manage groups...** in `/bot`. See
[GROUPS.md](GROUPS.md).

### Control Bots from your phone or PC browser

If the operator has set up the Endbot web, type `/botweb link`. You get a short code to enter on the web page. See
[linking the web](COMMANDS.md#linking-the-web).

### Automate with a script

If the operator has turned scripting on and you have the scripter role, scripts can repeat operations with loops and
conditions. They can also start by themselves when something happens in the world. Start with the
[scripting tutorial](SCRIPTING_TUTORIAL.md).

## When something does not happen

- **A reply means the request was accepted, not that it worked.** The game still decides collision, reach and what
  breaks. Check with `/bot Alice status`, which shows where the Bot is and what it is doing.
- **`... failed: Bot is offline`**: bring it back with `/bot Alice spawn`.
- **`/bot` is not recognized**: you have no grant yet; ask the operator.
- **`Bot Alice is private to ...`**: the Bot belongs to another player, who has made it private. See
  [PERMISSIONS.md](PERMISSIONS.md).
- **You forgot a command**: `/bot help` lists them, and `/bot help destroy` explains one operation.

## Where next

- [Commands](COMMANDS.md): every operation, the menus and what the replies mean
- [Groups](GROUPS.md): operating many Bots as one target
- [Scripting tutorial](SCRIPTING_TUTORIAL.md): from a first script to reacting to events
