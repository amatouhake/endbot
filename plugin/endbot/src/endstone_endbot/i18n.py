"""Player-facing text in English (the source) and Japanese.

English templates are the message IDs: English output is the template itself,
so the default wording lives next to the code that uses it. Other languages
map a template to a translation with the same ``{placeholders}``.

Only text shown to players is translated. Command keywords and syntax
(``move``, ``for <ticks>``...), the server console and logs stay English.
Translations must use glyphs the Bedrock chat font has: kana, kanji, ASCII
and the ideographic comma/full stop. Symbols such as arrows render as boxes.
"""

from __future__ import annotations

LANGUAGES = ("en", "ja")
LANGUAGE_SETTINGS = ("auto", *LANGUAGES)
DEFAULT_LANGUAGE_SETTING = "auto"


class Text(str):
    """An English template translated when passed to :func:`tr` as a parameter."""

    __slots__ = ()


class LocalizedError(ValueError):
    """A ``ValueError`` whose English message is a template plus parameters."""

    def __init__(self, template: str, **params: object) -> None:
        super().__init__(_format(template, params, "en"))
        self.template = template
        self.params = params


def language_setting(value: object) -> str:
    """Validate the ``[ui] language`` plugin setting."""
    if value is None:
        return DEFAULT_LANGUAGE_SETTING
    setting = str(value).strip().lower()
    if setting not in LANGUAGE_SETTINGS:
        raise ValueError(f"[ui] language must be one of {', '.join(LANGUAGE_SETTINGS)}")
    return setting


def resolve_language(setting: str, locale: object = None) -> str:
    """The language for one player: a fixed setting, or ``auto`` from the client locale."""
    if setting != "auto":
        return setting
    code = str(locale or "").replace("-", "_").split("_")[0].lower()
    return code if code in LANGUAGES else "en"


def tr(language: str, template: str, **params: object) -> str:
    """Translate ``template`` and fill its placeholders; :class:`Text` values are translated too."""
    return _format(CATALOGS.get(language, {}).get(template, template), params, language)


def localize_error(error: BaseException, language: str) -> str:
    """Player-facing text for an error; unknown messages (e.g. runtime ones) stay as they are."""
    if isinstance(error, LocalizedError):
        return tr(language, error.template, **error.params)
    return tr(language, str(error))


def _format(text: str, params: dict[str, object], language: str) -> str:
    if not params:
        return text
    return text.format(
        **{key: tr(language, value) if isinstance(value, Text) else value for key, value in params.items()}
    )


