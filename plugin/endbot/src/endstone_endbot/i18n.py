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
    "exchange two inventory stacks": "持ち物の二つのスタックを交換する",
    "Slots are 1-36. An empty side is a move; equipment is excluded. BDS validates the request.":
        "スロットは1-36です。片方が空なら移動します。装備は対象外です。BDS が要求を検証します。",
    "select an item by exact identifier": "アイテムIDの完全一致で手に持つ",
    "Selects the lowest matching hotbar slot, or swaps the lowest main stack into the selected hotbar slot.":
        "一致する最小番号のホットバースロットを選びます。なければメインの最小番号から選択中のスロットへ交換します。",
    "Item IDs accept minecraft: or no prefix. Replies describe the request; BDS validates it.":
        "アイテムIDの minecraft: は省略できます。返答は要求内容です。BDS が検証します。",
    "Use swap <a> <b>": "swap <a> <b> を使用してください",
    "Use hold <item>": "hold <item> を使用してください",
    "Inventory slots must be from 1 to 36": "持ち物のスロットは1から36で指定してください",
    "Swap requires two different slots": "swap には異なる二つのスロットを指定してください",
    "Both inventory slots are empty": "両方のスロットが空です",
    "Invalid hold item identifier": "手に持つアイテムのIDが正しくありません",
    "Item {item} was not found": "アイテム {item} が見つかりません",
    "swap {a} {b} requested": "swap {a} {b} を要求しました",
    "hold {item}: selected hotbar {slot} requested": "hold {item}: ホットバー {slot} の選択を要求しました",
    "hold {item}: moved slot {source} to hotbar {slot} requested":
        "hold {item}: スロット {source} からホットバー {slot} への移動を要求しました",
    "Hold item...": "アイテムを手に持つ (hold)...",
    "Swap slots...": "スロットを交換する (swap)...",
    "First slot (1-36)": "一つ目のスロット (1-36)",
    "Second slot (1-36)": "二つ目のスロット (1-36)",
    "request drops from inventory and equipment": "持ち物と装備を落とすよう要求する",
    "drop drops one selected item; stack drops the selected stack; a slot drops its whole stack.":
        "drop は選択中の1個、stack は選択中のスタック、スロット指定はそのスタック全体を落とすよう要求します。",
    "Slots: 1-36, offhand, head, chest, legs, feet. Item IDs accept minecraft: or no prefix.":
        "スロット: 1-36、offhand、head、chest、legs、feet。アイテムIDの minecraft: は省略できます。",
    "An item without count drops every matching stack; count takes up to that many, lowest slots first.":
        "個数を省略すると一致する全スタック、指定すると小さいスロット番号からその個数まで要求します。",
    "drop all immediately requests every inventory and equipment stack. BDS validates every request.":
        "drop all は持ち物と装備の全スタックを即座に要求します。BDS が各要求を検証します。",
    "Use drop [stack|all|<slot>|<item> [count]]": "drop [stack|all|<slot>|<item> [count]] を使用してください",
    "A slot or all does not accept a count": "スロット、stack、all に個数は指定できません",
    "Invalid drop item identifier": "落とすアイテムのIDが正しくありません",
    "Drop count must be an integer from 1 to 10455": "落とす個数は1から10455の整数で指定してください",
    "Invalid drop selector": "落とす対象の指定が正しくありません",
    "Invalid drop stack flag": "スタック指定が正しくありません",
    "Unknown drop item": "落とすアイテムが不明です",
    "drop all requested for {slots} slots": "{slots} スロットの drop all を要求しました",
    "drop {item} x{count} requested (asked for {asked})": "{item} x{count} を落とすよう要求しました (指定個数 {asked})",
    "drop {item} x{count} requested from {slots} slots":
        "{slots} スロットから {item} x{count} を落とすよう要求しました",
    "drop slot {slot} requested; slot is empty": "スロット {slot} を落とすよう要求しました。スロットは空です",
    "drop slot {slot} {item} x{count} requested": "スロット {slot} の {item} x{count} を落とすよう要求しました",
    "BDS validates each drop request.": "BDS が落とす要求を検証します。",
    "Selected one / stack...": "選択中の1個またはスタック...",
    "A slot...": "スロットを指定...",
    "An item...": "アイテムを指定...",
    "Inventory slot (whole stack)": "持ち物スロット (スタック全体)",
    "Item identifier": "アイテムID",
    "Count (blank = every matching stack)": "個数 (空欄で一致する全スタック)",
    "Enter one item identifier": "アイテムIDを1つ入力してください",
    "Drop all...": "すべて落とす...",
    "Drop all": "すべて落とす",
    "Request every inventory and equipment stack to be dropped?": "持ち物と装備の全スタックを落とすよう要求しますか?",
    "Pick a Bot or group. (/bot help lists commands)": "Bot またはグループを選んでください。(/bot help でコマンド一覧)",
    "Back to group operations": "グループの操作へ戻る",
    "Back to group management": "グループの管理へ戻る",
    "Group management...": "グループ管理...",
    "Members {count}, online {online}": "メンバー {count} 体、オンライン {online} 体",
    "Members {count} - page {page}/{pages}. Open a member for individual operations.":
        "メンバー {count} 体 - {page}/{pages} ページ。各メンバーを開くと単体操作ができます。",
    "Spawn at coordinates...": "座標を指定してスポーン...",
    "Status": "状態を表示",
    "List inventory": "持ち物を表示",
    "inventory: {items}": "持ち物: {items}",
    "inventory is empty": "持ち物は空です",
    "show non-empty inventory and equipment slots": "持ち物と装備の空でないスロットを表示",
    "Slots are hotbar 1-9, main 10-36, offhand, head, chest, legs and feet.":
        "スロットはホットバー 1-9、持ち物 10-36、オフハンド、頭、胴、脚、足です。",
    "offhand": "オフハンド",
    "head": "頭",
    "chest": "胴",
    "legs": "脚",
    "feet": "足",
    "Profile...": "プロファイル...",
    "profile": "プロファイル",
    "Rename...": "名前を変更...",
    "Rename": "名前を変更",
    "New Bot name": "新しい Bot の名前",
    "Forget...": "登録を削除...",
    "Forget": "登録を削除",
    "Forget {name}? Requires an offline Bot. Its world player data stays intact.":
        "{name} の登録を削除しますか? オフラインの Bot が対象です。ワールド内のプレイヤーデータは残ります。",
    "Interact with block...": "ブロックを操作...",
    "List nearby entities": "近くのエンティティを一覧表示",
    "Interact with entity...": "エンティティを操作...",
    "Dismount": "降りる",
    "Entity type, nearest or id (from the list)": "エンティティの種類、nearest、または一覧の id",
    "Enter one entity type, nearest or id": "エンティティの種類、nearest、id のどれかを1つ入力してください",
    "Interact": "操作",
    "Chunk radius (per Bot)": "チャンク半径 (各 Bot に適用)",
    "Bot and Group operation menus share the same controls; Group management uses /botgroup.":
        "Bot とグループの操作メニューは共通です。グループの管理は /botgroup を使います。",
    # Shared groups and bounded operation results.
    "+ New group...": "+ グループを作成...",
    ", other {count}": ", ほか {count} 件",
    ", unknown {count}": ", 受付不明 {count}",
    "Add": "追加",
    "Add a member to {name}": "{name} に Bot を追加",
    "Add member...": "メンバーを追加...",
    "Back to group": "グループへ戻る",
    "Back to groups": "グループ一覧へ戻る",
    "Bot": "Bot",
    "Bot identity changed": "Bot の識別情報が変更された",
    "Create": "作成",
    "Create a group": "グループを作成",
    "Delete {name}": "{name} を削除",
    "Delete group...": "グループを削除...",
    "Delete {name}? Its Bots stay registered.": "{name} を削除しますか? Bot 自体の登録は残ります。",
    "Endbot: groups": "Endbot: グループ",
    "Group operations...": "グループを一括操作...",
    "Group reference (#name)": "グループ指定 (#名前)",
    "Member": "メンバー",
    "Next page": "次のページ",
    "Previous page": "前のページ",
    "Operations apply to every current member. Limits apply per Bot.":
        "現在の全メンバーが対象です。回数や個数の制限は各 Bot に適用します。",
    "Pick a group.": "グループを選んでください。",
    "Reasons: {reasons}": "理由: {reasons}",
    "Recent operation result...": "直近の操作結果...",
    "Remove": "外す",
    "Remove a member from {name}": "{name} から Bot を外す",
    "Newer result": "新しい結果",
    "Older result": "一つ前の結果",
    "Remove member...": "メンバーを外す...",
    "acceptance unknown": "受付不明",
    "accepted": "受付",
    "not run": "未実行",
    "missing": "プロファイルなし",
    "count <blocks> (optional; per Bot)": "count <blocks> (任意、各 Bot あたりのブロック数)",
    "missing Bot profile": "Bot プロファイルなし",
    "other errors": "その他のエラー",
    "permission revoked or plugin stopped": "権限取消またはプラグイン停止",
    "runtime unavailable": "runtime と通信できない",
    "{operation}: accepted {accepted}, failed {failed}, not run {skipped}":
        "{operation}: 受付 {accepted}, 失敗 {failed}, 未実行 {skipped}",
    "{reason} {count}": "{reason} {count}",
    "{target} {operation} - history {history}/{total} (1 = latest)":
        "{target} {operation} - 履歴 {history}/{total} (1 = 最新)",
    "Result history must be between 1 and {count} (1 = latest)":
        "結果の履歴は 1 から {count} の範囲で指定してください (1 = 最新)",
    "Result history must be a positive integer (1 = latest)":
        "結果の履歴は 1 以上の整数で指定してください (1 = 最新)",
    "No recent group result for this controller (results reset on restart)":
        "この実行者の直近のグループ操作結果はありません (再起動すると結果は消去されます)",
    "Group changed while the menu was open; reopen the menu":
        "メニュー表示中にグループが変更されました。メニューを開き直してください",
    "Bot changed while the menu was open; reopen the menu":
        "メニュー表示中に Bot が変更されました。メニューを開き直してください",
    "Result is no longer retained; reopen the result history": "結果の保持期限が切れました。履歴を開き直してください",
    "Page must be a positive integer": "ページは 1 以上の整数で指定してください",
    "Group member page is out of range": "メンバー一覧のページが範囲外です",
    "Group syntax: #group (for example #miners)": "グループは #group の形式で指定してください (例: #miners)",
    "Group names must start with a letter/underscore and contain 1-16 letters, digits or underscores":
        "グループ名は英字かアンダースコアで始まる 1-16 文字の英数字・アンダースコアにしてください",
    "Group {name} does not exist": "グループ {name} はありません",
    "Group {name} already exists": "グループ {name} はすでにあります",
    "Bot is already a group member": "この Bot はすでにグループのメンバーです",
    "Bot is not a group member": "この Bot はグループのメンバーではありません",
    "Group target syntax: #group or #group[1] (one-based)":
        "グループは #group または #group[1] (1 から数える) の形式で指定してください",
    "{target}: member index is out of range": "{target}: メンバー番号が範囲外です",
    "Bot {name} does not exist; add an existing profile": "Bot {name} はありません。登録済みの Bot を追加してください",
    "{operation} requires one Bot; use #group[1] or a Bot name":
        "{operation} は 1 体の Bot が対象です。#group[1] か Bot 名を指定してください",
    "Groups require the matching updated Endbot runtime":
        "グループ機能には対応する更新済みの Endbot runtime が必要です",
    "missing Bot profile; membership retained": "Bot プロファイルなし (メンバー登録は残しています)",
    "Use /botgroup result #group [history] from the console":
        "コンソールでは /botgroup result #group [history] を使ってください",
    "Remove requires a Bot name or one indexed member of this group":
        "外すには Bot 名か、このグループの番号付きメンバーを 1 体指定してください",
    "Invalid group command; use /botgroup help":
        "グループコマンドが正しくありません。/botgroup help を参照してください",
    "Menu Bot changed; reopen the menu": "メニューの Bot が変更されました。メニューを開き直してください",
    "Group has no members": "グループにメンバーがいません",
    "Groups are unavailable because the groups file could not be loaded; see the server log":
        "グループのファイルを読み込めないため、グループ機能は使えません。サーバーのログを確認してください",
    "Groups must be stored in a regular file": "グループは通常のファイルに保存する必要があります",
    "Groups file must not be a symlink": "グループのファイルにシンボリックリンクは使えません",
    "Invalid groups file": "グループのファイルが正しくありません",
    "Invalid group entry": "グループの項目が正しくありません",
    "Duplicate group member": "グループのメンバーが重複しています",
    "Duplicate group name": "グループ名が重複しています",
    "Duplicate group identity": "グループの識別情報が重複しています",
    "Unexpected request while rendering group input result": "グループ操作の結果を表示中に想定外の要求がありました",
    "Invalid group input batch response": "グループ一括操作の応答が正しくありません",
    "no groups": "グループはありません",
    "group created": "グループを作成しました",
    "group deleted; Bots retained": "グループを削除しました。Bot の登録は残っています",
    "no members": "メンバーはいません",
    "add: {target}": "追加: {target}",
    "remove: {target}": "外しました: {target}",
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
    "list nearby entities": "近くのエンティティを一覧表示する",
    "Shows up to 10 non-player entities within 16 blocks with their id and distance.":
        "16ブロック以内のプレイヤー以外のエンティティを最大10件、id と距離つきで表示します。",
    "get off a minecart, boat or animal": "トロッコ、ボート、動物から降りる",
    "Riding Bots follow their vehicle; status shows what the Bot is riding.":
        "乗っている Bot は乗り物と一緒に動きます。何に乗っているかは status で確認できます。",
    "entities and vehicles": "エンティティと乗り物",
    "no entities nearby": "近くにエンティティはいません",
    "nearby: {entities}": "近く: {entities}",
    "interaction with {entity} @{id} requested": "{entity} @{id} への操作を要求しました",
    "dismount from {vehicle} requested": "{vehicle} から降りるよう要求しました",
    "riding {vehicle}": "{vehicle} に乗車中",
    "right-click a block or an entity": "ブロックかエンティティを右クリックする",
    "No argument right-clicks what the Bot looks at. A block type is the nearest visible one within 6 blocks.":
        "引数なしなら Bot が見ているものを右クリックします。"
        "ブロックの種類なら6ブロック以内で見えている一番近いものです。",
    "@ picks an entity within 6 blocks, turns to it and uses the held item: feed, breed, shear, name, ride.":
        "@ は6ブロック以内のエンティティを選び、その方を向いて手に持ったアイテムを使います。"
        "エサやり、繁殖、毛刈り、名付け、乗車。",
    "Without a face, the side facing the Bot. BDS decides reach and outcome; players are never targets.":
        "面を省くと Bot に向いた面です。届くかどうかと結果は BDS が決めます。プレイヤーは対象外です。",
    "@ turns to the nearest matching entity within 4.5 blocks and attacks it once; players are never targets.":
        "@ は4.5ブロック以内で一番近い該当エンティティの方を向き、1回攻撃します。プレイヤーは対象外です。",
    "A block type breaks the nearest one the Bot can see within 5 blocks.":
        "ブロックの種類を指定すると、5ブロック以内で Bot に見えている一番近いものを壊します。",
    "Entities take @: use @{id}": "エンティティには @ を付けてください: @{id}",
    "Use entity @<type|id|nearest>": "entity @<type|id|nearest> の形で指定してください",
    "Repeat {action} @<entity> with continuous, interval <ticks>, for <ticks> or count <n>":
        "{action} @<entity> の繰り返しには continuous、interval <ticks>、for <ticks>、count <n> を使います",
    "Count must be an integer from 1 to 10000": "count は 1 から 10000 の整数で指定してください",
    "Use destroy <block type> [continuous] [for <ticks>] [count <blocks>]":
        "destroy <block type> [continuous] [for <ticks>] [count <blocks>] の形で指定してください",
    " up to {count} times": " (最大 {count} 回)",
    "Unknown block type: {block}": "不明なブロックの種類です: {block}",
    "No {block} in sight within {reach} blocks": "{reach}ブロック以内に見えている {block} はありません",
    "destroy breaks blocks; use attack @<entity>":
        "destroy はブロックを壊します。エンティティには attack @<entity> を使ってください",
    "attack on {entity} @{id} requested": "{entity} @{id} への攻撃を要求しました",
    "show one nearby entity in detail": "近くのエンティティ1体の詳細を表示する",
    "Position, distance, health and name tag of the nearest match within 16 blocks; players are excluded.":
        "16ブロック以内で一番近い該当エンティティの位置、距離、体力、名前を表示します。プレイヤーは対象外です。",
    "No matching entity within 16 blocks": "16ブロック以内に該当するエンティティがいません",
    "named {name}": "名前 {name}",
    "empty": "空",
    "Entity details...": "エンティティの詳細...",
    "show the block or entity the Bot is looking at": "Bot が見ているブロックかエンティティを表示する",
    "Within 6 blocks of the Bot's eyes; the nearer of a block and a non-player entity.":
        "Bot の目から6ブロック以内で、近い方のブロックかプレイヤー以外のエンティティを表示します。",
    "show one block's type and states": "1つのブロックの種類と状態を表示する",
    "~ is relative to the Bot. Any loaded position can be read.":
        "~ は Bot からの相対位置です。読み込まれた位置なら調べられます。",
    "find the nearest exposed blocks of a type": "見えている指定の種類のブロックを近い順に探す",
    "Only blocks with a face open to air, water or plants count, as a player could see them.":
        "空気、水、植物に面したブロックだけを数えます。プレイヤーに見えるものだけです。",
    "Radius defaults to 8 and limit to 10; results are nearest first.":
        "半径の既定は8、件数の既定は10です。近い順に表示します。",
    "looking around, entities and vehicles": "周囲の確認、エンティティと乗り物",
    "looking at {block} at {position} face {face} ({distance}m)":
        "{position} の {block} の面 {face} を見ています ({distance}m)",
    "looking at {entity} @{id} ({distance}m)": "{entity} @{id} を見ています ({distance}m)",
    "looking at nothing within 6 blocks": "6ブロック以内に見ているものはありません",
    "block at {position}: {block}": "{position} のブロック: {block}",
    "{block}: {positions}": "{block}: {positions}",
    "no exposed {block} within {radius} blocks": "{radius}ブロック以内に見えている {block} はありません",
    "Use block <x> <y> <z>": "block <x> <y> <z> の形で指定してください",
    "Use blocks <type> [radius <1-12>] [limit <1-64>]":
        "blocks <type> [radius <1-12>] [limit <1-64>] の形で指定してください",
    "Invalid block type": "ブロックの種類が正しくありません",
    "{clause} must be an integer from {low} to {high}": "{clause} は {low} から {high} の整数で指定してください",
    "Look around...": "周囲を確認...",
    "look around": "周囲の確認",
    "Looking at": "見ているもの",
    "Block info...": "ブロックを調べる...",
    "Find blocks...": "ブロックを探す...",
    "Enter one block type": "ブロックの種類を1つ入力してください",
    "Block type": "ブロックの種類",
    "Radius": "半径",
    "health {health}/{max}": "体力 {health}/{max}",
    "food {food}": "満腹度 {food}",
    "Invalid entity type": "エンティティの種類が正しくありません",
    "No matching entity within reach": "届く範囲に該当するエンティティがいません",
    "No entity within reach": "届く範囲にエンティティがいません",
    "No entity with that ID is in view": "その ID のエンティティが見当たりません",
    "That entity is out of reach": "そのエンティティには届きません",
    "Bot is not riding": "Bot は何にも乗っていません",
    "The vehicle is not in view": "乗り物が見当たりません",
    "An entity interaction is already pending": "エンティティへの操作がすでに待機中です",
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
    "{count} left": "残り {count} 回",
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

