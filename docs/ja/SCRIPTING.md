<!-- source: docs/SCRIPTING.md 4f94e42ce5e7b5be0a25aefde08fb51a5c0f53ec -->

<a id="luau-scripting-reference"></a>
# Luau スクリプトのリファレンス

スクリプトは、Bot を動かす Luau のプログラムです。各スクリプトは、それを作ったコントローラーのものです ([持ち主](#ownership-visibility-and-revisions)を参照)。各実行は、実行した人の権限を使います。このページには、スクリプトを書く人が調べたいことをすべてまとめています。

- スクリプトは初めてですか。[チュートリアル](SCRIPTING_TUTORIAL.md)から始めてください。
- スクリプトは**既定でオフ**です。有効にすること、その上限、実行をどう閉じ込めるかはオペレーター側の話です: [SCRIPTING_SETUP.md](../SCRIPTING_SETUP.md)。
- 内部の仕組み: [SCRIPTING_INTERNALS.md](../SCRIPTING_INTERNALS.md)。実際のサーバーで確認したこと: [確認状況](../COMPATIBILITY.md#scripting)。

<a id="commands"></a>
## コマンド

`/botscript` でスクリプトを管理し、実行します。`endbot.command.control` が必要です。スクリプトの作成と変更は、ロールにもよります ([ロール](../PERMISSIONS.md#roles))。

```text
/botscript list
/botscript show <name> [page]
/botscript run <name> [positional arguments...] [name=value...]
/botscript runs
/botscript stop <run-id|name|all>
/botscript delete <name>
/botscript copy <name> <new-name>
/botscript visibility <name> shared|private
/botscript owner <name> <GamerTag>
/botscript history <name>
/botscript restore <name> <n>
/botscript rename <name> <new-name>
/botscript import <name>
/botscript export <name> [overwrite]
/botscript reload
/botscript help
/botscript menu
```

| サブコマンド | すること |
| --- | --- |
| `list` | 見られるスクリプトを `name (owner, visibility)` の形で一覧にします。自分のものには `自分` と付きます |
| `show <name> [page]` | ソースを番号付きのページで表示します。長い行は折り返し、ソースを欠かすことはありません |
| `run <name> ...` | 実行を始めます。引数は[引数の渡し方](#passing-arguments)を参照 |
| `runs` | 自分の実行を一覧にします |
| `stop <run-id\|name\|all>` | 自分の実行を、id、スクリプト名、またはすべてを指定して止めます |
| `delete <name>` | 自分が持ち主のスクリプトを削除します。実行中のものは動き続けます |
| `copy`、`visibility`、`owner`、`history`、`restore`、`rename` | [持ち主、公開範囲、版](#ownership-visibility-and-revisions)を参照 |
| `import`、`export` | 手に持った本から読む、または本に書き込みます。[本](#books)を参照 |
| `reload` | オペレーターの[スクリプトファイル](#script-files)を読み直します |
| `help` | `/botscript` のヘルプを表示します |
| `menu` | [スクリプトのメニュー](#the-script-menu)を開きます |

補足:

- 開始、停止、保存、削除は、あなたの名前つきでログに残ります。各実行の終了も同じです。
- 入力したファイルシステムのパスからソースを読むことはありません。読むファイルは、オペレーターの[スクリプトファイル](#script-files)だけです。
- コマンドの入力候補は `/botgroup` と同じように働き、固定した BDS のバージョンにある抜けも同じです。

コンソールだけ:

- コンソールの `runs` は、すべてのコントローラーの実行を持ち主つきで一覧にします。
- コンソールの `stop <id|name|all>` は、そのどれでも止められます。そのため、ログアウトしたコントローラーが残した実行も、そのコントローラーの権限を取り消さずに終わらせられます。

<a id="ownership-visibility-and-revisions"></a>
### 持ち主、公開範囲、版

各スクリプトは、作ったコントローラーを持ち主として記録します。編集、削除、復元、公開範囲の変更ができるのは、持ち主かコンソールだけです。ほかの人には「スクリプト … を変更できるのは持ち主の … だけです」と返り、**コピーして編集** を案内されます。

| 公開範囲 | 見て実行できる人 |
| --- | --- |
| `private` | 持ち主だけ。ほかの人には存在しないのと同じです |
| `shared` | すべてのコントローラーが一覧表示、show、実行、コピーできます |

- 新しいスクリプトとコピーは `private` で始まります。`visibility <name> shared|private` で切り替えます。
- 0.4.0 の `share <name>` と `private <name>` という書き方は 0.6.0 で廃止されました。入力すると、`visibility` を案内して拒否されます。
- `copy <name> <new-name>` は、あなたが持ち主になる非公開のコピーを作ります。
- `rename <name> <new-name>` (持ち主だけ) は、持ち主、公開範囲、履歴を保ちます。実行中のものは古い名前のままです。
- 保存のたびに、前のソースを残します。`history <name>` は直近 10 版を新しい順に、時刻と行数つきで一覧にします。
- `restore <name> <n>` は、版 `n` を新しい保存として現在の版にします。置き換えられたソースも履歴に残ります。
- 実行は、スクリプトの持ち主がだれであっても、常に実行した人の権限を使います。

補足:

- 持ち主の仕組みより前に保存されたスクリプト (ファイルのバージョン 1) には持ち主がなく、共有のままです。最初に編集したコントローラーが持ち主になります。
- コンソールだけ: `owner <name> <GamerTag>` で、保存されたスクリプトの持ち主を付け替えられます。公開範囲、版、履歴はそのままです。
- [スクリプトファイル](#script-files)には付け替える持ち主がいません。ファイルが本体です。

<a id="the-script-menu"></a>
### スクリプトのメニュー

スクリプトが有効なとき、`/bot menu` には **+ ここに新しい Bot** の後、**グループを管理...** の隣に **スクリプト...** が並びます。その上の対象の一覧には、`/bot` で指定できる対象だけが並びます。

スクリプトの画面は、ソース全体を灰色の行番号つきで表示します。折り返した行は番号と離れたままです。とても長いスクリプトは途中で切り、`show` を案内します。ソース中の `§` は `\u{a7}` と表示するので、ソースの文字がプレビューに色を付けたり、隠したり、崩したりすることはできません。

| ボタン | 表示される人 |
| --- | --- |
| 実行 | スクリプトを見られる全員 |
| 実行を停止 | スクリプトを見られる全員 |
| コピーして編集 | 持ち主以外の全員 (持ち主は「その他」の中にあります) |
| 手に持った本に書き出す ([本](#books)) | 書き出しが有効なら全員 |
| **行を編集...** ([行のエディター](#edit-lines)) | 持ち主 |
| **その他...**: 名前を変更、操作者に共有する/非公開にする、以前の版、コピーして編集、手に持った本に書き出す、削除 | 持ち主 |

**以前の版** は、その版のソースを表示し、復元する前に確認します。**削除** は確認を求めます。

**スクリプトを作る。** 作成の画面では、次の始め方を選べます。テンプレートかコピーなら名前だけを聞かれ、保存すると新しいスクリプトが開きます。

- **グループを一列に並べる**
- **簡単な巡回**
- **指定地点で採掘** (下を参照)
- 空のスクリプト
- **手に持った本から...** ([本](#books))

**指定地点で採掘** は、`name`、`start`、`spots` (既定は 2)、`step` (既定は 4)、`blocks` (既定は 2) をこの順に受け付けます。`start` から +X 方向へ `step` の間隔でテレポートし、+Z を向いて `destroy{mode="continuous",count=blocks}` を実行し、最大 1200 tick 待って、各位置で `spot N: <reason>` をログに出します。実行する前に、各地点の前に、届く壊せるブロックを置いてください。

<a id="edit-lines"></a>
#### 行を編集

**行を編集** は、ソースを 1 行ずつボタンにして並べます。1 ページ 40 行です。

- 行を押すと、その行が入力済みのフォームが開きます。下に追加する行 (省略可) と、**この行を削除** のトグルもあります。追加した行は、自分でインデントを入れない限り、編集した行のインデントを引き継ぎます。
- **+ 先頭に行を追加** と **+ 末尾に行を追加** で、両端に行を足せます。
- 一覧の最後にある **手に持った本で全体を置き換え** で、スクリプト全体を本の内容で置き換えます。

補足:

- Windows クライアントでは、フォームの欄が**貼り付けた改行を落とします**。複数行の変更は[本](#books)で行ってください。
- 保存すると、スクリプト全体がコンパイルできることと、宣言が正しいことを確認してから、一覧の同じページに戻ります。何も変えないフォームは、そのまま戻ります。
- スクリプトが変わる前に開いたフォームは、送信すると拒否されます。
- キャンセルしても保存されません。下書きは残りません。

<a id="books"></a>
### 本

本と羽根ペンは、ゲーム内で使いやすいエディターです。改行を保ち、Windows では Ctrl+A/C/X/V が使えます。

```text
/botscript import <name>
/botscript export <name> [overwrite]
```

**複数行の編集の流れ:**

1. テンプレート (または空) からスクリプトを作り、開きます。
2. **手に持った本に書き出す** (`export`) を押します。
3. ページを編集します。
4. **手に持った本で全体を置き換え** (`import`) で、新しい版を保存します。

本は編集の間もソース全体を保つので、書き出しは 1 回で足ります。

**取り込み (import)** は、メインハンドに持った本と羽根ペン、または記入済みの本を読みます。本は読むだけです。

- ページは改行でつなぎます。ただし、すでに改行で終わっているページはそのままつなぎます。末尾の空白ページは無視します。
- 新しい名前なら、非公開のスクリプトを作ります。既存の名前なら、メニューと同じコンパイル、宣言、持ち主の確認を通して、新しい版を保存します。
- メニューの **手に持った本から...** は、すでにある名前を拒否します。
- メニューの **手に持った本で全体を置き換え** は、まず本のソースを両方の行数とともに表示します。**置き換える** を押したときだけ、そして本がまだその内容のままの場合だけ、スクリプトを置き換えます。
- 入力した `import` で既存の名前を指定すると、すぐに保存します。前のソースは以前の版に残ります。

**書き出し (export)** は、実行できるスクリプトを、メインハンドの本と羽根ペンに書き込みます。ほかの人の共有スクリプトを編集するには、新しい名前で取り込み直してください。

- 空の本には、すぐに書き込みます。
- すでに書いてある本を上書きするのは `export <name> overwrite` だけです。ただの `export` は拒否します。
- メニューでは、先に本の今の内容とページ数を表示します。**上書きする** は `export <name> overwrite` を実行し、本がまだそのページのままの場合だけ書き込みます。
- 記入済みの (署名した) 本は決して変えません。アイテムを作ることもありません。

上限:

- ページには行をまるごと入れます。1 ページ 256 文字、50 ページまでです。それより長い行やスクリプトは拒否され、問題の行が示されます。この上限は、あえて控えめにしています。
- クライアント自身の本のエディターは、ページの容量を超えた文字を何も言わずに落とします。長いソースは手でページに分けてください。
- 書き出しは既定でオンです。持っているアイテムをプラグインに一切変えさせたくないオペレーターは、`[scripting] book-export = false` を設定します。

<a id="optional-ui-pack-field"></a>
### 任意の UI パックの入力欄

任意の [Endbot UI リソースパック](UI_PACK.md)をグローバルリソースとして追加すると、プレイヤーは大きなフォームのウィンドウ、コンパクトなボタン、複数行の入力欄を使えます。

`[scripting] ui-pack-editor = true` (既定は false) のとき:

- 行のエディターに **全体をまとめて編集 (UIパック)...** も表示されます。ソース全体を 1 つの欄で編集でき、ほかの編集と同じように保存します。
- 新しいスクリプトの画面も、同じ欄にテンプレートを表示します。そのため、スクリプトを作る前に全体を書けます。

パックがないとき:

- クライアントが改行を落とし、その返信は保存せずに拒否します。新しいスクリプトの画面では、代わりに **表示どおりに保存** のトグルで、テンプレートをそのまま保存できます。
- フォームの入力は約 100 文字で止まります。それより長い行は、パックを使うか、スクリプトファイルか、本で編集してください。
- パックの確認がまだのクライアントでは、複数行の編集に[本](#books)を使ってください。

<a id="script-files"></a>
### スクリプトファイル

PC のエディターでスクリプトを書くには、オペレーターが `<name>.luau` ファイルを `<instance>/scripts/` に直接置きます。`endbot.toml` と同じ場所にあるフォルダーです。セットアップ時と起動時に作られます。

ファイルは、サーバーの起動時と、`/botscript reload` (コンソール、どのコントローラーでも、またはスクリプト一覧の **スクリプトファイルを再読み込み**) で読まれます。フォルダーを監視する仕組みはありません。すでに動いている実行は、始めたときのソースのままです。

- ファイルのスクリプトはすべてのコントローラーに**共有**され、ゲーム内では**読み取り専用**です。実行、実行を停止、show、コピーして編集、export は使えます。編集、名前の変更、削除、共有は、ファイルを案内して拒否します。
- 一覧では、持ち主を `ファイル` と表示します。コピーすると、あなたが持ち主の普通の保存済みスクリプトになります。
- 版の履歴はありません。必要なら、フォルダーを自分でバージョン管理してください。

読み込まれるファイル:

- 読み込むのは、正しいスクリプト名を持ち、`max-script-bytes` 以下の普通の `.luau` ファイル (UTF-8、CRLF も可) だけです。リンク、ほかの拡張子、サブフォルダーは無視されるか拒否されます。読み込むファイルは最大 `max-scripts` 個です。
- 同じ名前の保存済みスクリプトがあれば、ほかの人の非公開のものでも、そちらが優先されます (名前はサーバー全体で共通です)。ファイルは「読み込みませんでした。同じ名前の保存済みスクリプト … があります」として飛ばされます。コンソールと起動時のログは、そのスクリプトの持ち主も示します。

`reload` が確認すること:

- `reload` は各ファイルの宣言を確認し、飛ばしたファイルをすべて理由つきで返します。起動時はファイルを読むだけで、飛ばしたものはサーバーコンソールにログを出します。
- ファイルが外されるのは、そのファイル自身の問題のときだけです。宣言のエラーか、`params` の前で止まってしまう場合です。
- 確認を実行できないとき (あなたの実行枠が埋まっている、runner がない) は、ファイルを読み込んだままにし、確認していないことを返事で伝えます。
- `params` の前で止まるファイルは、確認に約 5 秒かかります。確認の間、`/bot` のコマンドは待たされます。

<a id="names-and-storage"></a>
### 名前と保存

- 名前は大文字と小文字を区別せず、グループと同じ規則に従います。ASCII の英字、数字、アンダースコアで 1-16 文字、先頭は英字かアンダースコアです。
- 保存に失敗しても、前の版はそのまま残ります。
- 再起動後に、実行を再開したりやり直したりすることはありません。

<a id="writing-a-script"></a>
## スクリプトを書く

<a id="parameters"></a>
### 引数

どのスクリプトも、Bot、log、wait の呼び出しより前に、`params` を 1 回呼びます。引数がなくても呼びます (`params {}`)。

```lua
local a = params {
    param.group "group",
    param.pos("origin", "~ ~ ~"),
    param.number("spacing", 2),
}
for i, member in group(a.group):members() do
    local result = member:tp(a.origin + vector.create((i - 1) * a.spacing, 0, 0))
    assert(result.ok, result.reason)
end
```

| コンストラクター | 補足 |
| --- | --- |
| `param.group(name[, default])` | グループ。上の `group(a.group)` のように使います |
| `param.bot(name[, default])` | Bot |
| `param.pos(name[, default])` | ベクトルとして届きます ([引数の渡し方](#passing-arguments)) |
| `param.number(name[, default])` | 有限の数値 |
| `param.bool(name[, default])` | `true` か `false` |
| `param.string(name[, default])` | テキスト |
| `param.choice(name, {"a", "b", ...}[, default])` | 1-64 文字の異なる文字列を 1-32 個。実行フォームではドロップダウンになります |
| `param.event(name)` | [トリガー](#triggers)の実行ではその発生、手動の実行では nil |

各宣言は名前を持ち、値は `a.<name>` で読みます。

規則:

- 宣言は連続した配列で渡してください。名前付きキー、混在、歯抜けのテーブルは拒否されます。重複した名前、不明な型、正しくない既定値も拒否されます。
- 名前は `[A-Za-z_][A-Za-z0-9_]{0,31}` に一致する必要があり、引数は最大 32 個です。
- スクリプトは、`params` まで実行して確認します。`params` より前のコードには通常の予算がありますが、Bot、log、wait の関数は呼べません。
- 宣言のエラーは、可能なら `params` を呼んだスクリプトの行を示します。日本語のプレイヤーには日本語で表示します。

<a id="capabilities"></a>
### capability

基本の組を超えるものが必要なスクリプトは、`requires` で宣言します。呼ぶのは 1 回だけで、`params` より前です。

```lua
requires { "observe.chat" }
params {}
```

| capability | `requires` が必要か | 対象 |
| --- | --- | --- |
| `act.bot`、`read.world`、`wait`、`log` | 不要 (書いても何も変わりません) | Bot の操作、ワールドの読み取り、待機、ログ |
| `observe.presence` | 必要 | `player.join` と `player.leave` の[イベントの種類](#event-kinds) |
| `observe.chat` | 必要 | `player.chat` のイベントの種類 |

補足:

- それ以外の名前は、その名前を示すエラーになります。2 回目の呼び出し、`params` の後の呼び出し、異なる文字列のリスト以外を渡すことは、宣言のエラーです。順序は関係ありません。
- 実行フォームは、1 行目に宣言を表示します。
- 確認済みの宣言から始まった実行 (実行フォーム、引数のないメニューからの開始、トリガー) は、その宣言に縛られます。実行中の `requires {}` の結果がそれと違えば、最初の操作の前に止まります。そのため、条件付きの呼び出しで、表示した範囲を広げることはできません。
- スクリプトが capability を宣言する理由: [PERMISSIONS.md](../PERMISSIONS.md#two-layers-the-person-and-the-script)。

<a id="passing-arguments"></a>
### 引数の渡し方

位置引数とメニューの入力欄は、宣言の順に従います。位置引数の値の後に `name=value` で、宣言したどの引数でも指定できます。そのため、前の既定値を飛ばせます。

```text
/botscript run row #miners
/botscript run row #miners "~ ~ ~" 3
/botscript run row #miners spacing=3
/botscript run row #miners origin="0 70 0"
```

| 型 | 受け付けるもの |
| --- | --- |
| グループ、Bot | 存在するグループか Bot |
| 位置 | `x y z`。空白を含むときは引用符で囲みます。`~` は送信したときのあなたの位置から決まります。コンソールの位置は絶対座標でなければなりません |
| 数値 | 有限の数値 |
| 真偽値 | `true` か `false` |
| choice | その選択肢のどれか |
| 文字列 | テキスト。空白を含むときは引用符で囲みます |

規則:

- 名前付きの値の後に位置引数の値は置けません。1 つの引数は 1 回しか指定できません。
- `word` が宣言した引数でない `word=...` は、普通の位置引数の値のままです。
- 実行フォームも同じ順です。グループ、Bot、choice のドロップダウン、あなたの位置を初期値にした位置の欄、数値とテキストの入力、宣言した既定値を入れたトグルを並べます。
- フォームで選んだ Bot やグループ、または Bot の引数として渡したものは、その Bot やグループそのものに結びついたままです。それが削除されたり、削除されて同じ名前で作り直されたりすると、フォームやその後の呼び出しは拒否されます。

位置は Luau の**ベクトル**として届きます。`a.origin.x` を読んだり、足したり、倍にしたり (`a.origin + vector.create(2, 0, 0) * i`) できます。操作が `x, y, z` を取るところには、どこでも 1 つで渡せます (`bot:tp(a.origin)`)。

<a id="results-and-codes"></a>
### 結果と code

操作の呼び出しが返すのは、完了ではなく**受け付け**です。

```lua
local r = bot("Alice"):jump()
-- {ok=true, code="ok", messages=..., data=...}
-- {ok=false, code=..., reason=..., messages=...}
assert(r.ok, r.reason)
```

| `code` | 意味 |
| --- | --- |
| `ok` | 受け付けた |
| `unauthorized` | 権限がない |
| `not_found` | その Bot や対象がない |
| `invalid_argument` | 要求を直す |
| `unavailable` | 後でもう一度試す |
| `conflict` | 今の Bot の状態ではできない。たとえばオフライン、名前が使用中 |
| `not_a_target`、`out_of_reach`、`blocked`、`unbreakable` | Bot がそのブロックを狙えない。[ブロックの狙い方](#targeting-blocks)を参照 |

補足:

- 分岐は `code` で行ってください。`code` はどの言語でも同じです。`reason` は人が読むための文言です。
- 拒否された呼び出しは、たとえば `assert(r.ok, r.reason)` のように明示的に扱ってください。
- グループの操作は、`data` に件数を含みます。一部が失敗すると `ok=false` で、グループの `code` は最初に失敗したメンバーのものです。クエリの各行にも、そのメンバーの `code` が入ります。
- 権限の取り消し、頻度や期限のエラー、プロトコルのエラーは、値を返さずに実行を終わらせます。
- コマンド文字列の API はありません。スクリプトから、入力するコマンドを実行することはできません。

<a id="logging-and-waiting"></a>
### ログと待機

```lua
wait(20)                       -- 20 tick = 実時間の 1 秒
waitOnline(bot("Alice"))       -- 名前やグループのハンドルも受け付ける
log("done")
```

- `wait(ticks)` は実行を一時停止します。20 tick が実時間の 1 秒です。待機中も権限を確認し直します。
- `waitOnline(...)` は、Bot (またはグループの全メンバー) がオンラインになるまで待ちます。タイムアウトは[ジョブと待機](#jobs-and-waits)を参照してください。
- `log(text)` は、実行 id を付けた 1 行を、サーバーのログと、実行を始めたプレイヤーのチャットに書きます。

`log` の上限:

- チャットへは 1 実行あたり 5 秒に 10 行までで、各行は書式コードを除いて 256 文字で切ります。出さなかった行は、次のチャットの行か終了メッセージで数を伝えます。
- サーバーのログにはすべての行が残ります。1 実行あたり 2000 行か 512 KiB までです。
- コンソールからの実行は、サーバーのログだけに出します。
- `[scripting] chat-logs = false` (プラグインの `config.toml`、管理されたインスタンスでは `endbot.toml`) にすると、ログはサーバーのログだけに残ります。

<a id="operation-replies-in-chat"></a>
### チャットに出る操作の返事

スクリプトが出す操作は、どれも普通の `/bot` コマンドでもあります。そのため、その返事は入力したときと同じくあなたのチャットに届くはずです。`[scripting] replies` (プラグインの `config.toml`、管理されたインスタンスでは `endbot.toml`) で、何を出すかを選びます:

| 値 | チャットに出るもの |
| --- | --- |
| `all` | 入力したコマンドと同じく、すべての操作 |
| `failures` (既定) | 拒否された操作だけ。受け付けた操作も、コンソールのログ行は残ります |
| `none` | 何も出しません |

補足:

- `failures` では、`not_a_target` で拒否された `destroy` は出しません。スクリプトが読んだ後に、対象が消えることがあるからです ([ブロックの狙い方](#targeting-blocks))。スクリプトはそれでも拒否を受け取ります。
- 実行の開始と終了の行、`log()`、採掘ループの開始と停止の通知は影響を受けません。
- スクリプトから方針は変えられません。

<a id="structured-status-and-mining-waits"></a>
### 構造化された状態と採掘の待機

`bot:status()` は、いつもの結果を返します。下の `data` のフィールドを読んでください。

```lua
local s = bot("Alice"):status().data
if s.mining.active then log("still mining " .. s.mining.blocksBroken) end
```

| フィールド | 意味 |
| --- | --- |
| `data.name` | 今の Bot の名前 |
| `data.identityId` | Bot の UUID |
| `data.connectionState` | runtime の接続状態。`online` は接続済み |
| `data.vehicle` | 乗っている間は `{type, runtimeId}`、それ以外は nil |
| `data.position` | 足の位置 `{x, y, z}` |
| `data.dimension` | `Overworld`、`Nether`、`TheEnd` のどれか |
| `data.look` | 度で表した `{yaw, pitch}`。yaw は 0-360 で、0 が南 (+Z)、90 が西 (-X)。pitch は -90 (上) から 90 (下)。`look` と同じ表し方 |
| `data.health`, `data.maxHealth` | BDS の体力 |
| `data.hunger`, `data.saturation` | 満腹度 (0-20) と隠し満腹度 |
| `data.onGround`, `data.inWater` | 真偽値 |
| `data.selectedSlot` | 選んでいるホットバーのスロット、1-9 |
| `data.held` | 選んでいるアイテムの `{name, count}`。空なら nil |
| `data.mining.active` | 真偽値: ブロックを壊すジョブか連続採掘ループが動いている (対象を待っている間も含む) |
| `data.mining.mode` | `once` か `continuous`。採掘ジョブがまだなければ nil |
| `data.mining.state` | runtime のジョブの状態 (`active`、`waiting`、`completed`、`canceled`、`failed`)。ジョブがなければ nil |
| `data.mining.blocksBroken` | runtime の連続採掘ループの数。1 ブロックの場合は、完了なら 1、それ以外は 0。ジョブがなければ 0 |
| `data.mining.blockCount` | 連続採掘の数の上限。無制限かジョブがなければ nil |
| `data.mining.reason` | 動いている間は nil。それ以外は runtime の終了理由。ジョブがなければ nil |
| `data.mining.target` | `state` が `active` の間は壊しているブロックの `{x, y, z, type}`、それ以外は nil |
| `data.running` | 動いている入力ジョブの id を種類別に (`move`、`jump`、`attack`、`use`、`interact`)。何も動いていなければ nil |
| `data.ended` | 入力の種類ごとの最後の終了: `{job, reason}` |
| `data.teleports` | runtime の起動後に Bot が確認応答した、サーバーのテレポートの数 |

補足:

- 位置、向き、体力、インベントリは、呼び出した時点で BDS から読みます。満腹度と隠し満腹度は、BDS が最後に Bot に送った値です。
- Bot がワールドにいないとき、または BDS がまだ送っていないとき、フィールドは nil です。
- 採掘の値は、最後に終わったものも含めて、Bot の実際の採掘ジョブを伝えます。経過時間から完了を推測することはありません。
- 終了理由には、`block count reached`、`duration elapsed`、`stopped`、runtime の失敗理由があります。
- `group:status()` は、メンバー順で `data.results` を返します。各行には `index`、`identityId`、`name`、`ok`、`reason` (成功時は nil)、そしてそのメンバーの上のフィールドを入れた `data` (クエリが失敗したら nil) があります。クエリが一部失敗すると、外側が `ok=false` になります。
- 採掘を待つには、[`waitMining`](#jobs-and-waits) か `destroy` のジョブの `wait` を使ってください。

<a id="other-queries"></a>
### ほかのクエリ

| 呼び出し | `data` |
| --- | --- |
| `bot:inventory()` | `slots`: 空でないスロットを `{slot, name, count}` で並べます。スロットは `"1"`-`"36"`、`offhand`、`head`、`chest`、`legs`、`feet` |
| `bot:hotbar()` | `selectedSlot` とホットバーの `slots` |
| `bot:entities()` | `entities`: 近くのプレイヤー以外のエンティティを近い順に最大 10 件、`{runtimeId, type, distance, position, health, maxHealth, nameTag}` で並べます。Mob でなければ `health` は nil |
| `bot:entity("@cow")` | `entity`: 16 ブロック以内のそうした要素 1 つ。`"@9"`、`"@nearest"`、引数なしも使えます |

- インベントリ、ホットバー、`held` のアイテムのうち、道具や防具のように耐久値のあるものには `durability = {remaining, max}` が付きます。
- エンティティの `selector` (`"@9"`) を `:interact(e.selector)` や `:attack(e.selector)` に渡してください。エンティティには常に `@` を付けます。
- 繰り返しは、最後のオプションテーブルで指定します: `bot:interact("@cow", {count=5})`、`bot:attack("@zombie", {interval=20, ticks=200})`、`bot:destroy("oak_log", {count=10})`。

<a id="world-queries"></a>
### ワールドのクエリ

ワールドのクエリは BDS を読み、頻度制限では操作として数えます。

| 呼び出し | `data` |
| --- | --- |
| `bot:target()` | `kind` (`"block"`、`"entity"`、nil)、`distance`、そして `block = {x, y, z, face, type}` か `entity = {runtimeId, type}`。Bot の目から 6 ブロック以内で、近いほうに当たったもの。プレイヤーは無視します |
| `bot:block(x, y, z)` | `{x, y, z, type, states, reachable}`。届かないときは `reachCode` と、`blocked` なら `obstruction` も加わります。`"~"`/`"~-1"` の文字列は Bot からの相対です。読み込まれている位置ならどこでも使えます |
| `bot:blocks("oak_log", {limit=10})` | `{type, radius, blocks}`。`blocks` は `{x, y, z, distance, reachable}` を Bot に近い順に並べます。対象は Bot の周りの半径 8 の球の中で、プレイヤーに見えるブロックだけです (透視はしません。下を参照)。limit は 1-64 |
| `bot:blocks("oak_log", {area=area.sphere(5, x, y, z)})` | `{type, area, blocks}`: 同じクエリを[範囲](#areas)で絞ったもの。たとえば木のてっぺんの周り。Bot から 16 ブロックより先は探しません |

`blocks(type, x, y, z)` と `{radius = r}` は廃止されました。呼ぶと、`blocks(type, {area = area.sphere(r, x, y, z)})` を示すメッセージで失敗します。

<a id="areas"></a>
### 範囲

範囲は、`blocks`、種類を指定した `destroy`、`destroy` の連続採掘ループ、種類を指定した `interact` が選べるブロックを絞り込みます。`area` で作り、`{area = a}` として渡します。

| Luau | チャット | 形 |
| --- | --- | --- |
| `area.cell(x, y, z)` または `area.cell(v)` | `within <x> <y> <z>` | 1 ブロック |
| `area.box(x1, y1, z1, x2, y2, z2)` または `area.box(v1, v2)` | `within <x1> <y1> <z1> <x2> <y2> <z2>` | 直方体。`/fill` と同じく両方の角を含む |
| `area.sphere(r)` | `within <r>` | Bot を中心とする半径 `r` の球 |
| `area.sphere(r, x, y, z)` または `area.sphere(r, v)` | `within <r> of <x> <y> <z>` | ある地点を中心とする半径 `r` の球 |
| `a:minus(b)` | `except <b>` | `a` から `b` のブロックを除いたもの。`b` は上のどの形でもよい |

```lua
local footing = area.cell("~", "~-1", "~")
local job = bot:destroy("acacia_log", {count = 20, area = area.sphere(6):minus(footing)}).job
job:wait{}
```

- 座標は、数値、ベクトル、または Bot からの相対の `"~"`/`"~-1"` の文字列です。
- `:minus` は自由につなげられます (`area.sphere(6):minus(footing):minus(trunk)`)。チャットでは `except` は 1 つです。
- 範囲を作れるのはこれらの関数だけです。テーブルや文字列は拒否されます。
- Bot の足場は特別扱いしないので、例のように明示的に除いてください。

規則は[コマンドの規則](COMMANDS.md#areas-within-and-except)と同じです:

- 範囲は絞るだけで、届く距離や視線を広げることはありません。
- `~` は操作を受け付けたときに決まります。そのため、連続採掘ループは Bot が動いても範囲を保ちます。
- `within` は最大で半径 16 の球までです。
- 範囲がなければ、各操作は既定のままです。

`interact` は、ブロックの種類と `@entity` セレクターの両方を 1 つの `target` 引数で受けるので、`{area = a}` はそのシグネチャの一部です。範囲が効くのは、対象がブロックの種類のときだけです (`interact("chest", {area = ...})`)。`@entity` の対象では、呼び出しは「範囲を指定できるのはブロックの種類 …」として拒否されます。

<a id="targeting-blocks"></a>
### ブロックの狙い方

ブロックについての 3 つの問いには、それぞれ 1 つの答えがあります。

| 問い | 規則 | 使うもの |
| --- | --- | --- |
| プレイヤーに見えるか | 面の 1 つが、光を通すブロックに接している: 空気、液体、植物、葉、ガラス、氷、そしてハーフブロック、階段、フェンス、塀、板ガラス、ドアのような部分的な形 | `blocks` が並べるブロック |
| Bot が今狙えるか | 空気でも液体でもなく、一部が Bot の目から 6 ブロック以内にあり、Bot に向いた面まで、当たり判定のないブロック (空気、液体、草、花、松明、レール、雪の層) だけを通る直線が届く。葉、ガラス、部分的な形は直線を止める | `reachable`、`destroy x y z`、`destroy <type>`、`interact x y z`、`interact <type>` |
| 照準の先に何があるか | Bot の視線に沿って 6 ブロック以内で最初の、空気でも液体でもないブロック。プレイヤーと同じく、草や松明には当たり、ライトブロックには当たらない | `target`、および対象なしの `destroy` と `interact` |

そのため、背の高い草の後ろの原木は、名前や座標なら届きますが、照準はその手前の草に当たります。葉に包まれた原木は `blocks` には出ますが、葉を 1 つ壊すまでは届きません。

ブロックを狙えないとき、`block` は `reachable = false` と `reachCode` を返し、`destroy`/`interact` は同じ値を `code` として失敗します:

| コード | 意味 |
| --- | --- |
| `not_a_target` | 空気か液体、または照準の先に何もない |
| `out_of_reach` | どの部分も Bot の目から 6 ブロック以内にない |
| `blocked` | 何かがすべての直線をさえぎっている。見つかったときは、`obstruction = {x, y, z, type}` (拒否された操作では `data` の中) が、さえぎっている最初のブロックを示す |
| `unbreakable` | `destroy` のみ: 岩盤、バリアなど、サバイバルで壊せないブロック。狙うことはできる |

```lua
local info = bot:block(x, y, z).data
if not info.reachable and info.reachCode == "blocked" and info.obstruction then
  local o = info.obstruction
  bot:destroy(o.x, o.y, o.z)  -- 先に葉を壊す
end
```

補足:

- `block` と `blocks` は読み取りで、予約ではありません。`reachable` は読んだ瞬間のことです。`destroy` と `interact` は、実行するときに対象をもう一度確認します。
- その間にブロックが消えることがあります。Bot 自身の前のひと振りがまだ処理中だった、葉が自然に消えた、別の Bot やプレイヤーが壊した、などです。そのとき、操作は `not_a_target` で失敗します。
- ブロックがなくなればよいだけのスクリプトは、`destroy` の `not_a_target` を完了として扱えます。

<a id="jobs-and-waits"></a>
### ジョブと待機

続く操作の結果は、その**ジョブ**を持っています。ジョブとは、操作が返った後も Bot がし続けることです。`job:wait` で待ちます。

```lua
local r = bot("Alice"):move("forward", {ticks = 40})
local w = r.job:wait{timeout = 200}   -- {ok, code, reason, results}
if w.ok then log("stopped: " .. w.reason) end  -- duration elapsed, count reached, completed, stopped, replaced, ...
group("#miners"):destroy("stone", {count = 64}).job:wait{timeout = 2400}
```

- `move`、`jump`、`attack`、`use`、`interact` (続くモードならどれでも。`hold` 付きの `use` once も含みます)、`destroy`、`tp` がジョブを返します。グループでは、ジョブを始めたすべてのメンバーを 1 つのジョブがまとめます。
- `tp` のジョブは、Bot がサーバーのテレポートを確認応答すると `arrived` で終わります。
- `destroy` のジョブは、`waitMining` と同じように待ちます。

**どの待機も** `{timeout = ticks}` (既定は 1200) を取り、`ok`、`code`、`reason` を返します。Bot とグループの待機では、さらにメンバーごとに `name`、`identityId`、`ok`、`code`、`reason` を持つ 1 行を返します。

| 結果 | 返る値 |
| --- | --- |
| どんな理由であれ、ジョブが終わった | その理由とともに `ok=true` (`stopped`、同じ種類の新しいコマンドで置き換えられた `replaced`、`died`、`disconnected`、`held item changed`、...) |
| タイムアウト | `ok=false, code="timeout"`。ジョブは動き続けます |
| メンバーがオフライン | `ok=false, code="conflict", reason="offline"` |
| メンバーがもう動いておらず、その終了が同じ種類の後のジョブで上書きされた | `ended` |

**`waitOnline`。** `bot:waitOnline{timeout=...}` (グループにもあります) は、対象がオンラインになるまで待ち、タイムアウトでは `ok=false, code="timeout"` を返します。`timeout` がなければ、`waitOnline(b)` と同じく待ちます。

**`waitMining`。** `bot:waitMining{timeout=1200}` と `group:waitMining{timeout=1200}` は、破壊と採掘のジョブだけを待ちます。移動などほかの続く入力は、この待機を遅らせません。

- `{ok, code, reason, results={...}}` を返します。各結果には `name`、`identityId`、`ok`、`code`、`reason`、`mining` ([状態のフィールド](#structured-status-and-mining-waits)) があります。
- 動いていないオンラインのメンバーは、その終了理由を返します。ジョブがなければ `idle` です。状態が `failed` でなければ `ok=true` です。
- オフラインのメンバーは `ok=false, reason="offline"` を返します。タイムアウトの時点でまだ動いているメンバーは `ok=false, reason="timeout"` を返します。タイムアウトしても、採掘ジョブは止まりません。
- クエリの失敗は、そのクエリの理由とともに `ok=false` を返します。
- 外側の `ok` は、全メンバーが成功したときだけ成功になります。外側の `reason` は、共通の理由、成功の理由がまちまちなら `completed`、そうでなければ最初に失敗したメンバーの理由です。メンバーごとの結果には、まちまちの結果がそのまま残ります。

`waitMining` の補足:

- タイムアウトの既定は **1200 tick (実時間で 60 秒)** で、0 から 2147483647 の整数でなければなりません。0 は状態の確認を 1 回だけ行います。
- 停止、権限の取り消し、シャットダウン、または任意で設定する正の実行期限は、待機を成功として返すのではなく、実行を終わらせます。
- グループの待機は、今のメンバー全員が対象です。メンバーと各メンバーの同一性は、最初の確認で固定します。メンバーが入れ替わると、実行は終わります。直接書いた Bot の同一性も、最初の確認で固定します。
- 確認は `max(0.25, 1 / operations-per-second)` 秒 (実時間) ごとに行い、最後の確認は残りのタイムアウトまで短くします。タイムアウトは、最後の確認の値を使います。
- 確認のたびに操作の頻度制限に数え、権限と停止を確認し直します。
- `waitOnline(b)` と `b:waitMining{}` は 1.0 まで別名として残るので、今あるスクリプトはそのまま動きます。

<a id="generated-api-reference"></a>
## 生成された API リファレンス

この表はすべてのメソッドの一覧で、`/bot` と同じ操作のレジストリから生成しています。値の範囲やオプションの組み合わせは、入力したコマンドとまったく同じように確認します。メソッドは `bot("Alice")` と `group("#miners")` で使えます。グループ全体の rename と forget には、1 つの Bot だけという制限が残ります。

<!-- BEGIN GENERATED OPERATIONS -->
| メソッド | 位置のフィールド | 最後のオプションテーブル |
| --- | --- | --- |
| `:status()` | なし | なし |
| `:inventory()` | なし | なし |
| `:entities()` | なし | なし |
| `:entity()` | なし | なし |
| `:entity()` | target: string | なし |
| `:target()` | なし | なし |
| `:block()` | x: coordinate, y: coordinate, z: coordinate | なし |
| `:blocks()` | type: string | limit: integer, area: area |
| `:resume()` | なし | なし |
| `:reconnect()` | なし | なし |
| `:despawn()` | なし | なし |
| `:forget()` | なし | なし |
| `:visibility()` | visibility: shared / private | なし |
| `:stop()` | なし | なし |
| `:dismount()` | なし | なし |
| `:menu()` | なし | なし |
| `:spawn()` | なし | なし |
| `:spawn()` | x: coordinate, y: coordinate, z: coordinate | yaw: number, pitch: number, dimension: string |
| `:rename()` | name: string | なし |
| `:owner()` | gamertag: string | なし |
| `:tp()` | target: player | なし |
| `:tp()` | x: coordinate, y: coordinate, z: coordinate | yaw: number, pitch: number, dimension: string, facing: player, facingX: coordinate, facingY: coordinate, facingZ: coordinate |
| `:move()` | direction: forward / backward / left / right / stop | ticks: integer |
| `:look()` | yaw: number, pitch: number | なし |
| `:look()` | x: coordinate, y: coordinate, z: coordinate | なし |
| `:swap()` | a: integer, b: integer | なし |
| `:hold()` | item: string | なし |
| `:hotbar()` | なし | なし |
| `:hotbar()` | slot: integer | なし |
| `:radius()` | なし | なし |
| `:radius()` | value: integerOrDefault | なし |
| `:interact()` | なし | なし |
| `:interact()` | x: coordinate, y: coordinate, z: coordinate | なし |
| `:interact()` | x: coordinate, y: coordinate, z: coordinate, face: down / up / north / south / west / east | なし |
| `:interact()` | target: string | mode: once / continuous / stop, interval: integer, ticks: integer, count: integer, area: area |
| `:destroy()` | なし | mode: once / continuous / stop, ticks: integer, count: integer, area: area |
| `:destroy()` | x: coordinate, y: coordinate, z: coordinate | face: down / up / north / south / west / east |
| `:destroy()` | type: string | mode: continuous, ticks: integer, count: integer, area: area |
| `:drop()` | なし | なし |
| `:drop()` | selector: stringOrInteger | count: integer |
| `:jump()` | なし | mode: once / continuous / stop, interval: integer, ticks: integer |
| `:attack()` | なし | mode: once / continuous / stop, interval: integer, ticks: integer |
| `:attack()` | target: string | mode: once / continuous / stop, interval: integer, ticks: integer, count: integer |
| `:use()` | なし | mode: once / continuous / stop, interval: integer, hold: integer, ticks: integer |
| `:sprint()` | value: boolean | なし |
| `:sneak()` | value: boolean | なし |
| `:list()` | なし | なし |
| `:ping()` | なし | なし |
| `:help()` | なし | なし |
| `:help()` | topic: stringOrInteger | なし |
<!-- END GENERATED OPERATIONS -->

名前付きのオプションは、最後のテーブルで渡します: `:move("forward", {ticks=20})`、`:destroy{mode="continuous",count=64}`、`:drop("all")`、`:use{interval=40,hold=20,ticks=120}`。

オプションと入力するコマンドの対応:

- `ticks` は `for` に対応します。アクションの `interval` は `mode` の代わりになります。
- テレポートのオプションは、`yaw,pitch` を組で使うか、プレイヤーには `facing` を使うか、`facingX,facingY,facingZ` をすべて使います。`dimension` は先頭の `in` 句になります。
- 引数 3 つの `look(x, y, z)` は `look at` で、コマンドと同じく Bot からの相対です。
- `sprint` と `sneak` は真偽値を取ります。

補足:

- `group:members()` は、決まったメンバー順で、番号付きのメンバーのハンドルを返します。
- ルートの `list`、`ping`、`help` は、ルートの意味のままです。
- スクリプトの `menu()` は、UI を開かずに受け付けを返します。
- `look` (両方の形) は、BDS が新しい向きを報告してから返り、観測した `{yaw, pitch}` を `data.look` に入れます。そのため、直後の照準を使う操作は新しい視点を使います。BDS が約 0.5 秒 (20 TPS で 10 tick) 以内に報告しなければ、結果は `code="timeout"` になります。

<a id="event-kinds"></a>
## イベントの種類

スクリプトが監視できるイベントの種類は、下のとおりです。発生ごとに実行を始めるトリガーも、同じ種類とフィルターを使います。

<a id="watches"></a>
### 監視

監視は、実行のために 1 つのイベントの種類の発生を集めます。`w:wait` は次の 1 件を返します。

```lua
params {}
local w = watch("block.change", {area = area.cell(100, 64, 200), to = "air"})
local r = w:wait{timeout = 1200}   -- {ok, code, reason, data, missed}
if r.ok then log(r.data.from .. " became " .. r.data.to .. " (" .. r.data.cause .. ")") end
w:close()                          -- 省略可。監視は実行とともに終わる
```

```lua
local w = watch("schedule", {every = "1m"})        -- endbotd のタイマーが発動するたび
local n = watch("world.time", {phase = "night"})   -- 夜になるたび
```

種類ごとのフィルター:

| 種類 | フィルター |
| --- | --- |
| `block.change` | `area` (必須)、`dimension` (既定は `overworld`)、`from`、`to` |
| `schedule` | `every` (整数と `s`、`m`、`h` のどれか。`10s` から `24h`)、`at` (endbotd のローカル時刻での `HH:MM`。1 日 1 回)、またはその両方 |
| `world.time` | 省略可能な `phase`: `day`、`sunset`、`night`、`sunrise` のどれか |
| `player.join`、`player.leave`、`player.chat`、`bot.*`、`job.finished`、`server.started` | なし |

- `watch(kind, filter)` は、返す前に、種類、すべてのフィルターの名前と値、必須のフィルター、種類の capability を `requires {}` と照らし合わせて確認します。間違いがあると、そのフィールドを示すメッセージで実行が止まります。
- ブロック名の `minecraft:` は省略できます。
- `block.change` は、`area.cell`、`area.box`、`area.sphere` で作った `area` (`:minus` は 16 個まで) を取り、大きさは 512 ブロックまでです。監視には基準になる Bot がないので、`~` と Bot を中心とする球は拒否されます。Bot の `status()` の座標など、位置から範囲を作ってください。

待機:

- バッファーは `watch()` の時点で始まるので、待機と待機の間に取りこぼすことはありません。監視は、新しいほうから 64 件の発生を保持します。`missed` は、前回成功した待機から後に捨てた数です。
- `w:wait{timeout = ticks}` はほかの待機と同じです。既定のタイムアウトは 1200 tick で、タイムアウトは `ok = false, code = "timeout"` です。
- 待機は、発生が届けばすぐに答えます。操作の予算は使いません。
- 発生元 (BDS かリンク) が再起動したり、発生を捨てたりした後は、次の待機が一度だけ理由つきで `ok = false, code = "unavailable"` を返し、その後は待機を続けます。そうした空白の間の変化は、後から届けません。空白を許せないスクリプトは、`block()` で読み直してください。

`data` の中身:

| 種類 | `data` |
| --- | --- |
| `block.change` | `x`、`y`、`z`、`dimension`、`from`、`to`、`cause` (`player-break`、`player-place`、`poll` のどれか)、そして Bot がしたときは `bot`。人間のプレイヤーの名前は決して渡しません |
| `schedule` | `at`: 発動した tick のローカル時刻 |
| `world.time` | `phase` と `time` (その日のレベル時刻、0-23999)。昼夜の周期が別の段階に入ったときに発動します |

プレイヤーとチャット:

- `player.join`、`player.leave`、`player.chat` は人間のプレイヤーだけを報告します (Bot には `bot.*` の種類があります)。`requires { "observe.presence" }` か `requires { "observe.chat" }` が必要です。実行フォームとトリガーのフォームは、宣言を最初に表示します。
- `player.chat` は公開チャットだけです。`/tell`、`/msg`、`/w` は決して報告しません。メッセージは 256 文字で切ります。

上限 (上限を超える監視は、その上限を示して拒否されます):

| 上限 | 既定 |
| --- | --- |
| 1 実行あたりの監視の数 | 16 |
| `[limits] max-watches-per-controller`、1 人のコントローラーの実行全体 | 32 |
| `[limits] max-watched-blocks-per-controller`、1 人のコントローラーの監視とトリガー全体のマス数 | 1024 |
| `[events] max-watched-blocks`、サーバー全体 | 2048 |

すべての種類、そのペイロード、それぞれの配送の保証:

<!-- BEGIN GENERATED EVENTS -->
| 種類 | フィルター (どれか 1 つ) | ペイロード | 保証 |
| --- | --- | --- | --- |
| `block.change` | area: area (最大 512 ブロック) | x: integer, y: integer, z: integer, dimension: string, from: block, to: block, cause: string, bot: string? | 最大 1 回、事後に、発生元の順で届きます。4 tick 以上続いた変化が届きます。player-break と player-place は 1 件ずつ届きます。 |
| `player.join` | なし | player: string | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
| `player.leave` | なし | player: string | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
| `player.chat` | なし | player: string, message: string | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
| `bot.online` | なし | bot: string | 最大 1 回、事後に、発生元の順で届きます。40 tick 以上続いた変化が届きます。 |
| `bot.offline` | なし | bot: string, reason: string? | 最大 1 回、事後に、発生元の順で届きます。40 tick 以上続いた変化が届きます。 |
| `bot.died` | なし | bot: string, message: string | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
| `job.finished` | なし | bot: string, job: string, reason: string | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
| `server.started` | なし | なし | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
| `schedule` | every: duration または at: time | at: string | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
| `world.time` | なし | phase: string, time: integer | 最大 1 回、事後に、発生元の順で届きます。発生元が接続している間は、すべての発生が届きます。 |
<!-- END GENERATED EVENTS -->

<a id="triggers"></a>
### トリガー

トリガーは、イベントの種類とフィルターを、スクリプトとその引数に結びつけて保存したものです。一度有効にすると、だれもコマンドを入力しなくても、一致する発生ごとに実行を始めます。

```text
/botscript trigger add <script> <kind> [field=value ...] [name=<name>] [visibility=shared|private] [-- <arg> ...]
/botscript trigger list
/botscript trigger show <name>
/botscript trigger enable <name>
/botscript trigger disable <name>
/botscript trigger remove <name>
/botscript trigger set <name> <policy>=<value> ...
/botscript trigger visibility <name> shared|private
```

スクリプトは `param.event("event")` を宣言します。手動の実行では `a.event = nil` になるので、1 つのスクリプトをどちらの使い方にも使えます。event 引数は、ペイロード、`kind`、`missed` を運びます。

```lua
params {
    param.event("event"),
    param.number("count", 1),
}
if a.event then
    log(a.event.kind .. " at " .. a.event.x .. " " .. a.event.y .. " " .. a.event.z)
end
```

```text
/botscript trigger add guard block.change area="100 64 200" to=air
```

`trigger add` のオプション:

- `area=` は、範囲を引用符で囲んだ 1 つのトークンで取ります。形は `within` と同じです: `"100 64 200"`、`"0 64 0 3 65 3"`、`"5 of 0 64 0"`。トークンの中で、後ろに `except <values>` を続けることもできます。
- 範囲は絶対座標です。トリガーには `~` の基準になる Bot もプレイヤーもいないので、`~` と Bot を中心とする球 (`"3"`) は拒否されます。座標を書いてください。
- ほかのフィルターは `name=value` です。`block.change` では `dimension` (既定は `overworld`)、`from`、`to` です。ブロック名の `minecraft:` は省略できます。
- `--` はスクリプトの引数を区切ります。引数は、`/botscript run` が受け取るとおりの文字列として保存します。
- 名前の既定はスクリプト名です。
- トリガーは、`visibility=shared` でない限り非公開です。shared にしても、ほかの人の `list` と `show` に表示されるだけで、ほかの人が変更できるようにはなりません。

指定の仕方:

- 自分のトリガーは `<name>` で指定します。共有されたものは、`list` と `show` で `<owner>/<name>` と表示されます。
- コンソールだけ: コンソールは、ほかの持ち主のトリガーを `<owner>/<name>` で指定します。owner は表示名 (大文字と小文字を区別しない) か、持ち主のキーです。

**承認。** トリガーを追加したり有効にしたりするときは、スクリプトの宣言を表示し、それをスクリプトの版と引数とともに記録します。トリガーを有効にできるのは、そのスクリプトを実行できる人だけです。

| スクリプト | トリガーが実行するもの |
| --- | --- |
| 自分のもの | 現在の版。宣言が承認した範囲に収まり、引数の宣言が保存した引数をまだ受け付ける間 |
| ほかの人の共有スクリプト | 承認した版だけ。新しい版ができると一時停止します |
| オペレーターのスクリプトファイル | 自分のものと同じく、現在の版 |

始まった実行は、持ち主の権限と承認した宣言の重なる部分だけを持ち、持ち主の同時実行数の上限に数えます。

**ポリシー。** `trigger set <name> <policy>=<value>` で変更します。

| ポリシー | 既定 | 取りうる値 | 効果 |
| --- | --- | --- | --- |
| `concurrency` | `skip` | `skip`、`queue`、`parallel` | `skip` は、このトリガーからの実行が動いているかクールダウン中なら発生を捨て、missed として数えます。`queue` は新しいほうから `queue` 件の発生を保持し、順に始めます。`parallel` は同時に 2 つまでの実行を許します |
| `cooldown` | 20 tick | 0-72000 | 2 回の開始の間の最短時間。`skip` では、その間の発生は遅らせずに捨てます |
| `startsPerMinute` | 10 | 1-60 | 直近 60 秒の窓での開始数。超えるとトリガーは抑制されます: `skip` と `parallel` は発生を missed として捨て、`queue` は窓が開始を許すまで保持します |
| `queue` | 4 | 1-16 | `queue` での保留中の発生の数。あふれると最も古いものを捨て、missed として数えます |

**一時停止と無効化。** 一時停止したトリガーは、持ち主がもう一度有効にする (再承認になります) まで、一時停止のままです。

| 理由 | 起きるとき |
| --- | --- |
| `宣言が広がりました` | スクリプトの宣言が広がった |
| `引数が無効になりました` | 引数の宣言が、保存した引数を受け付けなくなった |
| `スクリプトを利用できません` | 持ち主がスクリプトを実行できなくなった |
| `他の人のスクリプトが変更されました` | 実行するほかの人のスクリプトに新しい版ができた |
| `所有者の権限が取り消されました` | 持ち主の権限が取り消された。トリガーは無効になり、権限を与え直しても有効には戻りません |

- 開始数の上限を超えても、トリガーが止まることはありません。抑制され、再び始まるまでの間に、持ち主にチャットで一度だけ伝えます。
- 一時停止、サーバーによる無効化、持ち主の実行数の上限による開始の拒否 (開始が成功するまで一度だけ) も、持ち主にチャットで伝えます。
- トリガーの実行が正常に終わったときは、ログに残すだけで、チャットには出しません。
- 無効なトリガーは状態を保ち、もう一度有効にできます。

**再起動。**

- endbotd が再起動すると、作動中のトリガーは、状態、統計、最後の開始とともに戻るので、クールダウンは保たれます。1 分の窓は空から始まり、`lastGap` が空白の期間を記録します。
- BDS やリンクが再起動してもトリガーは作動中のままですが、リンクが切れている間は実行を始めません。その間の発生は skipped として数えます。
- runtime から来る種類は、runtime が戻ってから再開します。

**上限。**

- `[limits] max-triggers-per-controller` (既定は 8) が、持ち主ごとの作動中のトリガーの数を制限します。`0` にすると、サーバーでトリガーが使えなくなります。
- トリガーの範囲は、監視と同じく `max-watched-blocks-per-controller` に数えます。
- ほかの実行の上限は、トリガーの実行にもそのまま当てはまります。

<a id="run-lifetime-and-cleanup"></a>
## 実行の寿命と後片付け

実行は、スクリプトが終わるか、止められるか、実行した人の権限がなくなるまで続きます。

- すべての操作は、入力したコマンドと同じくあなたの権限で確認され、同じようにログに残ります。
- 実行は、ログアウトと再参加を越えて続きます。
- 権限を取り消すと実行は終わり、権限を与え直しても生き返りません。
- オンラインのプレイヤーが必要な操作 (たとえば、そのプレイヤーの名前を指定したテレポート) は、今までどおりプレイヤーが必要です。オフラインでも続ける必要がある作業には、宣言した絶対位置を使ってください。
- ログアウト後の引数なしの `spawn` は、記録したあなたの最初の位置、ディメンション、向きを使います。

実行が終わったとき (正常終了、失敗やクラッシュ、停止、権限の取り消し、シャットダウン):

- その実行が始めた続く入力 (移動、アクション、フラグ、採掘) は止まります。
- あなたやほかのコントローラーが後から同じチャンネルで Bot に出した入力は残ります。
- 溜めのない 1 回きりのアクションは、続くジョブではありません。完了したインベントリやワールドの変更は、元に戻しません。
- そのため、続く作業が必要な間、スクリプトは待ち続けなければなりません。

runtime に届かないと、後片付けができないことがあります。その失敗は報告されます。`/bot <name> stop` か `/bot <name> destroy stop` で、自分で Bot を止めてください。runtime を直接動かすほかのプログラムからの入力は、追跡の対象外です。