JA: dict[str, str] = {
    # Connection states shown in list/status/forms.
    "online": "オンライン",
    "offline": "オフライン",
    "connecting": "接続中",
    "reconnecting": "再接続中",
    "failed": "失敗",
    # Help.
    "--- Endbot help page {page} of {total}: {title} ---": "--- Endbot ヘルプ {page}/{total}: {title} ---",
    "lifecycle, status and teleport": "接続と状態、テレポート",
    "movement and actions": "移動とアクション",
    "inventory, blocks and Bot settings": "インベントリ、ブロック、Bot の設定",
    "connectivity, profiles, this help": "疎通確認、Bot 一覧、このヘルプ",
    "Next page:": "次のページ:",
    " - detail:": " - 詳細:",
    "Detail:": "詳細:",
    "(for example": "(例:",
    "Endbot help: {operation}": "Endbot ヘルプ: {operation}",
    "create or connect a Bot": "Bot を作成または接続する",
    "First spawn creates a profile and places the arriving Bot at its caller.": (
        "初めての spawn でプロファイルを作り、Bot をコマンドを出した人の場所に出します。"
    ),
    "From the server console, spawn without coordinates uses the world spawn.": (
        "サーバコンソールから座標なしで spawn すると、ワールドのスポーン地点に出ます。"
    ),
    "Spawning an offline profile reuses its identity with the new placement.": (
        "オフラインの Bot を spawn すると、同じ ID のまま新しい場所に出ます。"
    ),
    "reconnect a profile without moving it": "場所を変えずに Bot を再接続する",
    "Keeps the stored identity and lets world persistence win.": (
        "保存済みの ID を使い、位置はワールドに保存された場所になります。"
    ),
    "restart the session in place": "その場でセッションを張り直す",
    "Tears down and reconnects without changing identity or location.": "ID も場所も変えずに切断して再接続します。",
    "take a Bot offline (stays registered)": "Bot をオフラインにする (登録は残る)",
    "Cancels automatic reconnect; resume or spawn brings it back.": (
        "自動再接続も止めます。resume か spawn で戻せます。"
    ),
    "remove an offline profile registration": "オフラインの Bot の登録を削除する",
    "Requires an offline Bot; BDS player data is left alone.": (
        "Bot がオフラインのときだけ使えます。BDS のプレイヤーデータは消しません。"
    ),
    "rename a Bot, keeping its identity": "ID はそのままで Bot の名前を変える",
    "Reconnects automatically so the login name changes.": "ログイン名を変えるため自動で再接続します。",
    "show lifecycle state and position": "接続状態と位置を表示する",
    "Combines runtime state with the live position when spawned.": (
        "runtime の状態と、ワールドにいるときは現在の位置を表示します。"
    ),
    "teleport a Bot": "Bot をテレポートする",
    "Uses the server teleport; never dispatches vanilla /tp.": (
        "サーバのテレポート機能を使います。バニラの /tp は実行しません。"
    ),
    "~ coordinates are relative to the command source.": "~ の座標はコマンドを出した人からの相対です。",
    "walk a Bot in a direction": "Bot を歩かせる",
    "Movement acts as held input; stop halts it. for stops it after that many ticks.": (
        "移動は押しっぱなしの入力として続き、stop で止まります。for を付けるとその tick 数で止まります。"
    ),
    "turn a Bot's head": "Bot の向きを変える",
    "look at turns toward world coordinates.": "look at はワールド座標の方を向きます。",
    "make a Bot jump": "Bot をジャンプさせる",
    "Omitting the mode means once, or continuous with for; ticks are 1-72000.": (
        "mode を省くと once、for があれば continuous になります。tick 数は 1-72000 です。"
    ),
    "for is how long the input keeps going; once cannot take it.": (
        "for は入力を続ける長さです。once には付けられません。"
    ),
    "make a Bot attack": "Bot に攻撃させる",
    "Continuous actions run every input tick; for stops them after that many ticks.": (
        "continuous は毎 tick 実行します。for を付けるとその tick 数で止まります。"
    ),
    "use the held item in air": "持っているアイテムを空中で使う",
    "use for holds use that long, counted from the first press, then releases.": (
        "use for は最初に押してからその長さだけ押し続け、離します。"
    ),
    "hold charges each press (bow, trident) and releases; for limits the repeats.": (
        "hold は押すたびにため (弓、トライデント)、離します。for で繰り返す長さを決めます。"
    ),
    "With an empty hand it is a safe no-op; blocks need interact.": (
        "手が空なら何も起きません。ブロックへの操作は interact を使います。"
    ),
    "toggle sprinting": "スプリントを切り替える",
    "toggle sneaking": "スニークを切り替える",
    "Emits Bedrock start/stop transitions plus held state.": ("Bedrock の開始と終了の操作を送り、状態を保ちます。"),
    "show or select the hotbar slot": "ホットバーのスロットを表示または選択する",
    "Slots use human-facing 1-9 numbering through Bedrock equipment.": (
        "スロットは画面と同じ 1-9 の番号で、Bedrock の装備変更として送ります。"
    ),
    "show or set the Bot chunk radius": "Bot のチャンク半径を表示または設定する",
    "Overrides this Bot only, persists across reconnect/restart, and applies live.": (
        "この Bot だけの設定で、再接続や再起動の後も残り、すぐに反映されます。"
    ),
    "default restores the shared runtime setting; BDS may clamp the requested range.": (
        "default で共通の runtime 設定に戻します。BDS が値を制限することがあります。"
    ),
    "right-click a block face": "ブロックの面を右クリックする",
    "BDS decides reach, legality and inventory changes.": "届く距離、可否、インベントリの変化は BDS が決めます。",
    "break reachable blocks with the held tool (hold left-click)": (
        "持っている道具で届くブロックを壊す (左クリック長押し)"
    ),
    "once (default) breaks one block in sight; x y z breaks that block. ~ is relative to the Bot.": (
        "once (既定) は視線上のブロックを 1 つ壊し、x y z はそのブロックを壊します。~ は Bot からの相対です。"
    ),
    "continuous holds left-click along the Bot's sight, also after you log off; waits when empty.": (
        "continuous は視線の先で左クリックを押し続けます。ログアウト後も続き、ブロックがなければ待ちます。"
    ),
    "Other commands do not cancel it; only destroy stop, stop, death or disconnect end it.": (
        "ほかのコマンドでは止まりません。destroy stop、stop、死亡、切断でだけ終わります。"
    ),
    "for ends continuous mining after that many ticks, aborting a block in progress.": (
        "for を付けるとその tick 数で連続採掘を終え、壊している途中のブロックはやめます。"
    ),
    "count ends it after that many broken blocks; waiting costs nothing, and both limits combine.": (
        "count を付けるとその数のブロックを壊したところで終わり、待ち時間は消費しません。for と併用できます。"
    ),
    "No interval mode. No automatic walking; BDS controls speed, drops and durability.": (
        "interval はありません。自動では歩きません。速さ、ドロップ、耐久値は BDS が決めます。"
    ),
    "drop the selected item": "選択中のアイテムを捨てる",
    "drop drops one item; drop stack drops the selected stack.": "drop は 1 個、drop stack はスタックごと捨てます。",
    "halt movement and actions": "移動とアクションを止める",
    "Clears movement, actions, sprint and sneak; stays connected.": (
        "移動、アクション、スプリント、スニークを止めます。接続は切りません。"
    ),
    "check the command path": "コマンドが届くか確かめる",
    "Answers pong without touching the runtime.": "runtime を使わずに pong と答えます。",
    "list every Bot profile": "すべての Bot を一覧表示する",
    "Shows each profile name with its lifecycle state.": "Bot の名前と接続状態を表示します。",
    "open the Bot control forms (players only)": "Bot 操作フォームを開く (プレイヤーのみ)",
    "/bot or /bot menu lists every Bot; <name> menu opens that Bot's actions directly.": (
        "/bot か /bot menu で Bot 一覧を、<name> menu でその Bot の操作を開きます。"
    ),
    "Forms run the same /bot commands, so replies and permissions match typed commands.": (
        "フォームは同じ /bot コマンドを実行するので、返信も権限も入力したときと同じです。"
    ),
    "interact, rename, forget and spawn at coordinates stay command-only.": (
        "interact、rename、forget、座標を指定した spawn はコマンドでだけ使えます。"
    ),
    "show this help": "このヘルプを表示する",
    "Pages summarize commands; a command name shows detail.": (
        "ページ番号でコマンドの一覧、コマンド名で詳細を表示します。"
    ),
    # Command syntax errors.
    "{label} must be a number": "{label} は数値で指定してください",
    "{label} must be finite": "{label} は有限の数値で指定してください",
    "coordinate": "座標",
    "Action interval": "interval",
    "Hold": "hold",
    "Duration": "for",
    "Block count": "count",
    "relative coordinate": "相対座標",
    "Expected x y z coordinates": "x y z の座標が必要です",
    "Spawn syntax: at <x> <y> <z> [facing <yaw> <pitch>] [in <dimension>]": (
        "spawn の書き方: at <x> <y> <z> [facing <yaw> <pitch>] [in <dimension>]"
    ),
    "{label} must be an integer number of ticks": "{label} の tick 数は整数で指定してください",
    "{label} must be between 1 and 72000 ticks": "{label} の tick 数は 1 から 72000 で指定してください",
    "{label} must be an integer number of blocks": "{label} のブロック数は整数で指定してください",
    "{label} must be between 1 and 72000 blocks": "{label} のブロック数は 1 から 72000 で指定してください",
    "{action} accepts [once|continuous|interval <ticks>|stop]{hold} [for <ticks>]": (
        "{action} に使えるのは [once|continuous|interval <ticks>|stop]{hold} [for <ticks>] です"
    ),
    "{action} stop takes no ticks": "{action} stop には tick 数を付けられません",
    "{action} interval replaces once/continuous": "{action} の interval は once や continuous と一緒に使えません",
    "{action} once runs one time; use continuous or interval with for <ticks>": (
        "{action} once は 1 回だけです。for <ticks> は continuous か interval と使ってください"
    ),
    "Use interval must be longer than hold": "use の interval は hold より長くしてください",
    "Teleport requires a player or destination": "tp にはプレイヤーか行き先が必要です",
    "Teleport dimension syntax: in <dimension> <x> <y> <z>": (
        "tp でディメンションを指定する書き方: in <dimension> <x> <y> <z>"
    ),
    "facing requires a player or x y z": "facing にはプレイヤーか x y z が必要です",
    "Invalid teleport suffix": "tp の後ろの指定が正しくありません",
    "Help page must be from 1 to {total}": "ヘルプのページは 1 から {total} で指定してください",
    "Unknown help topic '{topic}'. Use /bot help <page> or /bot help <command>": (
        "ヘルプに '{topic}' はありません。/bot help <page> か /bot help <command> を使ってください"
    ),
    "Use /bot help <page> or /bot help <command>": "/bot help <page> か /bot help <command> を使ってください",
    "Expected /bot <name> <action>": "/bot <name> <action> の形で入力してください",
    "Spawn accepts no arguments or: at <x> <y> <z> ...": "spawn は引数なしか at <x> <y> <z> ... で使います",
    "move accepts forward|backward|left|right [for <ticks>], or stop": (
        "move に使えるのは forward|backward|left|right [for <ticks>] か stop です"
    ),
    "Hotbar slot must be an integer from 1 to 9": "ホットバーのスロットは 1 から 9 の整数で指定してください",
    "Hotbar slot must be from 1 to 9": "ホットバーのスロットは 1 から 9 で指定してください",
    "radius accepts an integer from 2 to 32, or default": "radius に使えるのは 2 から 32 の整数か default です",
    "Chunk radius must be from 2 to 32": "チャンク半径は 2 から 32 で指定してください",
    "destroy for <ticks> and count <blocks> run continuous mining; one block has no duration": (
        "destroy for <ticks> と count <blocks> は連続採掘です。1 ブロックだけの採掘には指定できません"
    ),
    "destroy has no interval mode; destroy continuous waits for the next block automatically": (
        "destroy に interval はありません。destroy continuous は次のブロックを自動で待ちます"
    ),
    "Invalid block face": "ブロックの面の指定が正しくありません",
    "destroy accepts once, continuous, stop, [continuous] for <ticks> [count <blocks>], or x y z [face]": (
        "destroy に使えるのは once、continuous、stop、[continuous] for <ticks> [count <blocks>]、x y z [face] です"
    ),
    "Unknown or invalid Bot action: {operation}": "不明または正しくない Bot の操作です: {operation}",
    "{operation} failed: {error}": "{operation} に失敗しました: {error}",
    # Command replies.
    "menu is available only to players; use /bot help": "menu はプレイヤーだけが使えます。/bot help を使ってください",
    "runtime control is not configured": "runtime の制御が設定されていません",
    "no Bot profiles": "Bot はまだありません",
    "already connecting": "すでに接続中です",
    "already reconnecting": "すでに再接続中です",
    "already online": "すでにオンラインです",
    "already active": "すでに動いています",
    "created and connecting": "作成して接続中",
    "{verb} ({where})": "{verb} ({where})",
    "at the world spawn": "ワールドのスポーン地点",
    "where it last was": "前回の場所",
    "teleported to {location}": "{location} にテレポートしました",
    " for {ticks} ticks": " ({ticks} tick の間)",
    " until {blocks} blocks": " ({blocks} ブロックまで)",
    "destroy continuous started{duration}{count}; destroy stop to cancel": (
        "destroy continuous を始めました{duration}{count}。destroy stop で止められます"
    ),
    "destroy stopped": "destroy を止めました",
    "destroying {block} at {position}; see status for the result": (
        "{position} の {block} を壊しています。結果は status で確認できます"
    ),
    "hotbar slot {slot}": "ホットバーのスロット {slot}",
    "hotbar slot {slot} selected": "ホットバーのスロット {slot} を選びました",
    "drop one item requested": "1 個捨てるよう要求しました",
    "drop stack requested": "スタックごと捨てるよう要求しました",
    "movement stopped": "移動を止めました",
    "movement set to {direction}{duration}": "移動を {direction} にしました{duration}",
    "look set to yaw={yaw} pitch={pitch}": "向きを yaw={yaw} pitch={pitch} にしました",
    ": hold {ticks} ticks, then release": " (毎回 {ticks} tick 押してから離す)",
    "{action} stopped": "{action} を止めました",
    "{action} started every {interval} ticks{hold}{duration}": (
        "{action} を {interval} tick ごとに始めました{hold}{duration}"
    ),
    "{action} repeating{hold}{duration}": "{action} を繰り返しています{hold}{duration}",
    "{action} started for {ticks} ticks, then releases": "{action} を {ticks} tick 押し続けてから離します",
    "{action} continuous started{duration}": "{action} continuous を始めました{duration}",
    "{action} once requested{hold}": "{action} once を要求しました{hold}",
    "{flag} on": "{flag} を on にしました",
    "{flag} off": "{flag} を off にしました",
    "movement and actions stopped (sprint/sneak off)": "移動とアクションを止めました (sprint と sneak も off)",
    "block interaction requested at {position} face={face}": "{position} の面 {face} への操作を要求しました",
    "renamed to {name} ({state})": "名前を {name} に変えました ({state})",
    "profile registration removed": "Bot の登録を削除しました",
    "{operation} requested ({state})": "{operation} を要求しました ({state})",
    # Status.
    "desired={state}": "目標={state}",
    "error={error}": "エラー={error}",
    "Bot override": "この Bot の設定",
    "shared default": "共通の既定値",
    "server confirmed {radius}": "サーバ確認済み {radius}",
    "server confirmation pending": "サーバの確認待ち",
    "applies when online": "オンライン時に反映",
    "chunk radius {radius} ({source}); {server}": "チャンク半径 {radius} ({source})、{server}",
    "move {direction} {ticks} ticks left": "move {direction} 残り {ticks} tick",
    "{ticks} ticks left": "残り {ticks} tick",
    "{done} blocks": "{done} ブロック",
    "{done}/{total} blocks": "{done}/{total} ブロック",
    "timed: {inputs}": "時間指定: {inputs}",
    "{block} at {position}": "{position} の {block}",
    "destroying {target}{mode}": "{target} を採掘中{mode}",
    "destroy continuous waiting for a block in sight{notes}": (
        "destroy continuous は視線の先のブロックを待っています{notes}"
    ),
    "last: {target}": "前回: {target}",
    "last destroy: {outcome}{target}": "前回の destroy: {outcome}{target}",
    "completed": "完了",
    "failed: {reason}": "失敗: {reason}",
    "canceled": "中止",
    "canceled: {reason}": "中止: {reason}",
    # Mining end reasons and notices.
    "block broken or changed": "ブロックが壊れたか変わりました",
    "stopped": "停止",
    "replaced by a new target": "新しい対象に切り替え",
    "replaced by destroy continuous": "destroy continuous に切り替え",
    "canceled by move": "move で中止",
    "canceled by look": "look で中止",
    "canceled by attack": "attack で中止",
    "canceled by use": "use で中止",
    "canceled by hotbar change": "ホットバーの変更で中止",
    "held item changed": "持っているアイテムが変わりました",
    "canceled by interact": "interact で中止",
    "canceled by drop": "drop で中止",
    "canceled by teleport": "テレポートで中止",
    "canceled by dimension change": "ディメンションの移動で中止",
    "canceled by death": "死亡で中止",
    "Bot disconnected": "Bot が切断されました",
    "target out of reach": "対象に届きません",
    "lost contact with the Endbot plugin": "Endbot プラグインとの連絡が途絶えました",
    "block did not break within 10 minutes; protected or unbreakable?": (
        "10 分たってもブロックが壊れませんでした。保護されているか、壊せないブロックかもしれません"
    ),
    "duration elapsed": "指定の時間が終わりました",
    "block count reached": "指定のブロック数に達しました",
    "ended": "終了",
    "{command} stopped: {reason}": "{command} が止まりました: {reason}",
    "control permission revoked": "操作権限が取り消されました",
    "Bot was renamed or removed": "Bot の名前が変わったか、削除されました",
    # Dispatch and plugin.
    "Bot control permission was revoked; command canceled": "Bot の操作権限が取り消されたため、コマンドを中止しました",
    "internal command failure": "コマンドの内部エラーです",
    "you do not have Bot control permission": "Bot の操作権限がありません",
    "command service is unavailable": "コマンドを受け付けられません",
    "Endbot plugin is disabled": "Endbot プラグインが無効になっています",
    "Command player is no longer online": "コマンドを出したプレイヤーがもういません",
    # World errors.
    "Bot is not present in the world": "Bot がワールドにいません",
    "tp me requires a player command source": "tp me はプレイヤーだけが使えます",
    "Player {name} is not online": "プレイヤー {name} はオンラインではありません",
    "Endstone rejected the teleport": "Endstone がテレポートを拒否しました",
    "An online player named {name} already exists": "{name} という名前のプレイヤーがすでにオンラインです",
    "Target block is out of reach (maximum 5 blocks)": "対象のブロックに届きません (最大 5 ブロック)",
    "Target block overlaps the Bot's eye position": "対象のブロックが Bot の目の位置と重なっています",
    "Target is air/liquid or blocked by another block": "対象が空気か液体、またはほかのブロックにさえぎられています",
    "No block in the Bot's line of sight within 5 blocks": "Bot の視線の先 5 ブロック以内にブロックがありません",
    "Bedrock cannot be destroyed in Survival": "サバイバルでは岩盤を壊せません",
    "A dimension is required for this command source": "このコマンドの出し方ではディメンションの指定が必要です",
    "Relative coordinates require a player command source": "相対座標はプレイヤーだけが使えます",
    "Unknown dimension: {dimension}": "不明なディメンションです: {dimension}",
    # Forms.
    "JOIN": "接続",
    "RETRY": "再試行",
    "FAIL": "失敗",
    "Back to Bot list": "Bot 一覧に戻る",
    "Endbot: offline Bots": "Endbot: オフラインの Bot",
    "Pick a Bot.": "Bot を選んでください。",
    "Offline ({count})...": "オフライン ({count})...",
    "+ New Bot here": "+ ここに新しい Bot",
    "Pick a Bot. (/bot help lists commands)": "Bot を選んでください。(/bot help でコマンド一覧)",
    "No Bot profiles yet. Spawn one to start.": "Bot はまだいません。spawn して始めましょう。",
    "chunk radius {radius}{source}": "チャンク半径 {radius}{source}",
    " (shared)": " (共通)",
    "Back to Bot": "Bot に戻る",
    "{name}: {title}": "{name}: {title}",
    "movement": "移動",
    "teleport": "テレポート",
    "gameplay": "ゲームプレイ",
    "inventory": "インベントリ",
    "bot": "Bot",
    "Teleport to me": "自分のところへテレポート",
    "Teleport...": "テレポート...",
    "Stop everything": "すべて止める (stop)",
    "Movement...": "移動...",
    "Move...": "歩く (move)...",
    "Jump...": "ジャンプ (jump)...",
    "Sprint / sneak...": "スプリント / スニーク...",
    "Look...": "向きを変える (look)...",
    "Gameplay...": "ゲームプレイ...",
    "Attack...": "攻撃 (attack)...",
    "Use item...": "アイテムを使う (use)...",
    "Destroy blocks...": "ブロックを壊す (destroy)...",
    "Inventory...": "インベントリ...",
    "Hotbar slot...": "ホットバーのスロット (hotbar)...",
    "Drop item...": "アイテムを捨てる (drop)...",
    "Bot...": "Bot...",
    "Reconnect": "再接続 (reconnect)",
    "Despawn...": "オフラインにする (despawn)...",
    "Chunk radius...": "チャンク半径 (radius)...",
    "Resume (where it was)": "前回の場所で再開 (resume)",
    "Spawn here": "ここに出す (spawn)",
    "mining continuous": "連続採掘中",
    "mining active": "採掘中",
    "Spawn a new Bot": "新しい Bot を出す",
    "Bot name (letters, digits, underscore; up to 16)": "Bot の名前 (英数字とアンダースコア、16 文字まで)",
    "Spawn": "出す",
    "enter a Bot name": "Bot の名前を入力してください",
    "Or look at x y z (overrides the sliders; ~ is relative to the Bot)": (
        "または look at x y z (スライダーより優先。~ は Bot からの相対)"
    ),
    "Look": "向く",
    "Sprint": "スプリント",
    "Sneak": "スニーク",
    "no change": "変更しない",
    "Apply": "適用",
    "Run": "実行",
    "20 = 1 second": "20 で 1 秒",
    "for <ticks> (optional; {ticks}; blank = until stopped)": "for <ticks> (任意。{ticks}。空欄なら止めるまで)",
    "count <blocks> (optional; stop after that many blocks)": (
        "count <blocks> (任意。その数のブロックを壊したら停止)"
    ),
    "hold <ticks> (optional; charge each press, then release)": "hold <ticks> (任意。押すたびにこの長さためてから離す)",
    "interval <ticks> ({ticks})": "interval <ticks> ({ticks})",
    "for <ticks> ({ticks})": "for <ticks> ({ticks})",
    "Pick a mode, as in the typed command. Ticks: {ticks}.": "コマンドと同じ mode を選んでください。tick: {ticks}。",
    "enter {clause} <ticks>": "{clause} <ticks> を入力してください",
    "direction": "方向",
    "x y z (~ is relative to the Bot)": "x y z (~ は Bot からの相対)",
    "automatic": "自動",
    "Destination": "行き先",
    "coordinates below": "下の座標",
    "x y z (coordinates only; ~ is relative to you)": "x y z (座標のときだけ。~ は自分からの相対)",
    "Teleport": "テレポート",
    "Hotbar slot": "ホットバーのスロット",
    "Select": "選ぶ",
    "one item": "1 個",
    "whole stack": "スタックごと",
    "Amount from the selected hotbar slot": "選択中のスロットから捨てる量",
    "Drop": "捨てる",
    "{name}: chunk radius": "{name}: チャンク半径",
    "Use the shared default": "共通の既定値を使う",
    "Chunk radius (this Bot only)": "チャンク半径 (この Bot だけ)",
    "Take {name} offline? It stays registered; resume or spawn brings it back.": (
        "{name} をオフラインにしますか? 登録は残り、resume か spawn で戻せます。"
    ),
    "Despawn": "オフラインにする",
    "Cancel": "キャンセル",
    "the form response was not understood": "フォームの応答を読み取れませんでした",
    "enter exactly three coordinates: x y z": "座標は x y z の 3 つを入力してください",
    "nothing to change": "変更はありません",
    "Unknown Endbot command root": "Endbotコマンドの種類が不明です",
}

CATALOGS: dict[str, dict[str, str]] = {"ja": JA}
