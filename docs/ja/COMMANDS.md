<!-- source: docs/COMMANDS.md 3fc70c715d27b178c3197f42fe3dc45d8a10fc4d -->

<a id="endbot-commands"></a>
# Endbot のコマンド

コントローラー権限を持つプレイヤーがゲーム内で入力したり押したりできるもの、つまり `/bot`、`/botgroup`、`/botscript`、`/botweb` の説明です。権限はサーバーのオペレーターが与えます ([コントローラーの登録](../OPERATIONS.md#3-controller-enrollment))。ロールと、それぞれができることは [PERMISSIONS.md](../PERMISSIONS.md) にあります。

これらのコマンドの実装は [BOT_CONTROL.md](../BOT_CONTROL.md) にあります。実際のサーバーで確かめた範囲は[検証状況](../COMPATIBILITY.md#bot-operations)にあります。

<a id="quick-start"></a>
## クイックスタート

どのコマンドも対象の Bot を名前で指定します。「選択中の対象」という状態はありません。

```text
/bot Alice spawn
/bot Alice tp me
/bot Alice jump
/bot Alice attack continuous
/bot Alice stop
/bot Alice despawn
```

`/bot` とだけ入力すると、Bot の一覧が開きます。`/bot Alice` は Alice のメニューを、`/bot #group` はグループの操作メニューを開きます ([操作フォーム](#control-forms)を参照)。

<a id="help-list-and-status"></a>
## ヘルプ、一覧、状態

コマンドの一覧、わかっている Bot、1 体の Bot の状態を表示します。

```text
/bot help
/bot help <page>
/bot help <operation>
/bot list
/bot <name> status
```

| コマンド | 表示するもの |
| --- | --- |
| `/bot help` | ヘルプの 1 ページ目 |
| `/bot help <page>` | コマンド一覧の 1、2、3 ページ目。バニラの Bedrock の `/help` にならっています |
| `/bot help <operation>` | その操作で使えるすべての書き方と短い説明。例: `/bot help move` |
| `/bot list` | わかっているすべてのプロファイルと、その接続状態 |
| `/bot <name> status` | runtime の状態。Bot がワールドにいる間は、BDS からのディメンション、位置、向きも |

ヘルプの各ページの内容:

- 1 ページ目: 接続の管理、状態、テレポート
- 2 ページ目: 移動とアクション
- 3 ページ目: インベントリ、ブロック、Bot の設定

ヘルプは 1 行ずつ別のチャットメッセージとして送られるので、Bedrock のチャットで読みやすくなっています。

```text
--- Endbot ヘルプ 1/3: 接続と状態、テレポート ---
/bot ping | /bot list | /bot help - 疎通確認、Bot 一覧、このヘルプ
/bot menu - Bot 操作フォームを開く (プレイヤーのみ)
/bot <name> spawn - Bot を作成または接続する
/bot <name> resume - 場所を変えずに Bot を再接続する
...
次のページ: /bot help 2 - 詳細: /bot help <command>
```

```text
Endbot ヘルプ: move
/bot <name> move forward|backward|left|right|stop
/bot <name> move forward|backward|left|right for <ticks>
移動は押しっぱなしの入力として続き、stop で止まります。for を付けるとその tick 数で止まります。
```

補足:

- `status` の `yaw=` は [0, 360) の範囲で表示します。`look` の結果やスクリプトの `status` の `data.look` と同じです。
- サーバーコンソールでは、`/bot` とだけ入力すると、一覧ではなくヘルプの 1 ページ目を表示します。

<a id="command-results"></a>
## コマンドの結果

Bot についての返事は、エラーや書き方の誤りも含めて、すべて `Endbot: <Bot>: ` で始まり、何を適用したかを伝えます。

```text
/bot Alice move forward       -> Endbot: Alice: 移動を forward にしました
/bot Alice jump               -> Endbot: Alice: jump once を要求しました
/bot Alice use continuous     -> Endbot: Alice: use continuous を始めました
/bot Alice stop               -> Endbot: Alice: 移動とアクションを止めました (sprint と sneak も off)
/bot Alice hotbar 2           -> Endbot: Alice: ホットバーのスロット 2 を選びました
/bot Alice move sideways      -> Endbot: Alice: 不明または正しくない Bot の操作です: move
```

- look の返事には yaw と pitch が、ブロック操作の返事にはブロックと面が入ります。
- 名前の変更や接続の管理の返事は、要求された変更を示します。
- エラーは操作名を示します。例: `Endbot: Alice: move に失敗しました: Bot is offline`。

返事は、入力が適用されたこと、または要求を受け付けたことを確認するもので、結果を示すものではありません。当たり判定、アイテムの使用、届く距離など、ワールドでの結果はすべて BDS が決めます。`status` と実際のワールドの様子で確かめてください。

接続の処理は非同期です。`connecting` は spawn の完了を意味しません。

<a id="control-forms"></a>
## 操作フォーム

よく使う操作は、入力する代わりに Bedrock のメニューから実行できます。

```text
/bot               -> Bot とグループの対象一覧 (プレイヤー)、ヘルプ 1 ページ目 (コンソール)
/bot menu          -> 同じ対象一覧
/bot Alice         -> Alice のメニュー (/bot Alice menu と同じ)
/bot Alice menu    -> Alice のアクションと、状態と位置の短い要約
/bot #miners       -> グループの操作メニュー (/bot #miners menu と同じ)
/bot #miners menu  -> 同じ操作メニューを #miners に適用
```

<a id="the-target-list"></a>
### 対象一覧

一覧は、`/bot` で指定できるものの順に並びます:

1. オンラインの Bot (名前順)。接続中、再試行中、失敗した Bot には、暗い色の `接続`、`再試行`、`失敗` が前に付きます。
2. オフラインの Bot をまとめた `オフライン (n)...` ボタン 1 つ。
3. `#group (count)` の項目。グループの操作画面を直接開きます。
4. `+ ここに新しい Bot`。
5. **グループを管理...**。`/botgroup menu` を開きます。
6. **スクリプト...**。`/botscript menu` を開きます (スクリプトが有効なときだけ)。

グループの管理は `/bot` の操作ではないので、このリンクとしてだけ表示されます。

<a id="a-bots-screen"></a>
### Bot の画面

分類の名前は Minecraft の操作設定にならっています。オンラインの Bot には次のボタンがあります:

| ボタン | 中身 |
| --- | --- |
| すべて止める (stop) | `stop` |
| 移動 | move、jump、スプリント / スニーク |
| 向きを変える (look) | look |
| テレポート | tp |
| ゲームプレイ | 攻撃、アイテムを使う、ブロックを壊す、ブロックを操作、近くのエンティティ (**周囲を確認...**)、エンティティを操作、降りる |
| インベントリ | 持ち物を表示、hotbar、hold、swap、drop |
| Bot | resume / spawn、reconnect、確認つきの despawn、radius と状態 |

- オフラインの Bot では、resume / spawn、radius、Bot の設定の分類が使えます。
- Bot の設定には、名前の変更と確認つきの forget があります。forget はオフラインの Bot にしか使えません。
- グループの画面にも同じ操作があり、メンバーにオンラインとオフラインが混じっていても、全員オフラインでも使えます。失敗は操作ごとに報告されます。名前の変更と forget は 1 体の Bot が対象です。

<a id="mode-forms"></a>
### モードのフォーム

ジャンプ (jump)、攻撃 (attack)、アイテムを使う (use)、ブロックを壊す (destroy) は、最初にコマンドのモードを並べます。`once`、`continuous`、`interval`、`stop` に加えて、アイテムを使う (use) では `for`、ブロックを壊す (destroy) では `x y z` があります。

- パラメーターのないモードは、すぐに実行されます。
- パラメーターのあるモードは、その句だけのフォームを開きます。句の名前は入力するときと同じで、`interval <ticks>`、`hold <ticks>`、`for <ticks>`、それにブロックを壊す (destroy) の continuous のフォームでは `count <blocks>` です。tick は 1 秒に 20 です。
- 任意の句を空欄にすると省略されます。`for` が空欄なら止めるまで続き、`count` が空欄ならブロック数の制限はありません。
- アイテムを使う (use) の `once` は `hold` を受け付けるので、フォームを開きます。
- 歩く (move) は `move <direction> [for <ticks>]` のフォームを 1 つ開きます。短く 1 回だけ動くのは、コマンドの入力に任せています。

例: ためた弓を 3 回撃つには、アイテムを使う (use) で `interval...` を選び、interval 30、hold 20、for 80 にします。

<a id="using-the-forms"></a>
### フォームの使い方

- 入力にはドロップダウン、スライダー、テキスト欄を使います。座標の欄はコマンドと同じ `~` の書き方を受け付け、欄のラベルに `~` が何からの相対かが書いてあります。
- フォームを閉じる、または despawn の確認をキャンセルすると、開く前の画面に戻ります。
- 送信するとフォームが閉じ、いつものチャットの返事が表示されます。続けるには、もう一度 `/bot` を実行してください。
- ボタンやフォームが実行するコマンドは、返事の前に、入力したときの形でチャットに灰色で一度表示されます。例: `/bot Alice use interval 30 hold 20 for 80`。メニューの移動は表示されません。

フォームは、入力したコマンドとまったく同じことをし、権限チェックも返事も同じです。フォームを開いている間に権限を取り消されると、送信したときにいつもの権限の返事が出ます。

高度な `tp ... facing ...` の引数と、ヘルプの詳細ページはコマンドだけです。座標を指定した spawn、ブロックの操作、名前の変更と forget には、フォームの操作があります。

サーバーコンソールはフォームを開けません。コンソールで `/bot menu` を実行すると `/bot help` を案内する返事が出ます。`/bot Alice` のように対象だけを指定したときは、メニューがプレイヤー専用だという返事になります。

<a id="reply-language"></a>
## 返事の言語

返事、ヘルプ、メニューは、英語か日本語で表示されます。

| `endbot.toml` の `[ui] language` | 言語 |
| --- | --- |
| `"auto"` (既定) | 各プレイヤーのゲームクライアントの言語。日本語のクライアントでは日本語、それ以外では英語 |
| `"en"` | すべてのプレイヤーで英語 |
| `"ja"` | すべてのプレイヤーで日本語 |

この設定はオペレーターが決めます ([OPERATIONS.md](../OPERATIONS.md) の 2 節)。

変わるのは言葉づかいだけです。コマンドの単語と書き方は、どの言語でも英語のままです:

- ヘルプは `/bot <name> move forward|backward|left|right for <ticks>` を日本語の説明つきで表示します。
- フォームのモードのボタンや句の欄は、コマンドの名前 (`once`、`continuous`、`interval <ticks>`、`for <ticks>`) のままです。そのため、フォームを使ううちに入力するコマンドを覚えられます。
- フォームのコマンドの灰色の表示も変わりません。

次のものは英語のままです:

- サーバーコンソールとサーバーのログ
- コマンドの入力補完に出る `/bot` の説明
- Endbot runtime から来るメッセージ (例: Bot が見つからない)

続いているジョブからの通知 (連続採掘の終了など) は、そのジョブを始めたプレイヤーの言語で表示されます。

日本語の文章には、かな、漢字、ASCII、日本語の読点と句点だけを使います。Bedrock のチャットのフォントには多くの記号がない (矢印は四角で表示される) ためです。

<a id="mining-blocks"></a>
## ブロックの採掘

ふつうのサバイバルの採掘でブロックを壊します。ブロックに左クリックを長押しするか、視界に入るものを掘り続けます。

```text
/bot <name> destroy [once]
/bot <name> destroy <x> <y> <z> [face]
/bot <name> destroy continuous [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot <name> destroy for <ticks>
/bot <name> destroy count <blocks>
/bot <name> destroy <block type> [continuous] [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot <name> destroy stop
```

| 書き方 | 掘るもの |
| --- | --- |
| `destroy`、`destroy once` | Bot の視線上で 6 ブロック以内にあるブロック 1 つ |
| `destroy <x> <y> <z> [face]` | そのブロック 1 つ (届く位置にあり、さえぎられていなければ)。Bot はそのほうを向きます |
| `destroy continuous` | Bot の視線に沿って次々とブロックを掘ります。届くものがなければ待ちます |
| `destroy for <ticks>` | その tick 数の間の連続採掘 (`continuous for` と同じ) |
| `destroy count <blocks>` | その数のブロックがなくなるまでの連続採掘 (`continuous count` と同じ) |
| `destroy <block type>` | Bot が 6 ブロック以内で届く、その種類のいちばん近いブロック ([対象](#targets-crosshair-coordinates-block-types-and--entities)) |
| `destroy <block type> continuous` (または `for`/`count`) | 連続採掘。1 ブロックごとに、見えているその種類のいちばん近いブロックのほうを向きます |
| `destroy stop` | 採掘を止めます。待機中の連続採掘も止まります |

例:

```text
/bot Alice hotbar 1
/bot Alice destroy continuous for 200 count 64
/bot Alice destroy ~ ~-1 ~ up
/bot Alice destroy acacia_log within 6 except ~ ~-1 ~ count 20
/bot Alice status
```

`destroy` は、`jump`、`attack`、`use` と同じく、モードの単語 `once` (既定)、`continuous`、`stop` を使います。`interval` モードはありません。`continuous` がすでに次のブロックを待つからです。

<a id="before-you-start"></a>
### 始める前に

- 先に `hotbar` で道具を選んでください。採掘は歩きも道具の選択もしません。
- 開始時に、移動、攻撃、アイテムの使用の入力はクリアされます。
- 相対座標は Bot から測ります。サーバーコンソールからのコマンドでも同じです。
- 面は `interact` と同じ名前を使います。

<a id="what-counts-as-in-the-way"></a>
### さえぎるものの扱い

- 草、花、たいまつなど当たり判定のないブロックは、名前で指定したブロックをさえぎりません。ただし照準は、プレイヤーと同じようにそこで止まります。
- 拒否されたときは、間にあるブロックの名前が出ます ([ブロックの狙い方](SCRIPTING.md#targeting-blocks)を参照)。
- 狙いはブロックのマス単位で決まります。ハーフブロックやフェンスも、狙う線を完全なブロックと同じように止めます。一方 `blocks` では、それらは見通せるものとして扱われます。

<a id="continuous-mining"></a>
### 連続採掘

`destroy continuous` は、Bot の今の視線の方向に左クリックを押し続けます。1 ブロック壊すたびに、同じ線上で次に届くブロックを選びます。Bot はその場にとどまり、範囲を掘り進めることはしません。

6 ブロック以内に何もないとき、または岩盤のような壊せないブロックが前にあるときは待ちます。届くブロックを視線の先に置くと、採掘は自動で再開します。

<a id="ending-after-a-time-or-a-number-of-blocks"></a>
### 時間やブロック数で終える

| 句 | 採掘が終わるとき | 終わったときのメッセージ |
| --- | --- | --- |
| `for <ticks>` | コマンドからその入力 tick 数が過ぎたとき。壊している途中のブロックはやめます | `Endbot: Alice: destroy continuous が止まりました: 指定の時間が終わりました` |
| `count <blocks>` | その数のブロックを壊したとき。壊している途中のブロックは最後まで壊します | `... が止まりました: 指定のブロック数に達しました` |

- どちらの値も 1–72000 です。
- `for` と `count` はどちらの順でも組み合わせられ、先に来たほうで採掘が終わります。
- `count` は BDS が壊れたと報告したブロックを数えます。そのため、ブロックの間の待ち時間は数に入らず、サーバーの負荷や TPS にも左右されません。`for` は実時間の入力 tick です。
- これらのメッセージは info レベルで、採掘を始めたプレイヤーに届きます。
- 1 ブロックの `destroy` と座標指定には、`for` も `count` も付けられません。

<a id="status"></a>
### 状態

最初の返事は対象を示します。例: `Endbot: Alice: 1 64 2 の minecraft:stone を壊しています。結果は status で確認できます`。完了のメッセージは送られません。採掘の状態は `status` で表示されます:

```text
1 64 2 の minecraft:stone を採掘中                                   (今壊している)
1 64 2 の minecraft:stone を採掘中 (continuous, 12/64 ブロック)      (destroy count)
destroy continuous は視線の先のブロックを待っています (前回: ..., 3 ブロック)  (destroy continuous、届く範囲に何もない)
前回の destroy: ブロックが壊れたか変わりました (1 64 2 の minecraft:stone)  (完了)
前回の destroy: 指定のブロック数に達しました (64/64 ブロック, 1 64 2 の minecraft:stone)  (count に到達)
前回の destroy: 失敗: 対象に届きません (...)                         (失敗)
前回の destroy: テレポートで中止 (...)                               (中止)
```

- `for` の実行中は、`status` に残りの tick 数が表示されます。
- 終わった実行は合計を残します。例: `前回の destroy: 指定の時間が終わりました (137 ブロック, 1 64 2 の minecraft:stone)`。
- 終わったジョブは、次のジョブが始まるまで `前回の destroy` として表示されたままです。

<a id="what-bds-decides"></a>
### BDS が決めること

ブロックを壊すのは BDS です。硬さと道具による速さ、ドロップ、耐久値、破壊を取り消すプラグインも BDS 側の処理です。Endbot がブロックを空気に置き換えたり、`/setblock` を実行したり、オペレーター権限を与えたりすることはありません。

1 ブロックの採掘は、BDS がブロックの変化を報告したときに終わります。そのため、置き換えやほかのプレイヤーによる変更でも終わります。連続採掘は次を選び直します。Endbot は、すぐに壊れることやドロップを予測せず、サーバーを待ちます。

拒否された対象や保護された対象は、止めるまで、または 12000 入力 tick のタイムアウト (20 TPS で約 10 分) まで続くことがあります。

<a id="other-commands-while-mining"></a>
### 採掘中のほかのコマンド

- `move`、`look`、`attack`、`use`、`drop`、`interact` では採掘は止まりません。連続採掘は、次のブロックを新しい視線の方向で選びます。
- ホットバーのスロットや手に持つアイテムが変わる (拾った、道具が壊れた) と、今のブロックを新しいアイテムでやり直します。
- 採掘中に攻撃やアイテムの使用をすると、両方の入力を送ります。結果は BDS が決めます。

テレポート、ディメンションの移動、対象が届かない位置になること、10 分のタイムアウトのどれかで、今のブロックは終わります。1 ブロックの採掘はそこで終わり、連続採掘は次の対象を選びます。

<a id="when-mining-ends"></a>
### 採掘が終わるとき

採掘が終わるのは次のときだけです:

- `destroy stop` (採掘だけ) または `stop` (採掘とすべての移動、アクション)
- `for` の時間の終了、または `count` への到達
- 死亡または切断
- 始めたプレイヤーの権限の取り消し
- プラグインの無効化
- Endbot のサーバー側が 200 入力 tick (約 10 秒) 応答しないとき

連続採掘は、始めたプレイヤーがログアウトした後も続きます。採掘は保存されず、再接続しても再開しません。

開始と停止はコマンドの返事で確認できます。採掘がほかの理由 (1 ブロックの失敗、死亡、切断、権限の取り消し、runtime との接続が失われた) で終わると、始めたプレイヤーにチャットのメッセージが 1 回届きます。例: `Endbot: Alice: destroy continuous が止まりました: 死亡で中止`。壊せたブロックは通知されません。

オペレーター向け: 開始と停止はサーバーコンソールに info レベルで、それ以外の終わり方は warning レベルで記録されます (プレイヤーがオフラインでも記録されます)。ログは英語のままです。

`attack` はエンティティへの攻撃です。ブロックには `destroy` を使います。名前はゲームの「攻撃/破壊」の操作にならっています。

<a id="linking-the-web"></a>
## ウェブとの連携

ゲームから、自分のアカウントを Endbot Web に連携します。

```text
/botweb link     Endbot Web 用のコード: 6 文字、有効 5 分、1 回のみ
/botweb status   このプレイヤーのウェブアカウントの有無と、パスキーとセッションの数 (既定)
/botweb unlink   このプレイヤーのウェブアカウント、パスキー、セッションを削除
/botweb help     この一覧
```

- 連携できるのは、権限があって Xbox で認証したプレイヤーだけで、自分のアカウントだけです。
- サーバーでウェブが有効になっている必要があります。
- 新しいコードを出すと、前のコードは使えなくなります。
- 連携を解除しても、コントローラー権限はそのままです。
- ウェブのテレポートにある「自分の位置へ」は `tp me` です。ゲームにオンラインでいる間に使えます。

ウェブの仕組み: [WEB.md](../WEB.md)。

<a id="lifecycle"></a>
## 接続の管理

Bot の作成、接続、切断、名前の変更、削除をします。

```text
/bot <name> spawn
/bot <name> spawn at <x> <y> <z>
/bot <name> spawn at <x> <y> <z> facing <yaw> <pitch>
/bot <name> spawn at <x> <y> <z> in <dimension>
/bot <name> resume
/bot <name> reconnect
/bot <name> despawn
/bot <name> forget
/bot <name> rename <new-name>
/bot <name> visibility shared|private
/bot <name> owner <GamerTag>
```

| 操作 | すること |
| --- | --- |
| `spawn` | 必要ならプロファイルを作り、Bot をあなたの場所に置きます。`at` を付けると座標に置きます。既存のオフラインのプロファイルは、UUID はそのままで、指定した場所に置きます |
| `resume` | 既存のプロファイルを移動せずに再接続します。BDS に保存されたプレイヤーの位置が優先されます |
| `reconnect` | ID も場所も変えずに切断して再接続します |
| `despawn` | Bot を意図してオフラインにし、自動の再接続を取り消します |
| `forget` | オフラインの Bot の Endbot の登録を削除します。BDS のプレイヤーデータは残ります |
| `rename` | Bot の名前を変えます。オンラインの Bot は UUID を保ち、ログイン名を変えるために再接続します |
| `visibility` | Bot を `shared` か `private` にします |
| `owner` | コンソールだけ: Bot をプレイヤーに割り当てます |

例:

```text
/bot Alice spawn at 100 64 -20 in nether
/bot Alice rename Miner_1
```

<a id="names"></a>
### 名前

- ASCII の英字、数字、アンダースコアを 1–16 文字使います。
- 大文字と小文字は区別しません。`alice` と `Alice` は重複します。
- オンラインの実プレイヤーの名前は拒否されます。Endbot はオフラインのプレイヤーの GamerTag をすべて調べられるわけではないので、知っている実プレイヤーの名前は避けてください。
- `help`、`list`、`menu`、`ping` (大文字と小文字は問いません) は予約されています。`/bot help`、`/bot list`、`/bot menu`、`/bot ping` が使うためです。これらの名前で作ることも、これらの名前に変えることも、コマンド、フォーム、スクリプトのどれからでも拒否されます。すでにこの名前を持つ Bot はそのまま使えます。`/bot list` とそのメニューはそれに印を付け、`rename` を勧めます。

<a id="spawn-notes"></a>
### spawn の補足

- サーバーコンソールから座標なしで `spawn` すると、置く場所は BDS が決めます。新しい Bot はワールドのスポーン地点に、既存の Bot は前回いた場所に現れます。場所を指定するには `spawn at <x> <y> <z> in <dimension>` を使います。
- 座標とディメンションは、何かを作る前に確かめます。最初の spawn では、オンラインの実プレイヤーの名前を拒否します。既存の Bot は、自分の UUID と名前をそのまま使えます。拒否された spawn は何も変えません。
- spawn がワールドに届かなかったときは、`resume`、`reconnect`、`despawn`、`forget`、`rename` で保留中の配置が破棄されます。そのため、その配置が後で使われることはありません。

<a id="limits"></a>
### 上限

`spawn`、`resume`、`reconnect` は、オペレーターの `[limits]` に従います ([設定](../OPERATIONS.md#2-configuration))。

- 人間のプレイヤー用に確保された枠を Bot が使うことになるとき、または `max-bots` を超えるときは、今の数を示して拒否されます。
- グループやスクリプトでは、該当するメンバーだけが失敗します。
- `despawn` と `forget` は制限されません。
- 上限を下げても、すでにオンラインの Bot はオンラインのままです。

<a id="visibility-and-owner"></a>
### 公開範囲と持ち主

- `visibility`、`forget`、`rename` は、Bot の持ち主かコンソールだけが使えます。
- 持ち主のいない Bot は共有のままです。`visibility` は拒否され、コンソールの `owner` コマンドが案内されます。そのため、プレイヤーが古い Bot を非公開にして自分のものにすることはできません。
- `owner` (コンソールだけ) は、Bot を指定したオンラインのプレイヤーかコントローラーに割り当てます。公開範囲はそのまま保ちます。記録がなかった Bot には、設定の既定値を使います。

<a id="teleport"></a>
## テレポート

Bot をプレイヤーのところ、座標、別のディメンションへ移します。

```text
/bot <name> tp me
/bot <name> tp <player>
/bot <name> tp <x> <y> <z> [<yaw> <pitch>]
/bot <name> tp <x> <y> <z> facing <player>
/bot <name> tp <x> <y> <z> facing <x> <y> <z>
/bot <name> tp in <dimension> <x> <y> <z>
```

- ディメンション: `overworld`、`nether`、`end` (Minecraft の名前空間付き ID に対応します)、または名前空間付き ID をそのまま書きます。
- 行き先と `facing` の `~` は、同じ時点から測ります。
- テレポートはバニラの `/tp` を使わず、チートの権限も必要ありません。

例:

```text
/bot Alice tp me
/bot Alice tp 100 64 -20 facing Steve
/bot Alice tp in nether 100 64 -20
```

空中にテレポートされた Bot は、ふつうに落ちて着地します。

<a id="chunk-loading-range"></a>
## チャンクの読み込み範囲

Bot の周りの何チャンクをサーバーが送るかを設定します。

```text
/bot <name> radius <chunks>
/bot <name> radius
/bot <name> radius default
```

| 書き方 | すること |
| --- | --- |
| `radius <chunks>` | この Bot の範囲を 2..32 チャンクに設定します |
| `radius` | 要求した値、その出どころ、サーバーの答えを表示します |
| `radius default` | Bot ごとの値を外します。共通の設定がまた使われます |

例:

```text
/bot Idle radius 5
/bot Explorer radius 12
/bot Alice radius         -> Endbot: Alice: チャンク半径 12 (この Bot の設定)、サーバ確認済み 10
```

- Bot ごとの値がなければ、`endbot.toml` の `[runtime].chunk-radius` が使われます (既定は 5)。
- 変更はその Bot だけに効き、オンラインなら再接続せずにすぐ反映されます。
- オフラインの Bot も設定できます。`オンライン時に反映` と表示されます。
- Bot ごとの値は、再接続、名前の変更、runtime の再起動の後も残ります。
- 変更の直後は、返事が `サーバの確認待ち` になります。もう一度問い合わせる (または `status` を使う) と、確定した範囲が表示されます。

止まっている Bot には小さい範囲を、探索する Bot には大きい範囲を使ってください。BDS はどちらの要求も調整することがあります。今の基準の組み合わせでは、3 の要求が 5 になりました。

この範囲は、サーバーのシミュレーション距離を変えません。また、Bot を動かしたり、地形の生成を保証したりもしません。[オペレーター向けの設定](../OPERATIONS.md)を見てください。

<a id="inputs-and-actions"></a>
## 入力とアクション

```text
/bot Alice move forward|backward|left|right|stop
/bot Alice look <yaw> <pitch>
/bot Alice look at <x> <y> <z>
/bot Alice move forward|backward|left|right for <ticks>
/bot Alice jump|attack|use [once|continuous|interval <ticks>|stop]
/bot Alice jump|attack|use [continuous|interval <ticks>] for <ticks>
/bot Alice use for <ticks>
/bot Alice use hold <ticks> [interval <ticks>] [for <ticks>]
/bot Alice sprint on|off
/bot Alice sneak on|off
/bot Alice inventory
/bot Alice hotbar
/bot Alice hotbar <1-9>
/bot Alice swap <a> <b>
/bot Alice hold <item>
/bot Alice interact [<x> <y> <z> [face] | <block type> [within <area>] [except <area>] | @<type|id|nearest>]
/bot Alice attack @<type|id|nearest> [once|continuous|interval <ticks>|stop] [for <ticks>] [count <n>]
/bot Alice interact @<type|id|nearest> [once|continuous|interval <ticks>|stop] [for <ticks>] [count <n>]
/bot Alice interact stop
/bot Alice destroy <block type> [continuous] [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot Alice destroy continuous [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot Alice entities
/bot Alice entity [@<type|id|nearest>]
/bot Alice target
/bot Alice block <x> <y> <z>
/bot Alice blocks <type> [limit <1-64>] [within <area>] [except <area>]
/bot Alice dismount
/bot Alice drop
/bot Alice drop stack
/bot Alice drop <1-36|offhand|head|chest|legs|feet>
/bot Alice drop <item> [count]
/bot Alice drop all
/bot Alice stop
```

`/bot` のどこでも、座標はバニラの詰めた相対の書き方を受け付けます。`~~-1~` は `~ ~-1 ~` です。

<a id="moving-sprinting-and-stopping"></a>
### 移動、スプリント、停止

移動は、止めるまでキーを押しっぱなしにした入力として続きます。

```text
/bot <name> move forward|backward|left|right|stop
/bot <name> move forward|backward|left|right for <ticks>
/bot <name> sprint on|off
/bot <name> sneak on|off
/bot <name> stop
```

- `stop` は、移動、予定されたアクション、スプリント、スニークをクリアし、採掘を終えます。Bot は接続したままです。
- `look` は `stop` の後も最後の向きのままです。

<a id="looking"></a>
### 向きを変える

Bot をある角度、またはある地点のほうへ向けます。

```text
/bot <name> look <yaw> <pitch>
/bot <name> look at <x> <y> <z>
```

| 値 | 意味 |
| --- | --- |
| yaw | 単位は度。0 が南 (+Z)、90 が西 (-X)、180 が北、270 が東。どんな値も 360 で割った余りとして扱います |
| pitch | -90 (真上) から 90 (真下) |

- `look` は、BDS が新しい向きを報告した時点 (1 度以内) で答えます。そのため、直後に送った `attack`、`use`、`destroy`、`interact` は新しい視界に対して動きます。
- BDS が約 0.5 秒 (20 TPS で 10 tick) 以内に報告しないと、`look` はタイムアウトで失敗します。それでも Bot は向きを変えていることがあります。
- `look <yaw> <pitch>` を入力したときの返事は、指定した角度をそのまま返します。向きを変える (look) のフォームのスライダーは -180 から 180 で、どの値も 360 で割った余りとして適用されます。

<a id="jump-attack-and-use"></a>
### ジャンプ、攻撃、使う

アクションを 1 回押す、押し続ける、一定の間隔で繰り返す、のどれかを選べます。

```text
/bot <name> jump|attack|use [once|continuous|interval <ticks>|stop]
```

| モード | 意味 |
| --- | --- |
| `once` (既定) | 1 回押す |
| `continuous` | 入力 tick ごと (1 秒に 20 回) |
| `interval <ticks>` | その tick 数ごと。1–72000 |
| `stop` | このアクションを止める |

例: `/bot Alice attack interval 20` は 1 秒に 1 回攻撃します。

`use` は、食べ物、弓、トライデントのように、手に持ったアイテムを空中で使います。手が空なら何も起きません。Endbot がアイテムを作り出したり、サーバーコマンドを実行したりすることはありません。

<a id="holding-use"></a>
### use を押し続ける

`use continuous` は、`use stop` か `stop` まで右クリックを押し続けます。たとえば、トライデントをためて投げたり、弓を撃ったりするときに使います。

- 食べるには、バニラの食べる条件が必要です。
- `use once` と `use interval` は短く押すだけです。食べたり、アイテムを最大までためたりするほど長くは押しません。
- ホットバーのスロットを切り替えると、前のアイテムを離します。

`use for <ticks>` は、1–72000 入力 tick の間 use を押し続けてから離します。たとえば `/bot Alice use for 40` は 2 秒押して離す動作を 1 回し、弓やトライデントをためるのに十分です。

- 押す時間は最初の使用から数えます。ブロックの操作が保留中なら、時間を消費せずに待ちます。
- 最初の使用の前に止めると、離す操作なしで取り消されます。
- 最初に手が空なら何も起きません。後で拾うアイテムを待つこともしません。
- 返事は要求を確認するもので、撃てたことや食べたことを示すものではありません。これは時間の指定です。食べ物は、早く食べ終わることも、長く押すと繰り返すことも、バニラの条件を満たさずに失敗することもあります。サーバーの遅れも結果に影響します。
- `use stop` と `stop` は早めに離すので、ためたアイテムが発射されることがあります。
- ホットバーのスロットを選ぶと、離して取り消されます。死亡、ディメンションの移動、切断では破棄されます。
- 新しい時間指定の use は前のものを置き換え、押す時間と期限を新しくします。
- 終わっても、移動やほかの入力は続きます。再接続の後に再開するものはありません。

<a id="durations-for-and-charged-presses-hold"></a>
### 時間の指定 (`for`) とためる押し方 (`hold`)

`for <ticks>` は、入力をどれだけ続けるかを指定します。すべてのアクション、移動、連続採掘で使えます。`hold <ticks>` は、use の 1 回ごとの押下をためる押し方にします。

```text
/bot Alice move forward for 40                 2 秒間前に歩いて止まる
/bot Alice jump for 100                        100 tick の間、毎 tick ジャンプ (jump continuous for 100 と同じ)
/bot Alice attack interval 20 for 200          200 tick の間、20 tick ごとに攻撃
/bot Alice use for 40                          40 tick use を押し続けて離す (従来どおり)
/bot Alice use hold 30                         30 tick ためて、1 回離す
/bot Alice use hold 30 for 200                 30 tick ためて離す動作を、200 tick の間続けて繰り返す
/bot Alice use hold 30 interval 40 for 200     200 tick の間、40 tick ごとに 30 tick のためを始める
/bot Alice destroy for 200                     200 tick の間、連続採掘
/bot Alice destroy count 64                    64 ブロック壊すまで連続採掘
```

ルール:

- 値はすべて 1–72000 入力 tick (1 秒に 20) です。
- モードがなければアクションは `once` で、`for` があれば `continuous` です。
- `once ... for` と `stop ... for` は拒否されます。1 回押すだけのものに長さはないからです。
- `interval`、`hold`、`for` はどの順で書いてもよく、それぞれ 1 回までです。`interval` は `once`/`continuous` の代わりになります。

`for` の動き:

- コマンドから (次の入力 tick から) 数えるので、入力はちょうどその tick 数だけ続きます。
- 例外: 単純な `use for <ticks>` / `use continuous for <ticks>` は最初の使用から数えるので、押すのが遅れてもためる時間は減りません ([use を押し続ける](#holding-use)を参照)。
- 終わると、その入力だけが止まります。移動、ほかのアクション、スプリント / スニーク、採掘は続きます。
- 同じ入力への新しいコマンドは期限を置き換え、`for` のないコマンドは期限を外します。
- `stop`、`<input> stop`、死亡、切断で早めに終わります。
- `status` は、自分で終わる入力を残りの tick 数とともに表示します。例: `時間指定: move forward 残り 30 tick, use interval 40 hold 30`。

`interval` での手に持つアイテムの扱い:

- `interval` のアクションは、最初に使ったアイテムより長く続きます。スタックを使い切ったり (ポーションを飲んだ)、手が空になったりしても、終わるのは進行中の押下だけです。次の発動では、そのとき手に持っているものを使います。
- 手が空のときの発動は何も送らず、次の間隔を待ちます。
- `once`、`continuous`、単純な `use for` は今までどおり、アイテムを使い切ったり、ホットバーのスロットが変わったりすると取り消されます。

`hold` の動き (use だけ):

- 1 回押すたびに `hold` tick の間 use を押し続けてから離します。`use for` と同じなので、弓やトライデントを繰り返し撃てます。
- `hold` だけなら 1 回ためます。
- `hold ... for` (または `continuous hold`) は、離した直後に次のためを始めます。
- `hold ... interval <ticks>` は、interval の tick ごとにためを始めます。離す操作がそれぞれ自分の tick を持てるように、interval は hold より長くする必要があります。
- ためている途中で `for` の期限が来ると、すぐに離すので、ため切っていないアイテムが発射されることがあります。避けるには、押す周期の倍数を選んでください。
- ホットバーの選択、手に持つアイテムが変わるか空になること、ディメンションの移動、死亡、切断で hold は取り消されます。再接続の後に再開することはありません。
- 離すたびに確認されるのは入力だけで、撃てたことではありません。

`count <blocks>` は、`for` に対応する `destroy` だけの条件です。[ブロックの採掘](#mining-blocks)を見てください。ほかの入力でのアイテム個数の条件はありません。

ジャンプ (jump)、攻撃 (attack)、アイテムを使う (use)、歩く (move)、ブロックを壊す (destroy) のフォームでも、同じ句を使えます ([操作フォーム](#control-forms)を参照)。

<a id="inventory-and-hotbar"></a>
### インベントリとホットバー

Bot の持ち物と、手に持っているスロットを確かめます。

```text
/bot <name> inventory
/bot <name> hotbar
/bot <name> hotbar <1-9>
```

スロットはゲーム画面と同じ番号で、持ち物のコマンドはすべて次のラベルを使います:

| ラベル | スロット |
| --- | --- |
| 1–9 | ホットバー |
| 10–36 | メインの持ち物 |
| `offhand`、`head`、`chest`、`legs`、`feet` | オフハンドと防具 |

例: `inventory` は `3: diamond_pickaxe x1, 10: stone x64` のようにスロットを表示します。

- `inventory` は空でないスロットだけを、`minecraft:` を除いた ID と個数で表示します。
- 道具と防具は、残りと最大の耐久値を表示します。例: `iron_pickaxe x1 (210/250)`。
- 持ち物が空なら `持ち物は空です` と表示されます。装備だけがあるときは、その装備が表示されます。
- `inventory` は BDS を直接読み、何も変えません。フォームでは **インベントリ... → 持ち物を表示** で実行できます。
- `hotbar` は選択中のスロットとホットバーのアイテムを表示し、`hotbar <1-9>` はスロットを選びます。
- オフラインの Bot は、ワールドにいないと返します。通常の Xbox プレイヤーは持ち物の対象にできません。

拾ったアイテムは自動で反映されます。通常は約 0.2 秒以内です。そのため、再接続しなくても `hotbar`、`drop`、手に持つアイテムを使うアクションで使えます。選択中のスロットが空か不明なら、`Selected hotbar slot is empty` で失敗します。拾ったアイテムが見当たらず、サーバーに対応する Endbot プラグインがないときは、Bot を再接続すると戻ります。

<a id="swap-and-hold"></a>
### swap と hold

スタックを並べ替えたり、アイテムを手に持たせたりします。

```text
/bot <name> swap <a> <b>
/bot <name> hold <item>
```

`swap <a> <b>` は、スロット 1–36 の 2 つのスタック全体を交換します。

- 片方が空なら移動になります。同じスロットどうしと、両方とも空のスロットはエラーです。
- オフハンドと防具は対象外です。装備する操作はありません。
- 例: `/bot Alice swap 10 2` はメインとホットバーのスロットを交換し、`/bot Alice swap 1 36` はホットバーのスタックを空のメインスロットに移します。

`hold <item>` は、ID の完全一致で探します (`diamond_pickaxe` または `minecraft:diamond_pickaxe`)。

- ホットバーにあれば、`hotbar <n>` と同じように、一致する最小番号のホットバースロットを選びます。
- なければ、一致する最小番号のメインスロットを、選択中のホットバースロットと交換します。そのスロットにあったスタックはメインスロットに移ります。
- アイテムがないときは、指定した ID を示して報告します。

返事は結果ではなく要求を伝えます。例: `hold diamond_pickaxe: ホットバー 3 の選択を要求しました`、`hold diamond_pickaxe: スロット 14 からホットバー 1 への移動を要求しました`。

フォームでは、**インベントリ... / アイテムを手に持つ (hold)...** がアイテム ID を受け付け、**スロットを交換する (swap)...** には 1-36 のドロップダウンが 2 つあります。どちらも、アイテムの接頭辞も含めて、入力したときの形のコマンドを表示します。グループを対象にしても swap と hold を使えます。

<a id="dropping-items"></a>
### アイテムを落とす

通常の Bedrock の持ち物の操作で、アイテムを落とします。

```text
/bot <name> drop
/bot <name> drop stack
/bot <name> drop <1-36|offhand|head|chest|legs|feet>
/bot <name> drop <item> [count]
/bot <name> drop all
```

| 書き方 | 落とすもの |
| --- | --- |
| `drop` | 選択中のアイテム 1 個 |
| `drop stack` | 選択中のスタック |
| `drop <slot>` | そのスロットのスタック全体 (スロットのラベルは `inventory` と同じ) |
| `drop <item>` | その ID に完全一致するすべてのスタック (`stone` または `minecraft:stone`) |
| `drop <item> <count>` | その数まで (1-10455)。スロット番号の小さい順、その後に offhand/head/chest/legs/feet の順 |
| `drop all` | 空でないすべてのスロット。オフハンドとすべての防具スロット (ヘルメットも) を含みます |

返事の例: `スロット 12 の stone x5 を落とすよう要求しました`、`41 スロットの drop all を要求しました`。見つかったアイテムが指定より少ないときは、両方を示します。例: `stone x3 を落とすよう要求しました (指定個数 5)`。

- 1-36 の数字と装備の名前はスロット、`all` と `stack` はキーワード、それ以外はアイテムです。キーワードやスロット名と同じ名前のアイテムには、`minecraft:` を付けてください。
- 個数はアイテムにだけ使えます。
- 返事は「完了した」ではなく「要求した」と伝えます。それぞれの drop は BDS が確かめます。BDS が拒否したときは、表示される持ち物が自動で直ります。これには約 1 秒かかることがあります。
- フォームでは、**インベントリ... / アイテムを捨てる (drop)...** に、1 個かスタック、スロット、アイテムと任意の個数、すべて落とすがあります。フォームは drop all の前に確認を求めます。入力したコマンドはすぐに実行されます。
- グループを対象にしても同じ書き方を使います。

<a id="interacting-with-blocks"></a>
### ブロックの操作

`interact` は、ブロックの面への通常の右クリックです。

```text
/bot <name> interact
/bot <name> interact <x> <y> <z> [face]
/bot <name> interact <block type> [within <area>] [except <area>]
```

届く距離、可否、当たり判定、設置、アイテムの使用は BDS が決めます。そのため、手に持ったアイテムが置けるものなら、Endbot がワールドを編集しなくても、サバイバルでブロックを置けます。対象の指定は[対象](#targets-crosshair-coordinates-block-types-and--entities)で説明しています。

`use` と `interact` の違い: `use` は手に持ったアイテムを空中で使い、`interact` はブロックやエンティティを右クリックします。

<a id="areas-within-and-except"></a>
### 範囲: `within` と `except`

1 つの範囲の値で、操作が選べるブロックを絞り込みます。`within <area>` は範囲の中のブロックだけを残し、`except <area>` は範囲の中のブロックを外します。どちらも同じ形を受け付け、値の数で形が決まります:

| 範囲 | 形 | 例 |
| --- | --- | --- |
| `<x> <y> <z>` | 1 ブロック | `except ~ ~-1 ~` (Bot の足元のブロック) |
| `<x1> <y1> <z1> <x2> <y2> <z2>` | 直方体。`/fill` と同じく両方の角を含む | `within ~-3 ~ ~-3 ~3 ~10 ~3` |
| `<r>` | Bot を中心とする半径 `r` の球 | `within 6` |
| `<r> of <x> <y> <z>` | ある地点を中心とする半径 `r` の球 | `within 4 of 10 70 -3` |

範囲は、`blocks <type>`、`destroy <block type>`、`destroy continuous` (`for`/`count` による場合も)、`interact <block type>` に使えます。座標、照準の 1 ブロック、`@` のエンティティには範囲を付けられません。

ルール:

- 範囲は候補を絞るだけです。届く距離や視線を広げることはありません。`destroy` と `interact` は 6 ブロックの届く距離と視線のままで、`blocks` が Bot から 16 ブロックより先を見ることはありません。
- `~` (と `<r>` の中心) は、コマンドを受け付けた時点の Bot から測ります。そのため、連続採掘は Bot が動いても同じ範囲を使います。
- `within` は最大で半径 16 の球 (17157 ブロック) までです。それより大きい直方体は拒否されます。`except` には上限がありません。
- 範囲がなければ、`blocks` は Bot から半径 8 の球を探し、入力した `destroy` と `interact` は届くものすべてから選び、視線での連続採掘は照準の先にあるものを掘ります。
- Bot の足場は特別扱いしません。`except ~ ~-1 ~` で守れます。
- チャットでは、`within` と `except` はそれぞれ 1 回まで、操作の後ならどこにでも書けます。Luau のスクリプトでは、`area:minus()` で複数の除外をつなげられます ([スクリプトでの範囲](SCRIPTING.md#areas)を参照)。
- 連続採掘は、対象を選び直すたびに範囲を当てはめます。照準が範囲の外にある視線での連続採掘は、何も見えていないときと同じように待ちます。
- ほかの個数の値は書き方の誤りです。今後の形は、先頭に形の単語を付けます (`within cylinder ...`)。
- `blocks ... radius` と `blocks ... at` は、`within <r>` と `within <r> of <x> <y> <z>` に置き換わりました。古い書き方は、新しい書き方を示すメッセージで失敗します。

`/bot help area` で、ゲーム内にこの表を表示できます。

<a id="targets-crosshair-coordinates-block-types-and--entities"></a>
### 対象: 照準、座標、ブロックの種類、`@` のエンティティ

左クリックと右クリックの動詞は別々のままですが (`attack`/`destroy`、`use`/`interact`)、対象は共通の書き方を使います:

| 書き方 | 対象 | 例 |
| --- | --- | --- |
| なし | Bot が見ているもの | `interact`、`destroy`、`attack` |
| `<x> <y> <z> [face]` | そのブロック。`~` は Bot からの相対。面がなければ Bot に向いた面 | `interact ~~-1~`、`destroy 1 64 2 up` |
| `<block type>` | Bot が 6 ブロック以内で届く、その種類のいちばん近いブロック | `interact chest`、`destroy oak_log` |
| `@<type\|id\|nearest>` | 一致するプレイヤー以外のいちばん近いエンティティ (`interact` は 6 ブロック以内、`attack` は 4.5 以内) | `interact @cow`、`attack @zombie`、`interact @9` |

`@` はバニラのエンティティセレクターの記号にならったもので、`entity @cow` や id (`interact @9`) も含め、すべてのエンティティで必要です。一覧では id をこの形で表示し (`cow @9 (3.4m)`)、スクリプトのデータでは `selector` として運びます。

エンティティを対象にした繰り返し:

- `attack @zombie continuous|interval <ticks>|for <ticks>` は、発動のたびにいちばん近い一致を狙い、届く範囲にいなければ何もしません。照準の先に切り替えることはないので、そこにいるプレイヤーを叩くことはありません。
- `interact @cow count 5` (または `interval`/`for`) は、この実行でまだクリックしていない一致するエンティティを右クリックし、いなければいちばん前にクリックしたものを右クリックします。そのため、群れに 1 頭ずつえさをやれます。
- `count` は、エンティティに送ったクリックの回数を数えます。結果は BDS が決めます (すでに繁殖モードの牛は何も食べません)。
- クリックのたびに、次のクリックは、BDS が手に持つアイテムの変化 (小麦を食べた、剣がすり減った) を報告するまで、最大 10 tick 待ちます。古い手持ちのアイテムのままのクリックは拒否されるからです。
- `once` は 1 回のクリックです。`attack @cow stop`、`interact stop`、`stop` で実行が終わります。
- `status` は、実行中の対象と残りのクリック数を表示します。例: `interact @cow continuous 残り 2 回`。

ブロックの種類:

- `destroy oak_log count 10` (または `continuous`/`for`) は連続採掘です。1 ブロックごとに、見えているその種類のいちばん近いブロックのほうを向き、届く範囲になければ待ちます。歩くことはありません。
- 不明なブロックの種類はそのように報告され、`destroy @…` は `attack` を案内します。

Luau では `bot:interact("@cow")`、`bot:interact("chest")`、`bot:destroy("oak_log")`、`bot:attack("@zombie")` です。

<a id="looking-around"></a>
### 周囲の確認

Bot の近くに何があるかを調べます。どれも BDS を読むだけで、何も変えません。

```text
/bot <name> target
/bot <name> block <x> <y> <z>
/bot <name> blocks <type> [limit <1-64>] [within <area>] [except <area>]
/bot <name> entity [@<type|id|nearest>]
/bot <name> entities
```

| コマンド | 表示するもの |
| --- | --- |
| `target` | 照準が 6 ブロック以内で当たるもの。ブロック (位置、面、種類) とプレイヤー以外のエンティティのうち、近いほう |
| `block <x> <y> <z>` | 1 つのブロックの種類と状態、そして Bot が今それを狙えるか (届く、届かない、または何がさえぎっているか) |
| `blocks <type>` | 見えているその種類のいちばん近いブロックを `x y z (distance)` の形で、近い順に。今狙えるものには `届く` の印が付きます |
| `entity [@...]` | 16 ブロック以内のエンティティ 1 体の詳細 (id、距離、位置、体力、名札)。引数がなければ、いちばん近いもの |
| `entities` | 16 ブロック以内のプレイヤー以外のエンティティを、近い順に最大 10 件。`<type> @<id> (<distance>m)` の形 |

例:

```text
/bot Alice blocks oak_log within 5 of ~ ~8 ~
```

`blocks` の補足:

- Bot から半径 8 の球か、指定した[範囲](#areas-within-and-except)の中を探します。Bot から 16 ブロックより先は探しません。
- 最大 `limit` 件 (既定は 10、最大 64) を表示します。
- 数えるのは、プレイヤーに見えるブロックだけです。つまり、空気、液体、植物、葉、ガラス、氷、またはハーフブロックやフェンスのような部分的な形に面が接しているものです。埋まっている鉱石はわかりません。
- 高い構造物は、例のように、大事な高さの周りの範囲で探してください。

そのほかの補足:

- `~` は Bot からの相対です。`block` は 30000000 を超える座標を拒否します。
- `target`、`block`、`blocks` はスクリプトにもデータを返します。
- グループを対象にすると、`status` と同じく各メンバーの結果を表示します。履歴や監査の記録は残りません。
- ゲームプレイのフォームの **周囲を確認...** で、近くのエンティティの一覧と一緒に使えます。

<a id="entities-and-vehicles"></a>
### エンティティと乗り物

エンティティを右クリックしたり、乗ったり、降りたりします。

```text
/bot <name> interact @<type|id|nearest>
/bot <name> dismount
```

`interact @<type|nearest|id>` は、6 ブロック以内で一致するいちばん近いプレイヤー以外のエンティティを選び、Bot をそのほうへ向けて、次の入力 tick で手に持ったアイテムで右クリックします。

- `cow` と `minecraft:cow` は同じです。
- `nearest` は、落ちているアイテム、経験値オーブ、飛び道具、エフェクト以外のどの種類でも選びます。
- id は `entities` で調べます。

プレイヤーの右クリックと同じように動きます。結果はすべて BDS が決め、返事は要求を確認するだけです:

- えさやりと繁殖では、手に持ったアイテムを消費します。
- ハサミは毛を刈り、名札は名前を付けます。
- 空の手でトロッコ、ボート、乗れる動物をクリックすると乗ります。

乗っている間:

- Bot は乗り物に合わせて動きます。`status` と Bot の画面には `<type> に乗車中` と表示されます。
- プレイヤーと同じように、別の乗り物を `interact` すると乗り換えます。`nearest` は、乗っている乗り物を飛ばします。
- 馬やボートの操縦はできません。

`dismount` で乗り物から降ります。Bot は乗り物の隣の空いている場所 (固い床と、その上に通れるブロックが 2 つある場所) を、乗り物と同じ高さ、1 つ上、1 つ下の順に探します:

- トロッコは、進行方向に対して右、左、右後ろ、左後ろ、右前、左前、後ろ、前の順に試します ([Minecraft Wiki](https://minecraft.wiki/w/Minecart#Dismounting))。
- ボートは、Bot の向いている方向を優先します ([Boat#Exiting](https://minecraft.wiki/w/Boat#Exiting))。
- ほかの乗れるものも同じ輪を使います。

空いている場所がなければ、Bot は乗り物があった場所にとどまります。降りる操作が保留中のときの 2 回目の `dismount` は拒否されます。BDS が 40 tick 以内に降りたことを確認しなければ、Bot は乗っている扱いをやめます。

床を取り除かれた Bot は落ちます。ボートの隣でも同じです。ハーフブロック、カーペット、水、ボート、シュルカーの上に立っているときは、そのままです。

<a id="autocomplete"></a>
### 入力補完

文書にある書き方はすべて入力すれば動きますが、ゲーム内の入力補完は一部しか役に立ちません。Bot の名前は候補に出ないので、`/bot list` で調べてください。理由: [コマンドの宣言](../BOT_CONTROL.md#command-declaration)。

<a id="shared-group-menus-and-results"></a>
## グループの共通メニューと結果

グループも、1 体の Bot と同じメニューで操作できます。グループの書き方とセレクターは [GROUPS.md](GROUPS.md) にあります。

```text
/botgroup                     グループの閲覧と作成 (/botgroup menu と同じ)
/botgroup menu #miners        メンバーとメンバーの管理。グループを一括操作... もある
/bot #miners menu             グループ用の共通の Bot 操作画面
/botgroup result #miners [history]
/botgroup visibility #group shared|private
/botgroup owner #group <GamerTag>
```

<a id="menus"></a>
### メニュー

- `/bot #miners menu` は、テレポート、接続の管理、チャンク半径も含めて、グループ全体の Bot 操作画面を開きます。
- 操作画面から戻ると、グループの管理から入ったときは管理に、直接開いたときは対象一覧に戻ります。
- **グループ管理...** は、そのグループの管理画面へのリンクです。管理から入ったときは、代わりに既存の **グループの管理へ戻る** ボタンを使います。
- 各メンバーは、通常の Bot の画面を開きます。
- グループの削除には確認があり、Bot は残ります。
- フォームは、同じ権限チェックで、同じ `/bot` や `/botgroup` のコマンドを送信します。古いフォームは拒否されます。

<a id="results"></a>
### 結果

グループ全体に変更を伴うコマンドの返事は、最大 2 行です。内容は数と、まとめた失敗の分類です。これは受け付けたことの確認で、ワールドでの動作が完了したことではありません。入力を送った後に応答が得られなかったときは、受付不明として報告します。

`/botgroup result #miners [history]` は、1 つの操作について各メンバーの結果を表示します:

- 番号なし、または `1`: 最新の操作
- `2`: その 1 つ前。以下同様です。

メニューでは `/botgroup menu #miners result [history]` で同じ結果を表示し、新旧を切り替えるボタンがあります。

- 保持するのは、プレイヤーとグループごとに最大 20 操作、全体で 128 操作までです。古いものから消えます。
- 結果はあなたとそのグループだけのもので、再起動で消えます。

<a id="queries"></a>
### 問い合わせ

`/bot #miners status`、`inventory`、`hotbar`、`radius` は、各メンバーの結果を `#group[index]` と Bot の名前を付けて表示します。

- 失敗はそのメンバーの行に入り、残りのメンバーも引き続き問い合わせます。
- 番号付きの問い合わせも同じです。
- 問い合わせは `result` の履歴を変えません。そのため、操作の後に状態を確かめても、その操作が最新の結果のままです。
- `hotbar <slot>` と `radius <value|default>` は変更を伴うので、要約で返事をし、履歴に残ります。

<a id="ownership"></a>
### 持ち主

- 持ち主かコンソールは、グループの公開範囲を `shared` か `private` にできます。
- 持ち主のいないグループは共有のままで、拒否されたときは `owner` が案内されます。
- グループの持ち主を変えられるのはコンソールだけです。GamerTag はオンラインのプレイヤーかコントローラーでなければなりません。
- 非公開のグループは、ほかのプレイヤーの一覧に表示されず、そのプレイヤーからは「見つからない」扱いになります。

グループの動作の全体: [GROUPS.md](GROUPS.md)。

<a id="scripts-and-triggers"></a>
## スクリプトとトリガー

Luau のスクリプトを実行し、イベントで始めます。スクリプトは、オペレーターが有効にしたサーバーで使えます ([準備](../SCRIPTING_SETUP.md))。

```text
/botscript list
/botscript show <name> [page]
/botscript run <name> [positional arguments...]
/botscript runs
/botscript stop <run-id|name>
/botscript delete <name>
/botscript help
/botscript menu
/botscript trigger add <script> <kind> [field=value ...] [name=<name>] [visibility=shared|private] [-- <arg> ...]
/botscript trigger list
/botscript trigger show <name>
/botscript trigger enable <name>
/botscript trigger disable <name>
/botscript trigger remove <name>
/botscript trigger set <name> <policy>=<value> ...
/botscript trigger visibility <name> shared|private
```

まず[チュートリアル](SCRIPTING_TUTORIAL.md)から始めてください。[SCRIPTING.md](SCRIPTING.md) がリファレンスです。

例:

```text
/botscript run row #miners "~ ~ ~" 2
```

<a id="running-scripts"></a>
### スクリプトの実行

- 実行にはあなたの権限が使われ、操作のたびに確かめ直します。実行はログアウトしても続き、止めるか権限が取り消されるまで続きます。
- 実行は再起動の後に再開しません。
- 順番どおりの引数は、スクリプトの宣言の順に並べ、その後に `name=value` を書きます。例のように、位置は引用符で囲みます。
- 入力するコマンドではソースを書けません。ソースを書くには、`/bot menu` の **スクリプト...** (**+ ここに新しい Bot** の後) を使います。共有のソース、行ごとの編集と本での編集、テンプレート、パラメーターのフォーム、各スクリプトの画面でのソースの表示、自分の実行の停止、確認つきの削除があります。
- スクリプトを無効にしても、ソースは残ります。

<a id="triggers"></a>
### トリガー

トリガーは、イベントの種類とフィルタをスクリプトに結びつけ、そのイベントが起きるたびにスクリプトを始めます。

- スクリプトは `param.event("event")` を宣言します。
- `trigger add` と `trigger enable` は、スクリプトの宣言とパラメーターを承認します。そのため、トリガーを有効にできるのは、そのスクリプトを実行できる人だけです。
- `area=` は、`within` と同じ範囲の値を、引用符で囲んだ 1 つの語として受け付けます (`"100 64 200"`、`"0 64 0 3 65 3"`、`"5 of 0 64 0"`)。`except <values>` を付けることもできます。
- `--` の後はスクリプトの引数です。
- `trigger set` は `concurrency`、`cooldown` (tick)、`startsPerMinute`、`queue` を受け付けます。
- コンソールは、ほかの持ち主のトリガーを `<owner>/<name>` (表示名か持ち主のキー) で指定します。

オペレーターが決める上限:

- `[limits] max-triggers-per-controller`: 既定は 8。`0` でトリガーを無効にします。
- `max-watched-blocks-per-controller`: 既定は 1024。

一時停止、無効化、ポリシーの既定値: [SCRIPTING.md](SCRIPTING.md#triggers)。

<a id="operational-states"></a>
## 接続状態

`/bot list` と `status` に出る状態の意味:

| 状態 | 意味 |
| --- | --- |
| `connecting` | 要求したセッションが、まだ spawn していません |
| `online` | セッションが動いています |
| `reconnecting` | 明示的な再接続か、自動の再試行が進んでいます |
| `offline` | Bot を意図してオフラインにしています |
| `failed` | 再試行を使い切りました。`status` に最後のエラーが入ります |

- 各 Bot の再接続と入力は、ほかのすべての Bot から独立しています。
- 予期しない切断の後、Bot は UUID を保ち、待ち時間を延ばしながら (間隔は最大 30 秒) 再試行し、設定した試行回数で止めます。
- `reconnect` とオンライン中の `rename` は、先に古い接続が閉じるのを待ちます。そのため、2 つのセッションが重なることはありません。