JA.update({
    "Delete": "削除",
    "Scripts...": "スクリプト...",
    "+ New script...": "+ 新しいスクリプト...",
    "Endbot: scripts": "Endbot: スクリプト",
    "Pick a script.": "スクリプトを選んでください。",
    "Script templates": "スクリプトのテンプレート",
    "Choose a template, then paste or edit source.": "テンプレートを選び、ソースを貼り付けるか編集してください。",
    "Group row": "グループを一列に並べる",
    "Simple patrol": "簡単な巡回",
    "Mine at spots": "指定地点で採掘",
    "Blank script": "空のスクリプト",
    "Delete script": "スクリプトを削除",
    "Delete script {name}?": "スクリプト {name} を削除しますか?",
    "Show source": "ソースを表示",
    "Stop runs": "実行を停止",
    "Edit source...": "ソースを編集...",
    "Run or edit this shared script.": "この共有スクリプトを実行または編集します。",
    "Script name": "スクリプト名",
    "Luau source (paste multiple lines)": "Luau ソース (複数行を貼り付け)",
    "Edit script": "スクリプトを編集",
    "Save": "保存",
    "{name} ({kind})": "{name} ({kind})",
    "Previous page": "前のページ",
    "Next page": "次のページ",
    "No targets available for script parameters": "スクリプトの引数に選べる対象がありません",
    "Script {name} does not exist": "スクリプト {name} は存在しません",
    "Script source is empty or too large": "ソースが空か、サイズの上限を超えています",
    "Script changed; reopen the menu": "スクリプトが変更されました。メニューを開き直してください",
    "Script parameter target changed; reopen the menu": "引数の対象が変更されました。メニューを開き直してください",
    "Stored script limit reached": "保存できるスクリプト数の上限に達しました",
    "Invalid script parameter declarations": "スクリプトの引数宣言が正しくありません",
    "Script parameters must be an array": "引数宣言は配列で指定してください (名前付きキーは使えません)",
    "Invalid script parameter name": "引数名は英字か下線で始まる32文字以内の英数字と下線で指定してください",
    "Duplicate script parameter name": "引数名が重複しています",
    "Unknown script parameter type": "不明な引数の型です",
    "Invalid script parameter default": "引数の初期値が型に合っていません",
    "Script parameter declaration at {location}: {error}": "{location} の引数宣言: {error}",
    "Too many script arguments": "スクリプトの引数が多すぎます",
    "Unknown script argument": "不明なスクリプト引数です",
    "Invalid script argument: {name} ({kind})": "スクリプト引数が正しくありません: {name} ({kind})",
    "scripting runner not built/installed": "スクリプト実行プログラムがビルドまたはインストールされていません",
    "Script protocol frame too large": "スクリプトの通信データが上限を超えています",
    "Script stopped or time limit reached": "スクリプトが停止されたか、制限時間に達しました",
    "Script runner failed: {error}": "スクリプト実行プログラムでエラー: {error}",
    "Script runner OS confinement failed; see the server log": (
        "スクリプト実行プログラムの OS 制限を設定できません。サーバーログを確認してください"
    ),
    "Script runner process memory allocation failed": "スクリプト実行プログラムのプロセスメモリを確保できません",
    "Script runner exceeded the CPU time limit": "スクリプト実行プログラムが CPU 時間の上限を超えました",
    "Scripting is disabled; enable it in config.toml and restart": (
        "スクリプトは無効です。config.toml で有効にして再起動してください"
    ),
    "Scripts file must not be a symlink": "スクリプトの保存ファイルにシンボリックリンクは使えません",
    "Script service is closed": "スクリプトサービスは停止しています",
    "Scripts are unavailable; see the server log": "スクリプトを利用できません。サーバーログを確認してください",
    "Script authority was revoked": "スクリプトの操作権限が取り消されました",
    "Script declaration failed: {error}": "スクリプトの引数宣言に失敗: {error}",
    "Script concurrency limit reached": "同時実行できるスクリプト数の上限に達しました",
    "Script operation rate limit reached": "スクリプトの操作頻度が上限を超えました",
    "Invalid script group target": "スクリプトのグループ対象が正しくありません",
    "Invalid script runner request": "スクリプト実行プログラムからの要求が正しくありません",
    "Groups could not be saved; see the server log": "グループを保存できませんでした。サーバーのログを確認してください",
    "Invalid script wait": "スクリプトの待機時間が正しくありません",
    "Invalid script target": "スクリプトの対象が正しくありません",
    "Script input cleanup failed; use /bot stop": "スクリプトの入力停止に失敗しました。/bot stop を使ってください",
    "Script {name} ({run}) ended: {reason}": "スクリプト {name} ({run}) が終了: {reason}",
    "Script help: list | show <name> [page] | run <name> [args...] | runs | stop <id|name|all> | delete <name> | menu":
        "スクリプト: list | show <name> [page] | run <name> [args...] | runs | stop <id|name|all> "
        "| delete <name> | menu",
    "Arguments follow parameter declaration order; quote positions. Create/edit by menu paste only.":
        "引数は宣言順です。座標は引用符で囲みます。作成と編集はメニューへの貼り付けで行います。",
    "No stored scripts": "保存されたスクリプトはありません",
    "No active script runs": "実行中のスクリプトはありません",
    "Stopping {count} script runs": "スクリプト {count} 件を停止します",
    "Script {name} saved": "スクリプト {name} を保存しました",
    "Use /botscript help": "/botscript help を使ってください",
    "Script source page is out of range": "ソースのページ番号が範囲外です",
    "Script {name} deleted": "スクリプト {name} を削除しました",
    "Script {name} started: {run}": "スクリプト {name} を開始: {run}",
    "Script menu is available only to players": "スクリプトメニューはプレイヤーのみ利用できます",
})

CATALOGS: dict[str, dict[str, str]] = {"ja": JA}
