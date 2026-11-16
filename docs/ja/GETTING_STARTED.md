<!-- source: docs/GETTING_STARTED.md 04848ed884ddf6123fb16b37707b940f007200d2 -->

<a id="getting-started-with-endbot"></a>
# はじめての Endbot

Endbot はサーバーに Bot を追加します。Bot は `/bot` コマンドやメニューで操作できるプレイヤーです。このページは
5 分ほどで読めます。最初の Bot の出し方と、試してみたいことをいくつか紹介します。すべての操作とオプションは
[コマンド](../COMMANDS.md) (英語) にあります。

<a id="before-you-start"></a>
## 始める前に

- **権限が必要です。** だれが Bot を操作できるかは、サーバーのオペレーターが決めます。権限がないと、ゲームは
  `/bot` をコマンドとして認識しません。オペレーターに頼んでください。
- **いつもの Xbox サインインで参加してください。** インストールするものはありません。
  [UI リソースパック](../UI_PACK.md)は任意で、メニューを大きくし、スクリプト用の複数行エディターを使えるようにします。
- **ゲームの言語が使われます。** 日本語に設定したクライアントでは、返事とメニューが日本語になります。コマンドは
  どの言語でも同じです。

<a id="1-make-your-first-bot"></a>
## 1. 最初の Bot を出す

チャットに `/bot` と入力します。操作できる Bot のメニューが開きます。**+ ここに新しい Bot** を押し、名前
(英数字と `_`、16 文字まで) を入れて送信します。Bot が接続し、数秒であなたの隣に現れます。

コマンドで同じことをするには:

```text
/bot Alice spawn
```

<a id="2-make-it-do-something"></a>
## 2. Bot を動かす

`/bot Alice` と入力する (または `/bot` の一覧で Bot を押す) と、その Bot のメニューが開きます。ボタンの分類は
Minecraft の操作設定にならっています。**移動...**、**向きを変える (look)...**、**テレポート...**、
**ゲームプレイ...**、**インベントリ...**、そして接続を扱う **Bot...** です。

押したボタンは、実行したコマンドとしてチャットに灰色で一度表示されます。メニューを使ううちにコマンドを覚えられる
ので、覚えたら次からは入力しても構いません。

```text
/bot Alice tp me                 自分のところへ呼ぶ
/bot Alice jump                  1 回ジャンプ
/bot Alice move forward for 40   2 秒間前に歩く
/bot Alice attack continuous     攻撃し続ける
```

時間の単位は **tick** です。20 tick が 1 秒です。

<a id="3-stop-it"></a>
## 3. 止める

```text
/bot Alice stop
```

`stop` は移動、アクション、採掘など、Bot がしていることをすべて止めます。Bot はオンラインのままです。メニューでは
いちばん上の **すべて止める (stop)** です。

Bot をサーバーから外すには `/bot Alice despawn` を使います。登録は残るので、`/bot Alice spawn` で戻せます。

<a id="things-to-try"></a>
## 試してみよう

<a id="chop-a-tree"></a>
### 木を切る

Bot に斧を渡し (近くに落とします)、木の隣に立たせます:

```text
/bot Alice hold iron_axe
/bot Alice destroy oak_log count 5
```

Bot は手の届くいちばん近いオークの原木のほうを向き、5 個壊します。歩きはしないので、先に届く距離まで連れてきて
ください (`tp me`)。採掘はふつうのサバイバルと同じで、合った道具なら速く、原木はアイテムとして落ちます。

<a id="feed-animals"></a>
### 動物にえさをやる

えさを Bot に持たせ、動物の近くに立たせます:

```text
/bot Alice hold wheat
/bot Alice interact @cow count 3
```

Bot は別々の牛 3 頭に、1 頭ずつえさをやります。

<a id="control-several-bots-at-once"></a>
### 何体もまとめて動かす

Bot をグループに入れ、`#` でグループを指定します:

```text
/botgroup create #miners
/botgroup add #miners Alice
/botgroup add #miners Bob
/bot #miners tp me
/bot #miners stop
```

`#miners[1]` は 1 番目のメンバーだけを指します。グループにもメニューがあり、`/bot` の **グループを管理...** から
開けます。詳しくは[グループ](../GROUPS.md)を見てください。

<a id="control-bots-from-your-phone-or-pc-browser"></a>
### スマホや PC のブラウザから操作する

オペレーターが Endbot Web を用意していれば、`/botweb link` と入力します。ウェブページに入力する短いコードが
表示されます。詳しくは[ウェブとの連携](../COMMANDS.md#linking-the-web) (英語)を見てください。

<a id="automate-with-a-script"></a>
### スクリプトで自動化する

オペレーターがスクリプトを有効にしていて、あなたにスクリプターの役割があれば、スクリプトでループや条件を使って
操作を繰り返せます。ワールドで何かが起きたときに自動で始めることもできます。まずは
[スクリプトのチュートリアル](../SCRIPTING_TUTORIAL.md)から始めてください。

<a id="when-something-does-not-happen"></a>
## うまくいかないとき

- **返事は「要求を受け付けた」という意味で、「うまくいった」という意味ではありません。** 当たり判定、届く距離、
  何が壊れるかはゲームが決めます。`/bot Alice status` で、Bot がどこにいて何をしているかを確かめてください。
- **`... failed: Bot is offline`**: `/bot Alice spawn` で戻してください。
- **`/bot` が認識されない**: まだ権限がありません。オペレーターに頼んでください。
- **`Bot Alice は ... の非公開 Bot です`**: その Bot は別のプレイヤーのもので、非公開になっています。
  [権限モデル](../PERMISSIONS.md)を見てください。
- **コマンドを忘れたとき**: `/bot help` で一覧が、`/bot help destroy` で 1 つの操作の説明が出ます。

<a id="where-next"></a>
## 次に読むもの

- [コマンド](../COMMANDS.md) (英語): すべての操作、メニュー、返事の意味
- [グループ](../GROUPS.md): 何体もの Bot を 1 つの対象として操作する
- [スクリプトのチュートリアル](../SCRIPTING_TUTORIAL.md): 最初のスクリプトからイベントへの反応まで
