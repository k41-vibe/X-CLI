# Xcli

X(旧Twitter)の**非公式 GraphQL API**(x.com Web アプリが内部で叩くもの)を利用する自己利用 CLI。
公式 API 不要・ほぼ全機能に近いが、**X 利用規約違反でありアカウント凍結リスクがある**。本人アカウントの低頻度な自己利用に限定して使うこと。

## 仕組み

- エンドポイント: `https://x.com/i/api/graphql/{queryId}/{Operation}`
- 認証: 公開 Bearer(固定)+ `auth_token`/`ct0` Cookie + `x-csrf-token`(=ct0)+ `x-client-transaction-id`
- `x-client-transaction-id` は [XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) で生成
- `queryId` / `features` は X のデプロイ毎に変わる → `xcli sync` で公開 JS から自動再取得、
  `features` 不足は API のエラーメッセージから自動補完リトライ

## セットアップ

```bash
pip install -e .        # または: pip install requests beautifulsoup4 XClientTransaction
xcli config             # 設定ファイルの場所を確認(初回に雛形生成)
```

`~/.xcli/config.json` に **自分の Cookie** を貼る(Chrome DevTools > Application > Cookies > https://x.com):

```json
{
  "auth_token": "＜auth_token の値＞",
  "ct0": "＜ct0 の値＞",
  "lang": "ja",
  "min_interval_sec": 2.0
}
```

> auth_token はアカウント資格情報。他人に渡さない。config.json はローカルのみに置く。

## 使い方

```bash
xcli sync                        # queryId を最新化(初回と、動かなくなった時に実行)
xcli user XDevelopers            # プロフィール取得
xcli user XDevelopers --tweets -n 50  # 最新ツイートも(任意件数)
xcli timeline -n 100             # ホームタイムライン(フォロー中・時系列)
xcli timeline --algo             # おすすめ(アルゴ順・要ログイン sync)
xcli post "こんにちは"            # 投稿
xcli post "写真つき" --media a.jpg b.jpg  # 画像/動画つき投稿(複数可)
xcli post "返信" --reply 190000   # 返信
xcli thread "1つ目" "2つ目" "3つ目"  # スレッド投稿(連続ツイート)
xcli delete 1900000000000000000  # 削除

xcli like <id>     / xcli unlike <id>      # いいね / 解除
xcli rt <id>       / xcli unrt <id>        # リツイート / 解除
xcli bookmark <id> / xcli unbookmark <id>  # ブックマーク / 解除

xcli follow <handle|id>    # フォロー(鍵垢はリクエスト送信)
xcli unfollow <handle|id>  # フォロー解除

xcli search "クエリ" --tab latest -n 20   # 検索
#   --tab: top(人気) latest(最新) people(アカウント) media(メディア) lists(リスト)

xcli likes <自分のhandle> -n 20   # いいね一覧(他人の分は非公開で見えない)
xcli bookmarks -n 20              # ブックマーク一覧
xcli notifications -n 40          # 通知(notif でも可)

xcli dms -n 20                   # DM受信箱(会話一覧)
xcli dm <handle|id> "本文"        # DM送信
xcli dm-read <handle|conv_id>    # 会話の全メッセージ表示(msg=IDも表示)
xcli dm-delete <message_id>      # DMメッセージを削除
xcli dm-delete-conv <handle|id>  # 会話ごと削除

xcli media <id>                  # ツイートの画像/動画URLを表示
xcli media <id> --open           # 既定ブラウザで開く
xcli media <id> --download ./dl  # フォルダに保存(画像は原寸、動画は最高画質mp4)
```

タイムライン/検索/ブックマーク等の出力にも `[photo]`/`[video]` 行で実メディアURLが付く。

## 端末内インライン画像表示

ANSI truecolor(半ブロック文字 `▀`)で画像をモザイク描画する。特殊な画像プロトコル
(sixel/iTerm2/kitty)に依存せず、24bitカラー対応の端末なら **OS問わず** 描画できる。
解像度は粗め(1セル=上下2px)。本物の画質が欲しい時は `xcli media <id> --open`。

```bash
xcli config --images on         # 全取得系で画像を常時インライン表示
xcli config --images off        # OFF(既定)
xcli timeline --images          # 単発でON(config OFF時)
xcli timeline --no-images       # 単発でOFF(config ON時)
xcli media <id> --show          # 単体ツイートの画像を表示
```

動画は端末内再生不可。`--open`(ブラウザ)/ `--download`(mp4保存)で扱う。

`-n` は**任意の件数**を指定可能。1ページ(〜40件)を超える分はカーソルを辿って
自動でページ送りして集める。フィード末尾に達したらそこまでを返す。

## ターミナルUI(TUI)

```bash
xcli tui
```

Textual 製のインタラクティブ画面。タブで タイムライン/検索/通知/DM を切替、閲覧+操作ができる。

- 操作は基本 **GUI(クリック)**:
  - 上部タブで **フォロー中 / おすすめ / 検索 / 通知 / DM** を切替
  - 上部バー: **自分のプロフィール** ボタン + 投稿欄(「いまどうしてる?」→Enter)
  - 各ツイート下のボタン: **♥いいね / RT / ★ブクマ / ↩返信 / 💬会話**(トグル系はトグル)、引用ポストは **❝引用元**、メディアあれば **画像/動画**、自分の投稿には **🗑削除**
  - **❞引用** = 引用して投稿 / **💬会話** = リプライの一連(TweetDetail) / **❝引用元** = 引用元ポストを開く
  - 一覧は**下までスクロールで自動追加読み込み**(無限スクロール)
  - プロフィール画面: フォロー / **ミュート** / **ブロック**(トグル)
  - ツイートの **名前をクリック → 著者プロフィール**
  - プロフィール画面: 相手なら **フォロー** ボタン、自分なら いいね/ブクマ タブ + 各ツイートに削除ボタン
  - **削除(ツイート/DM)は確認ダイアログ**が出る
- キーは最小限: `F5` 更新 / `q` 終了 / `Esc`(プロフィールから戻る)
- 検索タブ: クエリ+ドロップダウン(最新/人気/アカウント/メディア/リスト)+ from:/最低いいね数 の絞り込み
- 依存: `pip install textual rich-pixels`(画像インライン表示に使用)

## JSON エクスポート

取得系コマンド(timeline / user / search / likes / bookmarks / notifications / dms / dm-read)に
`--json` を付けると結果をJSONで出力。外部システム(監視・せどり集計・agent-fleet 等)から
`xcli ... --json` をパイプして食う想定。X本体に無い上位機能はXcliに入れず、これを部品にして別途組む。

```bash
xcli search "オールドレンズ" --tab latest --json -n 100 > lenses.json
xcli timeline --json | jq '.[].text'
```

## レート制御

`min_interval_sec`(既定 2 秒)で連続リクエストを間引く。ページ送りも毎ページこの間隔を空ける。
429 が出たら時間を空ける。大量取得は控えめに。

## トラブル

- `[認証エラー]` … Cookie 期限切れ。DevTools から取り直して config.json を更新
- `queryId が不明` / features エラー継続 … `xcli sync` を実行
- それでも失敗 … X 側の仕様変更。queryId/features の再捕獲が必要
