<!-- source: docs/SCRIPTING_TUTORIAL.md 933fa75b1b5dba7cb5d3bf455bfbdd7de9e38c94 -->

<a id="writing-bot-scripts-a-tutorial"></a>
# Bot のスクリプトを書こう: チュートリアル

このページでは、何もないところから始めて、Bot を動かしたり、採掘させたり、並べたりするスクリプトを書けるようになるまでを順に案内します。`/bot` はもう使えるものとして進めます ([コマンド](COMMANDS.md)を見てください)。[Luau](https://luau.org/) は、必要な分だけ途中で少しずつ説明します。すべてのメソッドと制限は[スクリプトのリファレンス](SCRIPTING.md)にあります。

スクリプトは Luau で書く小さなプログラムです。`/bot` コマンドで入力するのと同じ操作を送り、そのまわりで繰り返し、計算、条件分けができます。スクリプトはあなたの権限で動きます。あなたが操作できる Bot を操作でき、あなたがその権限を失うと止まります。

<a id="before-you-start"></a>
## 始める前に

- オペレーターがスクリプトを有効にしていること (`[scripting] enabled = true`。[スクリプトの準備](../SCRIPTING_SETUP.md#install-and-enable)を見てください)。
- あなたがコントローラーであること (`/bot` が使えること)。
- Bot がいること。例では Alice という Bot を使います: `/bot Alice spawn`。

<a id="1-where-to-write-a-script"></a>
## 1. スクリプトを書く場所

**PC のエディターで書く (サーバーのファイルを触れるなら、こちらがおすすめです)。** `<name>.luau` を、`endbot.toml` と同じ `<instance>/scripts/` に保存します (文字コードは UTF-8、改行コードはどれでも構いません)。それから、ゲーム内かコンソールで `/botscript reload` を実行します。ファイル名がスクリプトの名前になります。英数字とアンダースコアで 1 から 16 文字、最初の 1 文字は英字かアンダースコアです。ファイルを書き換えたら、そのたびにもう一度 `reload` してください。こうして置いたスクリプトはすべてのコントローラーに共有され、ゲーム内では読み取り専用です。[スクリプトファイル](SCRIPTING.md#script-files)を見てください。

**ゲーム内で書く。** `/bot menu` → **スクリプト...** → **+ 新しいスクリプト...** と進みます。テンプレート (または **空のスクリプト**) を選び、名前を付けて保存します。**行を編集...** で 1 行ずつ書き換えられます。長い文章は本と羽根ペンに書いてから、**手に持った本から...** (新しいスクリプト) か **手に持った本で全体を置き換え**を使ってください ([本](SCRIPTING.md#books)を見てください)。

どちらの方法でも、`/botscript menu` でスクリプトの一覧が出ます。スクリプトを押すと、そのソースが表示されます。

<a id="2-your-first-script"></a>
## 2. 最初のスクリプト

次の内容を `hello.luau` として保存し、`/botscript reload` します:

```lua
params {}
local alice = bot("Alice")
waitOnline(alice)
alice:jump()
```

`/botscript run hello` で実行するか、メニューで **hello** → **実行** を押します。Alice が 1 回ジャンプし、チャットに `スクリプト hello を開始: <run id>`、続いて `スクリプト hello (<run id>) が終了: ...` と表示されます。

1 行ずつ見ていきましょう:

- `params {}` は、このスクリプトの引数を宣言します。ここでは引数はありません。**どのスクリプトも `params` から始まります。** 1 回だけ、ほかの何よりも先に書きます。
- `bot("Alice")` は、Alice という名前の Bot を指すハンドルを返します。この時点では、まだ何も確かめません。
- `waitOnline(alice)` は、Alice が接続するまで待ちます。こうすると、Alice がまだ参加中のときに次の行が拒否されずに済みます。
- `alice:jump()` は `/bot Alice jump` と同じです。`:` で、ハンドルに対して操作を呼び出します。

`log("some text")` は、チャットとサーバーコンソールに 1 行表示します。チャットでは、スクリプトの名前と run id の後に表示されます。試すには、ジャンプの後に `log("jumped")` を足してみてください。チャットに出るのは、1 回の実行につき 5 秒ごとに最大 10 行までです。表示しきれなかった行があれば、その数が表示されます。コンソールにはすべての行が残ります。

<a id="3-parameters-one-script-many-uses"></a>
## 3. 引数: 1 つのスクリプトをいろいろに使う

`"Alice"` とスクリプトに直接書いてしまうと、Bot を変えるたびにスクリプトを書き直すことになります。代わりに引数を宣言しましょう。次の内容を `hop.luau` として保存します:

```lua
local a = params { param.bot "name", param.number("times", 3) }
local b = bot(a.name)
waitOnline(b)
for i = 1, a.times do
    b:jump()
    wait(20)
end
```

今度の `params` は、引数ごとに 1 つずつ項目 (フィールド) を持つテーブル `a` を返します。メニューで **実行** を押すとフォームが出ます。`name` には Bot を選ぶドロップダウン、`times` には数を入れる欄 (最初から 3 が入っています) があります。チャットからは、宣言した順に値を書きます。`name=value` と書けば、どの引数でも名前で指定できます:

```text
/botscript run hop Alice
/botscript run hop Alice 5
/botscript run hop Alice times=5
```

`wait(20)` は 20 tick、つまり 1 秒止まります。`for i = 1, a.times do ... end` は、その間に書いた部分を繰り返します。

| 宣言 | スクリプトの中での値 | メニューでの入力 |
| --- | --- | --- |
| `param.bot "name"` | `bot()` に渡す Bot の名前 | Bot のドロップダウン |
| `param.group "group"` | `group()` に渡す `#group` の名前 | グループのドロップダウン |
| `param.pos("origin", "~ ~ ~")` | ベクトル (5 章を見てください) | 文字の入力欄。最初から自分の立っている場所が入っています |
| `param.number("count", 2)` | 数 | 文字の入力欄 |
| `param.bool("loop", false)` | `true` か `false` | トグル |
| `param.string("label", "")` | 文字列 | 文字の入力欄 |
| `param.choice("mode", {"walk", "run"}, "walk")` | 並べた文字列のどれか 1 つ | ドロップダウン |

2 つ目の引数はデフォルト値です。デフォルト値がない引数は、必ず指定しなければなりません。`param.bot "name"` は `param.bot("name")` の Luau での省略した書き方です。

<a id="4-did-it-work-checking-results"></a>
## 4. うまくいった? 結果を確かめる

どの操作も、すぐに結果のテーブルを返します。結果からわかるのは、操作が **受け付けられた** かどうかです。終わったかどうかではありません:

```lua
local r = b:move("forward", {ticks = 40})
if not r.ok then
    error("move was rejected: " .. r.reason)
end
```

`r.ok` は `true` か `false` です。拒否されたときは、`r.reason` に理由が入ります。たとえば Bot がオフラインのときなどです。テンプレートでは、次の短い書き方を使っています:

```lua
assert(b:move("forward", {ticks = 40}).ok, "move rejected")
-- または、理由も残すなら:
local r = b:move("forward", {ticks = 40})
assert(r.ok, r.reason)
```

`error(...)` や失敗した `assert` は実行を終わらせます。チャットに出る終了メッセージで理由がわかります。

結果はすぐに返ってくるので、次の操作を始める前に **動作が終わるのを自分で待って** ください。40 tick 歩くには 40 tick かかります。そこで、見回りのスクリプトではその分だけ待ちます:

```lua
local a = params { param.bot "name", param.number("ticks", 40) }
local walker = bot(a.name)
waitOnline(walker)
for side = 1, 4 do
    local r = walker:move("forward", {ticks = a.ticks})
    assert(r.ok, r.reason)
    wait(a.ticks)
    walker:look(side * 90, 0)   -- yaw と pitch、単位は度
end
```

採掘だけは例外です。`waitMining` (7 章) は、ジョブそのものが終わるのを待ちます。

<a id="5-positions-are-vectors"></a>
## 5. 位置はベクトル

`param.pos` の値は、`.x`、`.y`、`.z` を持つベクトルです。ベクトルは足し算や掛け算ができます。また、操作が `x, y, z` を受け取るところには、どこでもベクトル 1 つを渡せます:

```lua
local a = params { param.bot "name", param.pos("start", "~ ~ ~") }
local b = bot(a.name)
waitOnline(b)
for i = 0, 4 do
    local spot = a.start + vector.create(i * 2, 0, 0)   -- +X 方向に 2 ブロックずつ離す
    local r = b:tp(spot)
    assert(r.ok, r.reason)
    wait(20)
end
```

デフォルトの `"~ ~ ~"` は「実行したときに自分が立っている場所」という意味です。コンソールからは、絶対座標を引用符で囲んで指定します: `/botscript run steps Alice "100 64 -20"`。`b:tp(x, y, z, {yaw = 90, pitch = 0})` とすると向きも変えられます。`b:tp("Steve")` はプレイヤーのところへテレポートします。

<a id="6-working-with-groups"></a>
## 6. グループを使う

[グループ](GROUPS.md)は、`#miners` のように名前を付けた Bot の一覧です。`group("#miners")` には Bot と同じ操作が使え、メンバー全員にその操作をさせます。`:members()` は、メンバーごとに 1 つずつ、順番にハンドルを返します:

```lua
local a = params { param.group "group", param.pos("origin", "~ ~ ~"), param.number("spacing", 2) }
for i, member in group(a.group):members() do
    local r = member:tp(a.origin + vector.create((i - 1) * a.spacing, 0, 0))
    assert(r.ok, r.reason)
end
```

これが **グループを一列に並べる** テンプレートです。自分の立っている場所から +X 方向に、グループを一列に並べます。グループ全体への呼び出しは、メンバーが 1 体でも拒否すると `ok = false` を返します。

<a id="7-reading-the-world-and-waiting-for-work"></a>
## 7. ワールドを読み取り、作業を待つ

問い合わせの答えは `data` に入って返ってきます:

```lua
local s = b:status()
assert(s.ok, s.reason)
local p = s.data.position
log(string.format("%s is at %.1f %.1f %.1f holding %s", s.data.name, p.x, p.y, p.z,
    s.data.held and s.data.held.name or "nothing"))
```

`status()` では、体力、満腹度、選んでいるスロット、採掘の状態もわかります。それ以外は `inventory()`、`entities()`、`target()`、`block(x, y, z)`、`blocks(type)` で読み取れます ([構造化された状態](SCRIPTING.md#structured-status-and-mining-waits))。

採掘には、本当に終わるまで待つ方法があります。Alice をオークの原木の隣に立たせて、次を実行してください:

```lua
local a = params { param.bot "name", param.number("logs", 5) }
local b = bot(a.name)
waitOnline(b)
local r = b:destroy("oak_log", {count = a.logs})
assert(r.ok, r.reason)
local done = b:waitMining{timeout = 1200}   -- 長くても 60 秒
log("mining ended: " .. done.reason)
assert(done.ok, done.reason)
```

`waitMining` は、ジョブが終わったとき (`block count reached`、`stopped` など)、またはタイムアウトになったときに戻ります。タイムアウトしてもジョブは止まりません。`group:waitMining{...}` は、メンバー全員を待ちます。

<a id="8-stopping-and-fixing"></a>
## 8. 止める、直す

- `/botscript runs` で、実行中のスクリプトの一覧が出ます。`/botscript stop <run id|name|all>` で止められます。止めても、Bot がすでにしたことは元に戻りません。
- コードの間違い (`end` の書き忘れや打ち間違いなど) は、スクリプトを保存したときや実行したとき、スクリプトファイルなら `reload` したときに、`script:4: ...` のように行番号つきで知らされます。その行を直して、もう一度試してください。
- 1 秒に約 20 回より多く操作を求めるスクリプトは、止められずに遅くされます。多すぎた操作は順番を待ち、そのことがログに一度だけ出ます。スクリプトが止められるのは、ホストにずっと多くの要求を浴びせたとき (ログや待ち時間 0 の wait も数に入ります)、CPU 時間を使い切ったとき、あなたが Bot を操作する権限を失ったときです。どれが理由かは終了メッセージでわかります。
- あなたがログアウトしても実行は続きます。サーバーが再起動すると、実行は終わります。

<a id="9-reacting-to-events"></a>
## 9. イベントに反応する

ここまでのスクリプトは、あなたが始めたときに動くものでした。イベントを使うと、Bot がワールドの出来事に反応できます。たとえばブロックが変わった、プレイヤーが参加した、夜になった、タイマーが鳴った、といった出来事です。使い方は 2 通りあり、それぞれ向いている仕事が違います。

- **トリガー** は、イベントが起きるたびに、あなたの代わりにスクリプトを始めます。無効にするまで続きます。トリガーはサーバーに保存され、再起動しても残ります。
- **監視** は、すでに動いているスクリプトの中でイベントを待ちます。そのため、スクリプトは先に準備をしておき、イベントが来たときに何をするかを決められます。

例では Bob という Bot と、`0 64 0` にあるブロックを使います。

<a id="your-first-trigger"></a>
### 最初のトリガー

トリガーから始まるスクリプトは、`param.event` を宣言します。次の内容を `bob_toggle.luau` として保存します:

```lua
local a = params { param.event "event", param.bot("name", "Bob") }
local b = bot(a.name)
local s = b:status()
if s.ok and s.data.connectionState == "online" then
    b:despawn()
else
    b:resume()
end
```

それから、このスクリプトをブロックに結び付けます:

```text
/botscript trigger add bob_toggle block.change area="0 64 0"
```

`0 64 0` のブロックを壊したり置いたりすると、Bob がオフラインになったり戻ってきたりします。`trigger add` は、まずスクリプトの宣言を見せて、あなたの承認を記録します。`name=...` を付けない限り、トリガーの名前はスクリプトの名前になります。コマンドを使わなくても、`/botscript menu` → **トリガー...** やウェブから同じことができます。

- `a.event` には、起きた出来事の内容が入っています。`block.change` なら、`x`、`y`、`z`、`from`、`to`、`cause` (`player-break`、`player-place`、`poll` のどれか)、Bot がしたときは `bot`、そして `missed` (前回始まってから落とした出来事の数) があります。`/botscript run bob_toggle` で手で実行すると `a.event` は `nil` になるので、1 つのスクリプトをどちらの使い方にも使えます。
- 絞り込み (フィルター) で、トリガーの対象を狭められます。`to=air` はブロックが取り除かれたときだけ、`from=air` はブロックが置かれたときだけ発動します:

  ```text
  /botscript trigger add bob_off block.change area="0 64 0" to=air
  /botscript trigger add bob_on  block.change area="0 64 0" from=air
  ```

  1 つは `b:despawn()`、もう 1 つは `b:resume()` を呼ぶ小さなスクリプトを 2 つ用意すれば、Bob の状態から推測する必要がなくなります。Bob がまだ接続中のときは、状態が遅れて反映されることがあるからです。
- `area=` には `within` と同じ値が使えます。1 ブロックなら `"0 64 0"`、箱型なら `"0 64 0 3 65 3"`、球なら `"5 of 0 64 0"` で、最大 512 ブロックです。
- ほかの種類も同じように使えます。`schedule every=10m`、`world.time phase=night` など、残りは[イベントの種類](SCRIPTING.md#event-kinds)にあります。プレイヤーに関する種類 (`player.join`、`player.chat`) には、スクリプトで `requires { "observe.presence" }` か `requires { "observe.chat" }` を宣言する必要があります。
- トリガーで始まった実行がふつうに終わったときは、チャットに何も出ません。そのため、頻繁に発動するトリガーでもチャットが埋まりません。失敗したときと、あなた自身の `log` の行は表示されます。
- `/botscript trigger list` で、自分のトリガーとその状態の一覧が出ます。`trigger show <name>` では、回数 (始まった回数、missed、skipped) も表示されます。`disable`、`enable`、`remove` は、名前のとおり無効にする、有効にする、削除する、です。

<a id="waiting-inside-a-script"></a>
### スクリプトの中で待つ

監視は「先にこれをして、それから反応する」ためのものです。次のスクリプトは、Bob をオンラインにしてから、ブロックが壊されるのを最大 10 分待ちます。壊されたとき、Bob がまだオンラインなら、Bob をサーバーから外します:

```lua
local a = params { param.bot("name", "Bob") }
local b = bot(a.name)
b:resume()
waitOnline(b)

local w = watch("block.change", {area = area.cell(0, 64, 0), to = "air"})
for _ = 1, 10 do
    local r = w:wait{timeout = 1200}           -- 1 分
    if r.ok then
        local s = b:status()
        if s.ok and s.data.connectionState == "online" then
            b:despawn()
        end
        break
    elseif r.code == "unavailable" then
        log("events were missed while waiting: " .. r.reason)
    end                                        -- タイムアウトなら、また待つだけ
end
```

- `watch()` から `wait` までの間に起きた出来事も失われません。監視は、新しいものから 64 個までの出来事を覚えています。
- `wait` は、出来事が届いたらすぐに戻ります。待っている間は、CPU も操作も使いません。
- `unavailable` は、BDS かその接続が再起動して、その間の変化が見えなかったという意味です。それが大事なら、`b:block(x, y, z)` でワールドを読み直してください。
- 監視は実行と一緒に終わります。そして、実行は Endbot が再起動すると残りません (8 章)。

<a id="which-one-to-use"></a>
### どちらを使うか

| したいこと | 使うもの |
| --- | --- |
| 毎回同じように、何日も、再起動をまたいで反応する | トリガー |
| 毎回反応するが、ある条件を満たすときだけ | トリガー。スクリプトの最初で条件を確かめ、満たさなければすぐ戻る |
| 準備して、イベントを 1 つ (またはいくつか) 待ち、終わる | スクリプトの中の監視 |
| 長い作業の途中でイベントを待つ | スクリプトの中の監視 |

どちらでもできるときは、トリガーを選んでください。動いているスクリプトは、待っている間ずっと、あなたが同時に使える実行の枠を 1 つ使います。その枠は 2 つしかありません (`[scripting] controller-runs`)。トリガーは待っている間は枠を使いません。実行があるのは反応している間だけです。長く待つスクリプトが 2 つあると両方の枠が埋まり、あなたのトリガーが始まろうとしても毎回飛ばされます (`trigger show` の `skipped` に数えられ、チャットで一度知らされます)。

<a id="things-that-surprise-people"></a>
### 驚きやすいところ

- **クールダウンは「後回し」ではなく「捨てる」です。** デフォルトでは、トリガーは 1 秒に最大 1 回しか始まりません (`cooldown` 20 tick)。また、前の実行がまだ続いている間のイベントは飛ばします。こうしたイベントは `missed` として数えられ、後から始まることはありません。代わりに後で始めたいときは、キューを使います: `/botscript trigger set bob_toggle concurrency=queue`。
- **頻繁なイベントは抑えられます。** トリガーが始まるのは 1 分に最大 10 回です (`startsPerMinute`)。それを超えたイベントは飛ばされ (またはキューに入り)、チャットで一度知らされます。トリガーは動き続けます。
- **一時停止したトリガーは、あなたを待ちます。** トリガーが一時停止するのは、スクリプトが前より多くを求めるようになったとき (`宣言が広がりました`)、引数が変わったとき (`引数が無効になりました`)、あなたが持ち主でないスクリプトをほかのプレイヤーが変更したとき (`他の人のスクリプトが変更されました`) です。そうなったときはチャットで知らされます。内容を確かめてから `/botscript trigger enable <name>` で新しい版を承認してください。自分のスクリプトとオペレーターのスクリプトファイルは、求めるものが増えない限り、自由に編集できます。
- **あなたとして動きます。** トリガーで始まった実行は、だれがイベントを起こしたかに関係なく、あなたの権限で動きます。コントローラーの権限を失うと、あなたのトリガーは無効になります。権限をもう一度もらっても、自分で有効にするまで無効のままです。
- **制限。** 有効なトリガーは 8 個まで、監視とトリガーで見張れるブロックは合わせて 1024 個まで、1 回の実行で持てる監視は 16 個までです。これらはオペレーターが `[limits]` で変えられます。

細かい決まりは[監視](SCRIPTING.md#watches)と[トリガー](SCRIPTING.md#triggers)にあります。

<a id="10-cheat-sheet"></a>
## 10. 早見表

```lua
local a = params { param.bot "name" }     -- いつも最初。引数を宣言する
local b = bot(a.name)                     -- Bot のハンドル。グループなら group("#name")
waitOnline(b)                             -- 接続するまで待つ
local r = b:jump()                        -- どの /bot 操作でも使える。{ok, reason, data} を返す
assert(r.ok, r.reason)                    -- 拒否されたら実行を止める
wait(20)                                  -- 20 tick = 1 秒
b:move("forward", {ticks = 20})           -- オプションは最後のテーブルに書く
b:tp(vector.create(0, 64, 0))             -- ベクトルは x, y, z の代わりになる
b:waitMining{timeout = 1200}              -- destroy や採掘のジョブを待つ
log("text")                               -- チャットとサーバーコンソールに 1 行
local w = watch("block.change", {area = area.cell(0, 64, 0)})   -- イベントのため込みを始める
local r = w:wait{timeout = 1200}          -- r.ok と r.data、またはコード "timeout"/"unavailable"
```

```text
/botscript trigger add <script> <kind> [filter=value ...]   -- スクリプトは param.event を宣言する
/botscript trigger list | show | enable | disable | remove <name>
/botscript trigger set <name> concurrency=queue cooldown=0
```

次に読むもの: [自動生成の API リファレンス](SCRIPTING.md#generated-api-reference)には、すべての操作とそのオプションが載っています。[コマンド](COMMANDS.md)では、それぞれの操作が何をするかを説明しています。
