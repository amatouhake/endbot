<!-- source: docs/SCRIPTING.md 73dd5e95686764e0531b0daec74b3e476edb9f37 -->

<a id="luau-scripting-reference"></a>
# Luau スクリプトのリファレンス

スクリプトは、Bot を動かす Luau のプログラムです。各スクリプトは、それを作ったコントローラーのものです
([持ち主](#ownership-visibility-and-revisions)を参照)。各実行は、実行した人の権限を使います。このページは、
スクリプトを書いて実行するためのリファレンスです。

- スクリプトは初めてですか。[チュートリアル](SCRIPTING_TUTORIAL.md)から始めてください。
- スクリプトは**既定でオフ**です。有効にすること、その上限、実行プログラム (runner) の隔離はオペレーター側の話です:
  [SCRIPTING_SETUP.md](../SCRIPTING_SETUP.md)。

<a id="commands-authoring-and-parameters"></a>
## コマンド、作成、引数

`/botscript` には `endbot.command.control` が必要です。操作のネイティブな入力候補は `/botgroup` と同じ方式で、
固定した BDS の基準バージョンにある補完の制限もそのまま残ります。

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

`runs`/`stop` は、実行したコントローラー自身の実行を対象にします。コンソールはオペレーターです。コンソールの `runs` は
すべてのコントローラーの実行を持ち主つきで一覧にし、`stop <id|name|all>` はそのどれでも止められます。そのため、
ログアウトしたコントローラーが残した実行も、そのコントローラーの権限を取り消さずに終わらせられます。開始、停止、保存、
削除はコントローラーの名前つきでログに残り、各実行の終了も同じです。スクリプトを削除しても、実行中のものは止まりません。
show は番号付きの、大きさに上限のあるページで表示し、長い行はソースを欠かさずに折り返します。ユーザーが指定した
ファイルシステムのパスからソースを読むことはありません。読むファイルは、オペレーターの
[スクリプトファイル](#script-files)だけです。

<a id="ownership-visibility-and-revisions"></a>
### 持ち主、公開範囲、版

各スクリプトは、作成者を持ち主として記録します (XUID、なければプレイヤーの UUID)。編集、削除、復元、公開範囲の変更が
できるのは、持ち主かコンソールだけです。ほかの人には「スクリプト … を変更できるのは持ち主の … だけです」と返り、
**コピーして編集** を案内されます。スクリプトは `private` (持ち主だけが見て実行できます。ほかの人には存在しないのと
同じです) か `shared` (すべてのコントローラーが一覧表示、show、実行、コピーできます) のどちらかです。新しいスクリプトと
コピーは `private` で始まります。`visibility <name> shared|private` で切り替えます (0.4.0 の `share <name>` と
`private <name>` という書き方は 0.6.0 で廃止され、この形を示して拒否されます)。`copy <name> <new-name>` は、コピーした
人が持ち主になる非公開のコピーを作ります。持ち主の `rename <name> <new-name>` は、持ち主、公開範囲、履歴を保ちます
(実行中のものは古い名前のままです)。保存のたびに、前のソースを直近 10 版の履歴に残します。`history` はそれを一覧表示し
(新しい順、時刻と行数つき)、`restore <name> <n>` は版 `n` を新しい保存として現在の版にし、置き換えられたソースも履歴に
残します。実行は、スクリプトの持ち主がだれであっても、実行した人の権限を使います。持ち主の仕組みより前に保存された
スクリプト (ファイルのバージョン 1) には持ち主がなく、共有のままです。最初に編集したコントローラーが持ち主になります。
一覧は `name (owner, visibility)` の形で表示し、自分のものには `自分` と付きます。コンソールは、
保存されたスクリプトの持ち主を `owner <name> <GamerTag>` で付け替えられます。持ち主のキーと名前が変わり、公開範囲、版、
履歴はそのままです。ファイルのスクリプトには付け替える持ち主がいません。ファイルが本体です。未実装のもの: 実行と作成で
別々の権限 (`endbot.script.run`/`endbot.script.author`)。今はどのコントローラーも作成できます。

有効なとき、`/bot menu` には **+ ここに新しい Bot** の後に (**グループを管理...** の隣に) **スクリプト...** が並びます。
その上の対象の一覧には、`/bot` で指定できる対象だけが並びます。スクリプトの画面は、ソース全体を灰色の行番号つきで表示
します (折り返した行は番号と離れたままです。とても長いスクリプトは途中で切り、`show` を案内します。ソース中の `§` は
`\u{a7}` と表示するので、ソースの文字がプレビューに色を付けたり、隠したり、崩したりすることはできません)。そして、
実行、実行を停止、コピーして編集 (書き出しが有効なら、手に持った本に書き出す も) を選べます。持ち主には、コピーして
編集の代わりに **行を編集...** と **その他...** が表示されます。その他には、名前を変更、操作者に共有する/非公開にする、
以前の版 (その版のソースを見せる確認つきで復元)、コピーして編集、手に持った本に書き出す、削除 (確認つき) があります。
作成では、**グループを一列に並べる**、**簡単な巡回**、**指定地点で採掘**、空のスクリプト、**手に持った本から...** を
選べます。テンプレートかコピーなら名前だけを聞かれ、保存すると新しいスクリプトが開きます。

**行を編集** は、ソースを 1 行ずつボタンにして並べます (1 ページ 40 行)。行を押すと、その行が入力済みのフォームが
開きます。下に追加する行 (省略可。自分でインデントを入れない限り、編集した行のインデントを引き継ぎます) と、
**この行を削除** のトグルもあります。**+ 先頭に行を追加** と **+ 末尾に行を追加** で、両端に行を足せます。1 行の欄は
その場で直しやすくなっています。クライアントの 2 行の欄にソース全体を入れておく方式とは違います。その方式では、欄を
空にできませんでした。Windows クライアントのフォームの欄は**貼り付けた改行を落とします** (2026-10-25 に確認)。
そのため、複数行の変更は本を使います (一覧の最後にある **手に持った本で全体を置き換え**。下を参照)。保存すると一覧の
同じページに戻り、何も変えないフォームはそのまま戻ります。保存ではソース全体をコンパイルし、本体を受け付けずに宣言を
調べます。`save` はフォーム内部の動作で、入力してもソースは更新できません。古くなった編集、削除、実行のフォームは、
ソースの版が変わっていれば拒否されます。キャンセルしても保存されません。下書きは残りません。

<a id="books"></a>
### 本

本と羽根ペンは、ゲーム内で使いやすいエディターです。改行を保ち、Windows では Ctrl+A/C/X/V が使えます。
`import <name>` (メニュー: 新しいスクリプトなら **手に持った本から...**、自分のスクリプトなら
**手に持った本で全体を置き換え**) は、メインハンドに持った本と羽根ペン、または記入済みの本を読みます。ページは改行で
つなぎます。ただし、すでに改行で終わっているページはそのままつなぎます。末尾の空白ページは無視します。新しい名前なら
非公開のスクリプトを作り、既存の名前なら、メニューと同じコンパイル、宣言、持ち主の確認を通して新しい版を保存します。
本は読むだけです。メニューの **手に持った本で全体を置き換え** は、まず本のソースを両方の行数とともに表示します。
**置き換える** を押し、本がまだその内容のままの場合だけ、スクリプトを置き換えます。**手に持った本から...** は、すでに
ある名前を拒否します。入力した `import` で既存の名前を指定すると、すぐに保存します (前のソースは以前の版に残ります)。

**複数行の作成手順 (2026-10-25 の Windows での確認):** テンプレート (または空) から作り、スクリプトを開き、
**手に持った本に書き出す** を押し、ページを編集してから、**手に持った本で全体を置き換え** で新しい版を保存します。本は
編集の間もソース全体を保つので、書き出しは 1 回で足ります。クライアント自身の本のエディターは、ページの容量を超えた
文字を何も言わずに落とします。長いソースは手でページに分けてください (最大 50 ページ、各 256 文字)。収まらない
スクリプトはプラグインが拒否し、問題の行を示します。

`export <name>` (メニュー: **手に持った本に書き出す**) は、実行できるスクリプトをメインハンドの本と羽根ペンに書き込み
ます。そこで編集して、もう一度取り込めます (ほかの人の共有スクリプトなら、新しい名前で取り込みます)。空の本にはすぐに
書き込みます。すでに書いてある本を上書きするのは `export <name> overwrite` だけで、ただの `export` は拒否します。
メニューは先に確認します。本の今の内容とページ数を表示し、**上書きする** で `export <name> overwrite` を実行します。
本がまだそのページのままの場合だけ書き込みます。記入済みの (署名した) 本は決して変えません。ページには行をまるごと
入れます。1 ページ 256 文字、50 ページまでなので、それより長い行やスクリプトは拒否されます。アイテムを作ることは
ありません。書き出しは既定でオンです (複数行の作成手順の最初の一歩だからです)。持っているアイテムをプラグインに一切
変えさせたくないオペレーターは、`[scripting] book-export = false` を設定します。ページと文字数の上限は控えめな値です。
実際のクライアントでの上限は、人間のクライアントでの確認とともに記録しています。

<a id="optional-ui-pack-field"></a>
### 任意の UI パックの入力欄

任意の [Endbot UI リソースパック](UI_PACK.md)をグローバルリソースとして追加したプレイヤーは、大きなフォームの
ウィンドウ、コンパクトなボタン、複数行の入力欄を使えます。`[scripting] ui-pack-editor = true` (既定は false) にすると、
行のエディターに **全体をまとめて編集 (UIパック)...** も表示されます。ソース全体を 1 つの欄で編集でき、ほかの編集と
同じように保存します。新しいスクリプトの画面も同じ欄にテンプレートを表示するので、スクリプトを作る前に全体を書けます。
パックがないとクライアントが改行を落とし、その返信は保存せずに拒否します。その代わり、新しいスクリプトの画面の
**表示どおりに保存** のトグルで、テンプレートをそのまま保存できます。パックがないとフォームの入力も約 100 文字で
止まるので、それより長い行は、パックを使うか、スクリプトファイルか、本で編集する必要があります。パックのウィンドウ、
ボタン、複数行の欄は 1.26.60.5 のクライアントで確認しました ([UI パック](UI_PACK.md))。ほかのクライアントでは、本が
確認済みの複数行の方法です。

<a id="script-files"></a>
### スクリプトファイル

PC のエディターでスクリプトを書くには、オペレーターが `<name>.luau` ファイルを `<instance>/scripts/` に直接置きます。
`endbot.toml` と同じ場所にあるフォルダーです (セットアップ時と起動時に作られます。endbotd より前は
`server/plugins/endbot/scripts/` でした)。ファイルは、サーバーの起動時と、`/botscript reload` (コンソール、どの
コントローラーでも、またはスクリプト一覧の **スクリプトファイルを再読み込み**) で読まれます。フォルダーを監視する
仕組みはありません。すでに動いている実行は、始めたときのソースのままです。

- ファイルのスクリプトはすべてのコントローラーに**共有**され、ゲーム内では**読み取り専用**です。実行、実行を停止、
  show、コピーして編集、export は使えます。編集、名前の変更、削除、共有は、ファイルを案内して拒否します。一覧では
  持ち主を `ファイル` と表示します。コピーすると、コピーした人が持ち主の普通の保存済み
  スクリプトになります。
- 版の履歴はありません。必要なら、フォルダーを自分でバージョン管理してください。`scripts.json` に書かれることは
  ありません。
- 読み込むのは、正しいスクリプト名を持ち、`max-script-bytes` 以下の普通の `.luau` ファイル (UTF-8、CRLF も可) だけです。
  リンク、ほかの拡張子、サブフォルダーは無視されるか拒否されます。同じ名前の保存済みスクリプトがあれば (だれのもの
  でも、非公開でも。名前はサーバー全体で共通です)、そちらが優先され、ファイルは「読み込みませんでした。同じ名前の
  保存済みスクリプト … があります」として飛ばされます。コンソールと起動時のログはそのスクリプトの持ち主も示すので、
  オペレーターが整理できます。
- `reload` は各ファイルの宣言の確認も行い、飛ばしたファイルをすべて理由つきで返します。起動時はファイルを読むだけで、
  飛ばしたものはサーバーコンソールにログを出します。ファイルが外されるのは、そのファイル自身の問題 (宣言のエラー、
  または `params` の前で止まってしまう) のときだけです。確認を実行できないとき (呼び出した人の実行枠が埋まっている、
  runner がない) は、ファイルを読み込んだままにし、確認していないことを返事で伝えます。読み込むファイルは最大
  `max-scripts` 個です。確認は保存と同じくコマンドワーカーで実行し、`params` の前で止まるファイル 1 つにつき、最悪で
  約 5 秒かかります。そのため、そういうファイルがあるフォルダーの再読み込み中は、`/bot` のコマンドが待たされます。

スクリプトは、バージョン付きの `state/scripts.json` (endbotd より前はプラグインのデータフォルダー) にアトミックに保存
されます。名前は大文字と小文字を区別せず、グループと同じ規則に従います (ASCII の英字、数字、アンダースコアで
1-16 文字、先頭は英字かアンダースコア)。ディスクへの書き込みエラーが起きても、前のファイルとメモリ上の版はそのまま
残ります。再起動後に、自動で再開したりやり直したりすることはありません。

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

`params` は、ホストの処理より前にちょうど 1 回呼んでください。引数がなくても呼びます (`params {}`)。ホストが本体を
受け付ける前に、`params` が宣言を渡します。調査 (inspection) はこの宣言まで実行します。それより前のコードには通常の
予算があり、Bot、log、wait の関数は呼べません。コンストラクターは `param.group(name[, default])`、
`param.bot(name[, default])`、`param.pos(name[, default])`、`param.number(name[, default])`、
`param.bool(name[, default])`、`param.string(name[, default])`、`param.choice(name, {"a", "b", ...}[, default])` です
(choice は 1-32 個の異なる文字列で、それぞれ 1-64 文字。実行フォームではドロップダウンになります)。各宣言は名前を
持ち、`a.<name>` での参照は変わりません。宣言は連続した配列で渡してください。名前付きキー、混在、歯抜けのテーブルは
拒否され、重複した名前、不明な型、正しくない既定値も拒否されます。名前は `[A-Za-z_][A-Za-z0-9_]{0,31}` に一致する
必要があり、最大 32 個です。宣言のエラーは、可能なら `params` 呼び出しのスクリプトの行を示し、日本語のプレイヤーには
日本語で表示します。runner とホストの間のプロトコルは、`schema` を JSON 配列で運びます。引数がないときは `[]` です。
各要素は `{"name":"spacing","type":"number","default":2}` の形です (choice には `"choices":[...]` が加わります)。
プラグインは、フォームや実行の前に、この配列をもう一度検証します。

基本の組を超える capability が必要なスクリプトは、`requires { "name", ... }` で宣言します。呼ぶのは 1 回だけで、
`params` より前です ([PERMISSIONS.md](../PERMISSIONS.md#two-layers-the-person-and-the-script) を参照)。基本の組
(Bot の操作、ワールドの読み取り、待機、ログ: `act.bot`、`read.world`、`wait`、`log`) は宣言が不要で、書いても何も
変わりません。ほかの capability はイベントの種類から来ます (`observe.chat`、`observe.presence`)。それ以外の名前は、
その名前を示す調査のエラーになります。2 回目の呼び出し、`params` の後の呼び出し、異なる文字列のリスト以外を渡すことは、
宣言のエラーです。`params` の要求は、このリストを `requires` として運びます (呼び出しがなければ `[]`)。調査はこれを
schema とともに集合として返すので、順序は関係ありません。調査から始まった実行 (宣言をガードに持つ実行フォーム、
引数のないメニューからの開始、トリガー) は、自分の `requires {}` がその調査で読んだものと違えば、最初の操作の前に
止まります。そのため、条件付きの呼び出しで、表示した範囲を広げることはできません。実行フォームは 1 行目に宣言を
表示し、そのフォームから送った実行はその宣言に縛られます。

**位置引数とメニューの入力欄は、宣言の順に従います。** 位置引数の値の後に `name=value` で、宣言したどの引数でも
指定できるので、前の既定値を飛ばせます。上の例では、`/botscript run row #miners`、
`/botscript run row #miners "~ ~ ~" 3`、`/botscript run row #miners spacing=3` を受け付けます。名前付きの値の後に
位置引数の値は置けません。1 つの引数は 1 回しか指定できません。`word` が宣言した引数でない `word=...` は、普通の
位置引数の値として扱います。空白を含む位置と文字列は、引用符で囲んでください (`origin="0 70 0"`)。
位置は Luau の**ベクトル**として届きます。`a.origin.x` を読んだり、足したり、倍にしたり
(`a.origin + vector.create(2, 0, 0) * i`) でき、操作が `x, y, z` を取るところにはどこでも 1 つで渡せます
(`bot:tp(a.origin)`)。宣言した引数の `~` 座標は、コマンドやフォームを送ったときに記録したプレイヤーの位置から
決まります。コンソールの位置は絶対座標でなければなりません。グループと Bot は存在している必要があり、数値は有限で
なければなりません。真偽値は `true`/`false` を、choice はその選択肢のどれかを受け付けます。実行フォームも同じ順で、
グループ、Bot、choice のドロップダウン、プレイヤーの位置を初期値にした位置の欄、宣言した既定値を入れた数値や
テキストの入力とトグルを並べます。
フォームでの選択は、グループの UUID と順序、Bot の UUID も固定し、送信前に削除や作り直しがあれば拒否します。
宣言した Bot 引数は、その後の要求でも UUID の固定を保ちます。明示的にグループを固定した呼び出しは、グループ自身の
同一性のガードを保ちます。

普通の操作の呼び出しが返すのは、完了ではなく**受け付け**です: `{ok=true,code="ok",messages=...,data=...}` か
`{ok=false,code=...,reason=...,messages=...}`。グループの変更は `data` に件数を含みます。一部が失敗すると
`ok=false` です。`code` は次にすべきことを示し、どの言語でも同じです: `unauthorized` (権限)、`not_found` (その Bot や
対象がない)、`invalid_argument` (要求を直す)、`unavailable` (後でもう一度試す)、`conflict` (今の Bot の状態ではでき
ない。たとえばオフライン、名前が使用中)。Bot が狙えないブロックには専用の code があり、
[ブロックの狙い方](#targeting-blocks)にまとめています。グループの code は、最初に失敗したメンバーのものです。クエリの
各行にも、そのメンバーの `code` が入ります。分岐は `code` で行ってください。`reason` は人が読むための文言です。
拒否された呼び出しは、たとえば `assert(r.ok, r.reason)` のように明示的に扱ってください。権限の取り消し、頻度や期限、
プロトコルのエラーは実行を終わらせます。
`wait(ticks)` (20 tick が実時間の 1 秒) と `waitOnline(bot("Alice"))` を使ってください。`waitOnline` は名前や
グループのハンドルも受け付けます。`log(text)` は、実行 id を付けた 1 行をサーバーのログに書き、実行を始めた
プレイヤーのチャットにも出します。チャットへは 1 実行あたり 5 秒に 10 行までで、各行は書式コードを除いて 256 文字で
切ります。出さなかった行は、次のチャットの行か終了メッセージで数を伝えます。サーバーのログにはすべての行が残ります
(1 実行あたり 2000 行 / 512 KiB まで)。`[scripting] chat-logs = false` (プラグインの `config.toml`、管理された
インスタンスでは `endbot.toml`) にすると、ログはサーバーのログだけに残ります。コンソールからの実行は、ログを
サーバーのログだけに出します。待機中も権限を確認し直します。コマンド文字列を渡す API はありません。

スクリプトが出す操作は、どれも普通の `/bot` コマンドでもあるので、その返事は入力したときと同じく実行した人の
チャットに届くはずです。何十もの操作を出すスクリプトでは、この返事はうるさいだけです。そこで、`[scripting] replies`
(プラグインの `config.toml`、管理されたインスタンスでは `endbot.toml`) で方針を選びます。`all` は入力したコマンドと
同じくすべての操作の返事を出し、`failures` (既定) は拒否された操作だけを出し (受け付けた操作も、コンソールのログ行は
残ります)、`none` は何も出しません。`failures` では、`not_a_target` で拒否された `destroy` は出しません。スクリプトが
読んだ後に対象が消えることがあるからです ([ブロックの狙い方](#targeting-blocks)を参照)。スクリプトはそれでも拒否を
受け取ります。実行の開始と終了の行と `log()` は影響を受けず、連続採掘の開始と停止の通知もチャットに届きます。
スクリプトから方針は変えられません。

<a id="structured-status-and-mining-waits"></a>
### 構造化された状態と採掘の待機

`bot:status()` は、いつもの結果のラッパーを返します。下の `data` のフィールドを読んでください。採掘の値は runtime の
状態から来ます。最後に保持した採掘ジョブも含みます。経過時間から完了を推測することはありません。位置、向き、体力、
インベントリは、呼び出した時点で Endstone SDK を通して BDS から取ります。満腹度と隠し満腹度は、BDS が Bot に送る
属性から来ます。Bot がワールドにいないとき (または BDS がまだ送っていないとき)、フィールドは nil です。

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
| `data.mining.active` | 真偽値: ブロックを壊すジョブか連続採掘が動いている (対象を待っている間も含む) |
| `data.mining.mode` | `once` か `continuous`。採掘ジョブがまだなければ nil |
| `data.mining.state` | runtime のジョブの状態 (`active`、`waiting`、`completed`、`canceled`、`failed`)。ジョブがなければ nil |
| `data.mining.blocksBroken` | runtime の連続採掘の数。1 ブロックの場合は、完了なら 1、それ以外は 0。ジョブがなければ 0 |
| `data.mining.blockCount` | 連続採掘の数の上限。無制限かジョブがなければ nil |
| `data.mining.reason` | 動いている間は nil。それ以外は runtime の終了理由。ジョブがなければ nil |
| `data.mining.target` | `state` が `active` の間は壊しているブロックの `{x, y, z, type}`、それ以外は nil |
| `data.running` | 動いている入力ジョブの id を種類別に (`move`、`jump`、`attack`、`use`、`interact`)。何も動いていなければ nil |
| `data.ended` | 入力の種類ごとの最後の終了: `{job, reason}` |
| `data.teleports` | runtime の起動後に Bot が確認応答した、サーバーのテレポートの数 |

終了理由には、`block count reached`、`duration elapsed`、`stopped`、runtime の失敗理由があります。JSON の null の
フィールドは、Luau では nil になります。`group:status()` は、固定したメンバー順で `data.results` を返します。各行には
`index`、`identityId`、`name`、`ok`、`reason` (成功時は nil)、そしてそのメンバーの上のフィールドを入れた `data`
(クエリが失敗したら nil) があります。クエリが一部失敗すると、外側が `ok=false` になります。

ほかのクエリもデータを返します。`bot:inventory().data.slots` は、空でないスロットを `{slot, name, count}` で並べます
(スロットは `"1"`-`"36"`、`offhand`、`head`、`chest`、`legs`、`feet`)。`bot:hotbar().data` には `selectedSlot` と
ホットバーの `slots` があります。`bot:entities().data.entities` は、近くのプレイヤー以外のエンティティを近い順に
最大 10 件、`{runtimeId, type, distance, position, health, maxHealth, nameTag}` で並べます (Mob でなければ health は
nil)。`selector` (`"@9"`) を `:interact(e.selector)` や `:attack(e.selector)` に渡してください。エンティティには常に
`@` を付けます。`bot:entity("@cow")` (または `"@9"`、`"@nearest"`、引数なし) は、16 ブロック以内のそうした要素
1 つを `data.entity` として返します。繰り返しは、最後のオプションテーブルで指定します:
`bot:interact("@cow", {count=5})`、`bot:attack("@zombie", {interval=20, ticks=200})`、
`bot:destroy("oak_log", {count=10})`。インベントリ、ホットバー、`held` のアイテムのうち、道具や防具のように耐久値の
あるものには `durability = {remaining, max}` が付きます。

ワールドのクエリはサーバースレッドで BDS を読み、操作として数えます:

| 呼び出し | `data` |
| --- | --- |
| `bot:target()` | `kind` (`"block"`、`"entity"`、nil)、`distance`、そして `block = {x, y, z, face, type}` か `entity = {runtimeId, type}`。Bot の目から 6 ブロック以内で、近いほうに当たったもの。プレイヤーは無視します |
| `bot:block(x, y, z)` | `{x, y, z, type, states, reachable}`。届かないときは `reachCode` と、`blocked` なら `obstruction` も加わります。`"~"`/`"~-1"` の文字列は Bot からの相対です。読み込まれている位置ならどこでも使えます |
| `bot:blocks("oak_log", {limit=10})` | `{type, radius, blocks}`。`blocks` は `{x, y, z, distance, reachable}` を Bot に近い順に並べます。対象は Bot の周りの半径 8 の球の中で、プレイヤーに見えるブロックだけです (透視はしません。下を参照)。limit は 1-64 |
| `bot:blocks("oak_log", {area=area.sphere(5, x, y, z)})` | `{type, area, blocks}`: 同じクエリを[範囲](#areas)で絞ったもの。たとえば木のてっぺんの周り。Bot から 16 ブロックより先は探しません |

半径 12 の探索は、実際の確認 (2026-10-24) で、サーバースレッドの時間を約 23 ms 使いました。

<a id="areas"></a>
### 範囲

1 つの範囲の値で、`blocks`、種類を指定した `destroy`、`destroy` の連続採掘、種類を指定した `interact` が選べる
ブロックを絞り込みます。`area` で作り、`{area = a}` として渡します:

| Luau | チャット | 形 |
| --- | --- | --- |
| `area.cell(x, y, z)` または `area.cell(v)` | `within <x> <y> <z>` | 1 ブロック |
| `area.box(x1, y1, z1, x2, y2, z2)` または `area.box(v1, v2)` | `within <x1> <y1> <z1> <x2> <y2> <z2>` | 直方体。`/fill` と同じく両方の角を含む |
| `area.sphere(r)` | `within <r>` | Bot を中心とする半径 `r` の球 |
| `area.sphere(r, x, y, z)` または `area.sphere(r, v)` | `within <r> of <x> <y> <z>` | ある地点を中心とする半径 `r` の球 |
| `a:minus(b)` | `except <b>` | `a` から `b` のブロックを除いたもの。`b` は上のどの形でもよい |

座標は、数値、ベクトル、または Bot からの相対の `"~"`/`"~-1"` の文字列です。`:minus` は自由につなげられます
(`area.sphere(6):minus(footing):minus(trunk)`)。チャットでは `except` は 1 つです。範囲の値はこれらの関数でしか
作れないので、テーブルや文字列は、ホストに届く前に拒否されます。

規則は[コマンドの規則](COMMANDS.md#areas-within-and-except)と同じです。範囲は絞るだけで、届く距離や視線を広げる
ことはありません。`~` は操作を受け付けたときに決まるので、連続採掘は Bot が動いても範囲を保ちます。`within` は最大で
半径 16 の球までです。範囲がなければ、各操作は既定の動きのままです。Bot の足場は特別扱いしないので、明示的に除いて
ください:

```lua
local footing = area.cell("~", "~-1", "~")
local job = bot:destroy("acacia_log", {count = 20, area = area.sphere(6):minus(footing)}).job
job:wait{}
```

`blocks(type, x, y, z)` と `{radius = r}` は廃止されました。呼ぶと、`blocks(type, {area = area.sphere(r, x, y, z)})`
を示すメッセージで失敗します。

`interact` は、ブロックの種類と `@entity` セレクターで 1 つの `target` 引数を共有するので、`{area = a}` はその
シグネチャの一部です。範囲が効くのは、対象がブロックの種類のときだけです (`interact("chest", {area = ...})`)。範囲は
ブロックを絞るものなので、`@entity` の対象はコマンド層で「範囲を指定できるのはブロックの種類 …」として拒否されます。

<a id="targeting-blocks"></a>
### ブロックの狙い方

ブロックについての 3 つの問いには、それぞれ 1 つの答えがあります。ブロックの性質は、固定した Bedrock のバージョンの
minecraft-data から取ります (`scripts/generate_block_properties.py`)。

| 問い | 規則 | 使うもの |
| --- | --- | --- |
| プレイヤーに見えるか | 面の 1 つが、光を通すブロックに接している: 空気、液体、植物、葉、ガラス、氷、そしてハーフブロック、階段、フェンス、塀、板ガラス、ドアのような部分的な形 | `blocks` が並べるブロック |
| Bot が今狙えるか | 空気でも液体でもなく、一部が Bot の目から 6 ブロック以内にあり、Bot に向いた面まで、当たり判定のないブロック (空気、液体、草、花、松明、レール、雪の層) だけを通る直線が届く。葉、ガラス、部分的な形は直線を止める | `reachable`、`destroy x y z`、`destroy <type>`、`interact x y z`、`interact <type>` |
| 照準の先に何があるか | Bot の視線に沿って 6 ブロック以内で最初の、空気でも液体でもないブロック。プレイヤーと同じく、草や松明には当たり、ライトブロックには当たらない | `target`、および対象なしの `destroy` と `interact` |

そのため、背の高い草の後ろの原木は、名前や座標なら届きますが、照準はその手前の草に当たります。葉に包まれた原木は
`blocks` には出ますが、葉を 1 つ壊すまでは届きません。

ブロックを狙えないとき、`block` は `reachable = false` と `reachCode` を返し、`destroy`/`interact` は同じ値を
`code` として失敗します:

| コード | 意味 |
| --- | --- |
| `not_a_target` | 空気か液体、または照準の先に何もない |
| `out_of_reach` | どの部分も Bot の目から 6 ブロック以内にない |
| `blocked` | 何かがすべての直線をさえぎっている。見つかったときは、`obstruction = {x, y, z, type}` (拒否された操作では `data` の中) が、さえぎっている最初のブロックを示す |
| `unbreakable` | `destroy` のみ: 岩盤、バリアなど、サバイバルで壊せないブロック。狙うことはできる |

`block` と `blocks` は読み取りで、予約ではありません。`reachable` は読んだ瞬間のことで、`destroy` と `interact` は
実行時に対象をもう一度確認します。その間にブロックが消えることがあります。Bot 自身の前のひと振りがまだ処理中だった、
葉が自然に消えた、別の Bot やプレイヤーが壊した、などです。そのとき、操作は `not_a_target` で失敗します。ブロックが
なくなればよいだけのスクリプトは、`destroy` のこの code を完了として扱えます。

```lua
local info = bot:block(x, y, z).data
if not info.reachable and info.reachCode == "blocked" and info.obstruction then
  local o = info.obstruction
  bot:destroy(o.x, o.y, o.z)  -- 先に葉を壊す
end
```

<a id="jobs-and-waits"></a>
### ジョブと待機

続く操作の結果は、その**ジョブ**を持っています。ジョブとは、操作が返った後も Bot がし続けることです。`move`、
`jump`、`attack`、`use`、`interact` (続くモードならどれでも。`hold` 付きの `use` once も含みます)、`destroy`、`tp` が
ジョブを返します。グループでは、ジョブを始めたすべてのメンバーを 1 つのジョブがまとめます。`tp` のジョブは、Bot が
サーバーのテレポートを確認応答すると `arrived` で終わります。`job:wait` で待ちます:

```lua
local r = bot("Alice"):move("forward", {ticks = 40})
local w = r.job:wait{timeout = 200}   -- {ok, code, reason, results}
if w.ok then log("stopped: " .. w.reason) end  -- duration elapsed, count reached, completed, stopped, replaced, ...
group("#miners"):destroy("stone", {count = 64}).job:wait{timeout = 2400}
```

どの待機も `{timeout = ticks}` (既定は 1200) を取り、共通の結果の形を返します: `ok`、`code`、`reason`、そして Bot と
グループの待機では、メンバーごとに `name`、`identityId`、`ok`、`code`、`reason` を持つ 1 行。どんな理由であれ
終わったジョブは、その理由とともに `ok=true` です (`stopped`、同じ種類の新しいコマンドで置き換えられた `replaced`、
`died`、`disconnected`、`held item changed`、...)。タイムアウトは `ok=false, code="timeout"` で、ジョブは**止めません**。
オフラインのメンバーは `ok=false, code="conflict", reason="offline"` です。もう動いておらず、その終了が同じ種類の
後のジョブで上書きされたメンバーのジョブは、`ended` を返します。`destroy` のジョブは、`waitMining` と同じように
待ちます。

`bot:waitOnline{timeout=...}` (グループにもあります) は、対象がオンラインになるまで待ちます。タイムアウトでは
`ok=false, code="timeout"` を返します。`timeout` がなければ、`waitOnline(b)` と同じく待ちます。`waitOnline(b)` と
`b:waitMining{}` は 1.0 まで別名として残るので、今あるスクリプトはそのまま動きます。

`bot:waitMining{timeout=1200}` と `group:waitMining{timeout=1200}` は、破壊と採掘のジョブだけを待ちます。
`{ok=boolean, code=string, reason=string, results={...}}` を返します。各結果には `name`、`identityId`、`ok`、`code`、
`reason`、`mining` (上のフィールド) があります。グループは今のメンバー全員を待ち、最初の確認 (ポーリング) で
メンバーと同一性を固定します。メンバーが入れ替わると、実行は終わります。直接書いた Bot の同一性も、最初の確認で
固定します。移動などほかの続く入力は、この待機を遅らせません。

タイムアウトの既定は **1200 tick (実時間で 60 秒)** で、0 から 2147483647 の整数でなければなりません。0 は状態の
確認を 1 回だけ行います。確認はホストで `max(0.25, 1 / operations-per-second)` 秒 (実時間) ごとに行い、最後の
待ち時間は残りのタイムアウトまで短くします (タイムアウトの判定は、最後に確認したスナップショットを使います)。
確認は通常の操作の頻度制限を使い、権限と停止を確認し直します。待っている間、コマンドワーカーは占有しません。停止、
権限の取り消し、シャットダウン、または任意で設定する正の実行期限は、待機を成功として返すのではなく、実行を
終わらせます。

動いていないオンラインのメンバーは、その終了理由 (ジョブがなければ `idle`) を返し、状態が `failed` でなければ
`ok=true` です。オフラインのメンバーは `ok=false, reason="offline"` を返し、タイムアウトの時点でまだ動いている
メンバーは `ok=false, reason="timeout"` を返します。タイムアウトしても、採掘ジョブは止まりません。クエリの失敗は、
そのクエリの理由とともに `ok=false` を返します。外側の `ok` は、全メンバーが成功したときだけ成功になります。`reason`
は、共通の理由、成功の理由がまちまちなら `completed`、そうでなければ最初に失敗したメンバーの理由です。メンバーごとの
結果には、まちまちの結果がそのまま残ります。

**指定地点で採掘** は、`name`、`start`、`spots` (既定は 2)、`step` (既定は 4)、`blocks` (既定は 2) をこの順に
受け付けます。`start` から +X 方向へ `step` の間隔でテレポートし、+Z を向いて
`destroy{mode="continuous",count=blocks}` を実行し、最大 1200 tick 待って、各位置で `spot N: <reason>` をログに
出します。実行する前に、各地点の前に、届く壊せるブロックを用意してください。

<a id="generated-api-reference"></a>
## 生成された API リファレンス

`/bot` のパーサー、型付きの argv アダプター、runner のテストが読むのと同じ `operations.json` から生成しています。
更新は `node runner/api-reference.mjs`、確認は `node runner/api-reference.mjs --check` です。値の範囲やオプションの
組み合わせの意味を検証するのは、今までどおり普通のパーサーです。メソッドは `bot("Alice")` と `group("#miners")` で
使えます。グループ全体の rename/forget には、1 つの Bot だけという制限が残ります。

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

名前付きのオプションは、最後のテーブルで渡します: `:move("forward", {ticks=20})`、
`:destroy{mode="continuous",count=64}`、`:drop("all")`、`:use{interval=40,hold=20,ticks=120}`。`ticks` は `for` に
対応し、アクションの `interval` は `mode` の代わりになります。sprint と sneak は真偽値を取ります。テレポートの
オプションは、`yaw,pitch` を組で使うか、プレイヤーには `facing` を使うか、`facingX,facingY,facingZ` をすべて使います。
`dimension` は先頭の `in` 句になります。引数 3 つの `look(x,y,z)` は `look at` で、コマンドと同じく Bot からの相対
です。`group:members()` は UUID とメンバーの順序を固定し、番号付きのメンバーのハンドルを返します。ルートの
list/ping/help は、ルートの意味のままです。スクリプトの `menu()` は、UI を開かずに受け付けを返します。
`look` (両方の形) は、BDS が新しい向きを報告してから返り、観測した `{yaw, pitch}` を `data.look` に入れます。その
ため、直後の照準を使う操作は新しい視点を使います。BDS が約 0.5 秒 (20 TPS で 10 tick) 以内に報告しなければ、結果は
`code="timeout"` になります。

<a id="event-kinds"></a>
## イベントの種類

スクリプトが監視できるイベントの種類も、同じレジストリから来ます。トリガーは発生のたびに実行を始めるもので、同じ
種類とフィルターを使います。

<a id="watches"></a>
### 監視

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

- `schedule` は、`every` (整数と `s`、`m`、`h` のどれか。`10s` から `24h`)、`at` (endbotd のローカル時刻での `HH:MM`。
  1 日 1 回)、またはその両方を取ります。`data.at` は、発動した tick のローカル時刻です。
- `world.time` は、省略可能な `phase` (`day`、`sunset`、`night`、`sunrise` のどれか) を取ります。`data.phase` と
  `data.time` (その日のレベル時刻、0-23999) は、昼夜の周期が別の段階に入ったときに変わります。

- `watch(kind, filter)` は、返す前に、種類、すべてのフィルターの名前と値、必須のフィルター、種類の capability を
  `requires {}` と照らし合わせて確認します。間違いがあると、そのフィールドを示すメッセージで実行が止まります。
  ブロック名の `minecraft:` は省略できます。
- `block.change` は、`area.cell`、`area.box`、`area.sphere` で作った `area` (`:minus` は 16 個まで) を取り、大きさは
  512 ブロックまでです。監視には基準になる Bot がないので、`~` と Bot を中心とする球は拒否されます。Bot の
  `status()` の座標など、位置から範囲を作ってください。省略可能なフィルター: `dimension` (既定は `overworld`)、`from`、
  `to`。
- バッファーは `watch()` の時点で始まるので、待機と待機の間に取りこぼすことはありません。監視は、新しいほうから
  64 件の発生を保持します。`missed` は、前回成功した待機から後に捨てた数です。
- `w:wait{timeout = ticks}` はほかの待機と同じです。既定のタイムアウトは 1200 tick で、タイムアウトは
  `ok = false, code = "timeout"` です。`data` は発生した内容です。`block.change` では `x`、`y`、`z`、`dimension`、
  `from`、`to`、`cause` (`player-break`、`player-place`、`poll` のどれか)、そして Bot がしたときは `bot` です。人間の
  プレイヤーの名前は決して渡しません。
- 発生元 (BDS かリンク) が再起動したり、発生を捨てたりした後は、次の待機が一度だけ理由つきで
  `ok = false, code = "unavailable"` を返し、その後は待機を続けます。そうした空白の間の変化は、後から届けません。
  空白を許せないスクリプトは、`block()` で読み直してください。
- 待機は、発生が届けばすぐに答えます。操作の予算は使いません。
- `player.join`、`player.leave`、`player.chat` は人間のプレイヤーだけを報告し (Bot には `bot.*` の種類があります)、
  `requires { "observe.presence" }` か `requires { "observe.chat" }` が必要です。実行フォームとトリガーのフォームは、
  宣言を最初に表示します。`player.chat` は公開チャットだけです: `/tell`、`/msg`、`/w` は決して報告しません。
  メッセージは 256 文字で切ります。
- 上限: 1 実行あたり 16 個の監視、1 人のコントローラーの実行全体で `[limits] max-watches-per-controller` (既定は 32)、
  1 人のコントローラーの監視とトリガー全体で `[limits] max-watched-blocks-per-controller` (既定は 1024) マス、
  サーバー全体で `[events] max-watched-blocks` (既定は 2048)。上限を超える監視は、その上限を示して拒否されます。

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

トリガーは、イベントの種類とフィルターを、スクリプトとその引数に結びつけて保存したものです。一度有効にすると、
その後はだれもコマンドを入力しなくても、一致する発生ごとに実行を始めます。スクリプトは `param.event("event")` を
宣言します。手動の実行では `a.event = nil` になるので、1 つのスクリプトをどちらの使い方にも使えます。event 引数は、
ペイロード、`kind`、`missed` を運びます。

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
/botscript trigger add <script> <kind> [field=value ...] [name=<name>] [visibility=shared|private] [-- <arg> ...]
/botscript trigger add guard block.change area="100 64 200" to=air
/botscript trigger list
/botscript trigger show <name>
/botscript trigger enable <name>
/botscript trigger disable <name>
/botscript trigger remove <name>
/botscript trigger set <name> <policy>=<value> ...
/botscript trigger visibility <name> shared|private
```

- `area=` は、範囲の値を引用符で囲んだ 1 つのトークンで取ります。形は `within` と同じです: `"100 64 200"`、
  `"0 64 0 3 65 3"`、`"5 of 0 64 0"`。トークンの中で、後ろに `except <values>` を続けることもできます。ほかの
  フィルターは `name=value` です。`block.change` では `dimension` (既定は `overworld`)、`from`、`to` です。ブロック名の
  `minecraft:` は省略できます。範囲は絶対座標です。トリガーには `~` の基準になる Bot もプレイヤーもいないので、`~` と
  Bot を中心とする球 (`"3"`) は拒否されます。座標を書いてください。
- `--` はスクリプトの引数を区切ります。引数は、`/botscript run` が受け取るとおりの文字列として保存します。名前の既定は
  スクリプト名です。トリガーは、`visibility=shared` でない限り非公開です。shared にしても、ほかの人の `list` と `show`
  に表示されるだけで、ほかの人が変更できるようにはなりません。
- コンソールは、ほかの持ち主のトリガーを `<owner>/<name>` で指定します。owner は表示名 (大文字と小文字を区別しない)
  か、持ち主のキーです。プレイヤーは自分のトリガーを `<name>` で指定し、共有されたものは `list` と `show` で
  `<owner>/<name>` と表示されます。

**承認。** 追加や有効化のときは、スクリプトの宣言を表示し、それをスクリプトの版と引数の宣言とともに記録します。
トリガーを有効にできるのは、そのスクリプトを実行できる人だけです。自分のスクリプトなら、宣言が承認した範囲に収まり、
引数の宣言が保存した引数をまだ受け付ける間は、トリガーは現在の版を実行します。ほかの人の共有スクリプトなら、承認した
版だけを実行し、新しい版ができると一時停止します。オペレーターのスクリプトファイルは、自分のスクリプトと同じく
現在の版に従います。始まった実行は、持ち主の権限と承認した宣言の重なる部分だけを持ち、持ち主の同時実行数の上限に
数えます。

| ポリシー | 既定 | 取りうる値 | 効果 |
| --- | --- | --- | --- |
| `concurrency` | `skip` | `skip`、`queue`、`parallel` | `skip` は、このトリガーからの実行が動いているかクールダウン中なら発生を捨て、missed として数えます。`queue` は新しいほうから `queue` 件の発生を保持し、順に始めます。`parallel` は同時に 2 つまでの実行を許します |
| `cooldown` | 20 tick | 0-72000 | 2 回の開始の間の最短時間。`skip` では、その間の発生は遅らせずに捨てます |
| `startsPerMinute` | 10 | 1-60 | 直近 60 秒の窓での開始数。超えるとトリガーは抑制されます: `skip` と `parallel` は発生を missed として捨て、`queue` は窓が開始を許すまで保持します |
| `queue` | 4 | 1-16 | `queue` での保留中の発生の数。あふれると最も古いものを捨て、missed として数えます |

**一時停止と無効化。** トリガーは、スクリプトの宣言が広がったとき (`宣言が広がりました`)、引数の宣言が保存した引数を
受け付けなくなったとき (`引数が無効になりました`)、持ち主がスクリプトを実行できなくなったとき
(`スクリプトを利用できません`)、または実行するほかの人のスクリプトに新しい版ができたとき (`他の人のスクリプトが変更されました`) に一時停止します。
持ち主がもう一度有効にする (再承認になります) まで、一時停止のままです。開始数の上限を超えても、トリガーが止まる
ことはありません。抑制され、再び始まるまでの間に、持ち主にチャットで一度だけ伝えます。一時停止、サーバーによる
無効化、持ち主の実行数の上限による開始の拒否 (開始が成功するまで一度だけ伝えます) も、持ち主にチャットで伝えます。
トリガーの実行が正常に終わったときは、ログに残すだけで、チャットには出しません。持ち主の権限を取り消すとトリガーは
無効になり (`所有者の権限が取り消されました`)、権限を与え直しても有効には戻りません。無効なトリガーは状態を保ち、もう一度有効に
できます。

**再起動。** endbotd は作動中のトリガーを、状態、統計、最後の開始とともに読み込むので、クールダウンは保たれます。
1 分の窓は空から始まり、`lastGap` が空白の期間を記録します。BDS やリンクが再起動してもトリガーは作動中のままですが、
リンクが切れている間は実行を始めません。その間の発生は skipped として数えます。runtime から来る種類は、runtime が
戻ってから再開します。

**上限。** `[limits] max-triggers-per-controller` (既定は 8) が、持ち主ごとの作動中のトリガーの数を制限します。`0` に
すると、サーバーでトリガーが使えなくなります。トリガーの範囲は、監視と同じく `max-watched-blocks-per-controller` に
数えます。ほかの実行の上限は、トリガーの実行にもそのまま当てはまります。

<a id="run-lifetime-and-cleanup"></a>
## 実行の寿命と後片付け

すべての操作は、実行した人の権限と通常のコマンドの監査記録とともに、既存の順序付きコマンドサービスに入ります。
runtime への要求は、どれも改めて認可します。グループの呼び出しは、UUID の固定とメンバーのガードを保ちます。実行は、
ログアウトと再参加を越えて続きます。権限の取り消しは別の権限の版を変えるので、取り消してから与え直しても、古い実行は
生き返りません。オンラインのプレイヤーが必要な操作 (たとえば、そのプレイヤーの名前を指定したテレポート) は、
今までどおりプレイヤーが必要です。オフラインでも続ける必要がある作業には、宣言した絶対位置を使ってください。
ログアウト後の引数なしの spawn は、コントローラーについて記録した最初の位置、ディメンション、向きを使います。

正常終了、失敗やクラッシュ、停止、権限の取り消し、シャットダウンでは、実行がまだ持っている続く入力を止めます。
所有の追跡は、スクリプト、コマンド、グループの一括処理について、Bot の UUID と、移動、アクション、フラグ、採掘の
チャンネルを対象にします。コントローラーが後からそのチャンネルに出した入力は残します。後片付けは、UUID と採掘
ループの ID を固定します。溜めのない 1 回きりのアクションは、続くジョブではありません。続く作業が必要な間、
スクリプトは待たなければなりません。完了したインベントリやワールドの変更は、元に戻しません。runtime との通信が
届かないと、後片付けができないことがあります。その失敗は報告され、オペレーターは `/bot <name> stop` か
`destroy stop` を使えます。独立した外部の runtime クライアントは、このプラグインの入力の所有の追跡の対象外です。
通信が使えないというエラーが起きると、後片付けはメンバーごとにタイムアウトを積み重ねるのではなく、残りの
チャンネルを試すのをやめます。シャットダウン時の後片付けは、既存の上限付きの runtime 制御のタイムアウトを使います。
入力の確認応答が失われたときは、受け付けられたかわからないので、プラグインは慎重を期して、固定した後片付けの所有を
持ち続けます。ほかの呼び出し元からの後の入力は、それでもその所有を置き換えます。
