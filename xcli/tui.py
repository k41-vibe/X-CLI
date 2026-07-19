"""Xcli ターミナルUI(Textual)。

起動時に XCLI ロゴのスプラッシュ表示。以降は基本すべて GUI(クリック)操作:
  上部タブ = フォロー中 / おすすめ / 検索 / 通知 / DM
  上部バー = 自分のプロフィールボタン + 投稿欄
  各ツイート下 = ♥いいね / RT / ★ブクマ / ↩返信 / (メディア) / (自分の投稿は🗑削除)
  名前クリック = 著者プロフィール、プロフィールでフォロー/削除ボタン、削除は確認ダイアログ
キーは F5 更新 / q 終了 / Esc 戻る のみ。起動: `xcli tui`
"""
from __future__ import annotations

from rich.markup import escape

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from . import commands, imaging
from .client import XClient

LOGO = """
███████       ███████             █████████████     ███████████                  ██████████
███████       ███████          ████████████████     ███████████                  ██████████
███████       ███████        ██████████████████     ███████████                  ██████████
████████     ████████       ███████████████████     ███████████                  ██████████
█████████   █████████      ███████       ██████       ███████                      ██████
   ███████ ███████        ███████                     ███████                      ██████
    █████████████         ███████                     ███████                      ██████
     ███████████          ███████                     ███████                      ██████
     ███████████          ███████                     ███████                      ██████
    █████████████         ███████                     ███████                      ██████
   ███████ ███████        ███████                     ███████                      ██████
█████████   █████████      ███████       ██████       ███████         ██████       ██████
████████     ████████       ███████████████████     ████████████████████████     ██████████
███████       ███████        ██████████████████     ████████████████████████     ██████████
███████       ███████          ████████████████     ████████████████████████     ██████████
███████       ███████             █████████████     ████████████████████████     ██████████
"""


class LogoScreen(ModalScreen):
    """起動スプラッシュ。数秒 or キー入力で消える。"""

    BINDINGS = [("escape,enter,space", "skip", "スキップ")]

    def compose(self) -> ComposeResult:
        with Vertical(id="logo-box"):
            yield Static(LOGO, id="logo-art")
            yield Static("非公式 X クライアント  —  起動中...", id="logo-sub")

    _closed = False

    def on_mount(self) -> None:
        self.set_timer(2.2, self._close)  # dismiss を直接 timer に渡すと await されて落ちる

    def action_skip(self) -> None:
        self._close()

    def on_key(self, event) -> None:
        event.stop()
        self._close()

    def _close(self) -> None:
        if not self._closed:
            self._closed = True
            self.dismiss()


# ---- 部品 --------------------------------------------------------------
class TweetItem(ListItem):
    def __init__(self, tweet: dict, deletable: bool = False):
        super().__init__()
        self.tweet = tweet
        self.deletable = deletable

    def compose(self) -> ComposeResult:
        yield Static(self._body())
        with Horizontal(classes="tw-actions"):
            actions = [
                ("♥ いいね", "like"),
                ("RT", "retweet"),
                ("★ ブクマ", "bookmark"),
                ("↩ 返信", "reply"),
                ("❞ 引用", "quote"),
                ("💬 会話", "conversation"),
            ]
            if self.tweet.get("quoted"):
                actions.append(("❝ 引用元", "quoted"))
            if self.tweet.get("media"):
                actions.append(("画像/動画", "media"))
            if self.deletable:
                actions.append(("🗑 削除", "delete"))
            for label, action in actions:
                btn = Button(label, classes="tw-btn")
                btn.tw_action = action
                btn.can_focus = False  # 矢印キーのリスト移動を邪魔しない
                yield btn

    def _body(self) -> str:
        t = self.tweet
        flags = []
        if t.get("favorited"):
            flags.append("[red]♥[/red]")
        if t.get("retweeted"):
            flags.append("[green]RT[/green]")
        if t.get("bookmarked"):
            flags.append("[yellow]★[/yellow]")
        fl = ("  " + " ".join(flags)) if flags else ""
        # 名前をクリックで著者プロフィールを開く(app 名前空間の action link)
        name = f"[b][@click=app.open_profile('{t['handle']}')]{escape(t['author'])}[/][/b]"
        head = f"{name} [dim]@{t['handle']}[/dim]{fl}"
        body = f"{head}\n{escape(t['text'])}"
        # 引用ポストは元ポストを引用ボックスで表示
        q = t.get("quoted")
        if q:
            qtext = escape(q["text"][:140])
            body += (
                f"\n[dim]  ┌─ 引用: [/dim][b]{escape(q['author'])}[/b] "
                f"[dim]@{q['handle']}[/dim]\n[dim]  │ {qtext}[/dim]"
            )
        meta = f"[dim]♥{t['likes']}  RT{t['retweets']}  {t.get('created_at','')}[/dim]"
        return f"{body}\n{meta}"

    def refresh_render(self) -> None:
        self.query_one(Static).update(self._body())


class SimpleItem(ListItem):
    def __init__(self, text: str, data: dict | None = None):
        super().__init__()
        self._text = text
        self.data = data or {}

    def compose(self) -> ComposeResult:
        yield Static(self._text)


class DMMsgItem(ListItem):
    """DMメッセージ1件(削除ボタン付き)。"""

    def __init__(self, text: str, msg_id: str):
        super().__init__()
        self._text = text
        self.msg_id = msg_id

    def compose(self) -> ComposeResult:
        yield Static(self._text)
        with Horizontal(classes="tw-actions"):
            btn = Button("🗑 削除", classes="dm-del-btn")
            btn.can_focus = False
            yield btn


# ---- モーダル ----------------------------------------------------------
class ComposeModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "キャンセル"), ("ctrl+s", "submit", "送信")]

    def __init__(self, title: str = "投稿", prefill: str = ""):
        super().__init__()
        self._title = title
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-box"):
            yield Label(self._title, id="compose-title")
            yield TextArea(self._prefill, id="compose-input")
            with Horizontal(id="compose-buttons"):
                yield Button("送信 (Ctrl+S)", variant="primary", id="send")
                yield Button("キャンセル", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#compose-input", TextArea).focus()

    @on(Button.Pressed, "#send")
    def _on_send(self) -> None:
        self.action_submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        text = self.query_one("#compose-input", TextArea).text.strip()
        self.dismiss(text or None)


class MediaModal(ModalScreen):
    """ツイートのメディアURL一覧と、画像のインラインプレビュー。"""

    BINDINGS = [("escape", "close", "閉じる")]

    def __init__(self, tweet: dict):
        super().__init__()
        self.tweet = tweet

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="media-box"):
            yield Label(f"メディア {len(self.tweet['media'])}件  (Esc で閉じる)")
            for i, m in enumerate(self.tweet["media"]):
                yield Static(f"[{i}] [b]{m['type']}[/b]: {m['url']}")
                if m["type"] == "photo":
                    yield Static("(プレビュー読込中...)", id=f"prev-{i}")

    def on_mount(self) -> None:
        self.load_previews()

    @work(thread=True)
    def load_previews(self) -> None:
        import io

        from PIL import Image
        from rich_pixels import Pixels

        ua = self.app.client.cfg["user_agent"]
        for i, m in enumerate(self.tweet["media"]):
            if m["type"] != "photo":
                continue
            data = imaging.fetch_image(m["url"], ua)
            if not data:
                continue
            try:
                img = Image.open(io.BytesIO(data))
                img.thumbnail((64, 40))
                px = Pixels.from_image(img)
            except Exception:  # noqa: BLE001
                continue
            self.app.call_from_thread(self._set, i, px)

    def _set(self, i: int, px) -> None:
        try:
            self.query_one(f"#prev-{i}", Static).update(px)
        except Exception:  # noqa: BLE001
            pass

    def action_close(self) -> None:
        self.dismiss()


class ConfirmModal(ModalScreen[bool]):
    """削除などの確認。はい=True / いいえ=False を返す。"""

    BINDINGS = [("escape", "cancel", "キャンセル")]

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._message)
            with Horizontal(id="confirm-buttons"):
                yield Button("はい(削除)", variant="error", id="yes")
                yield Button("いいえ", id="no")

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_cancel(self) -> None:
        self.dismiss(False)


class HandleModal(ModalScreen):
    """開くプロフィールのハンドルを入力。"""

    BINDINGS = [("escape", "cancel", "キャンセル")]

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-box"):
            yield Label("プロフィールを開く(ハンドル、@なし)")
            yield Input(id="handle-input")

    def on_mount(self) -> None:
        self.query_one("#handle-input", Input).focus()

    @on(Input.Submitted, "#handle-input")
    def _submit(self, ev: Input.Submitted) -> None:
        self.dismiss(ev.value.strip().lstrip("@") or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---- ツイート操作の共通アクション -------------------------------------
class TweetActions:
    """like/rt/bookmark トグル、メディア、プロフィールを提供する mixin。

    利用側は current_tweet_item() を実装すること。self.app.client を使う。
    """

    def current_tweet_item(self) -> TweetItem | None:
        raise NotImplementedError

    # --- item を明示的に受ける版(ボタン/キー共通)---
    def _do_like(self, item: TweetItem) -> None:
        t = item.tweet
        if t.get("favorited"):
            self._toggle(item, self.app.client.unlike, "favorited", "likes", -1, "いいね解除")
        else:
            self._toggle(item, self.app.client.like, "favorited", "likes", +1, "いいね")

    def _do_retweet(self, item: TweetItem) -> None:
        t = item.tweet
        if t.get("retweeted"):
            self._toggle(item, self.app.client.unretweet, "retweeted", "retweets", -1, "RT解除")
        else:
            self._toggle(item, self.app.client.retweet, "retweeted", "retweets", +1, "RT")

    def _do_bookmark(self, item: TweetItem) -> None:
        t = item.tweet
        if t.get("bookmarked"):
            self._toggle(item, self.app.client.unbookmark, "bookmarked", None, 0, "ブクマ解除")
        else:
            self._toggle(item, self.app.client.bookmark, "bookmarked", None, 0, "ブクマ")

    def _do_reply(self, item: TweetItem) -> None:
        t = item.tweet

        def after(text):
            if text:
                self.app.post_tweet(text, reply_to=t["id"])

        self.app.push_screen(ComposeModal(f"@{t['handle']} へ返信"), after)

    def action_like(self) -> None:
        item = self.current_tweet_item()
        if item:
            self._do_like(item)

    def action_retweet(self) -> None:
        item = self.current_tweet_item()
        if item:
            self._do_retweet(item)

    def action_bookmark(self) -> None:
        item = self.current_tweet_item()
        if item:
            self._do_bookmark(item)

    def _do_media(self, item: TweetItem) -> None:
        if item.tweet.get("media"):
            self.app.push_screen(MediaModal(item.tweet))

    def _do_quote(self, item: TweetItem) -> None:
        t = item.tweet
        url = f"https://x.com/{t['handle']}/status/{t['id']}"

        def after(text):
            if text:
                self.app.post_tweet(text, quote_url=url)

        self.app.push_screen(ComposeModal(f"引用: @{t['handle']}"), after)

    def _do_conversation(self, item: TweetItem) -> None:
        self.app.open_conversation(item.tweet["id"])

    def _do_quoted(self, item: TweetItem) -> None:
        q = item.tweet.get("quoted")
        if q and q.get("id"):
            self.app.open_conversation(q["id"])

    def _do_delete(self, item: TweetItem) -> None:
        def cb(ok: bool | None) -> None:
            if ok:
                self._delete_tweet_worker(item)

        self.app.push_screen(ConfirmModal("このツイートを削除しますか?"), cb)

    @work(thread=True, group="del")
    def _delete_tweet_worker(self, item: TweetItem) -> None:
        try:
            self.app.client.delete_tweet(item.tweet["id"])
            self.app.call_from_thread(self.notify, "削除しました")
            self.app.call_from_thread(item.remove)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.notify, f"削除失敗: {exc}", severity="error")

    def action_open_profile(self, handle: str) -> None:
        """ツイート名クリック(action link)から著者プロフィールを開く。"""
        self.app.open_profile(handle)

    def dispatch_tweet_button(self, button: Button) -> None:
        node = button
        while node is not None and not isinstance(node, TweetItem):
            node = node.parent
        if node is None:
            return
        action = getattr(button, "tw_action", "")
        {
            "like": self._do_like,
            "retweet": self._do_retweet,
            "bookmark": self._do_bookmark,
            "reply": self._do_reply,
            "quote": self._do_quote,
            "media": self._do_media,
            "delete": self._do_delete,
            "conversation": self._do_conversation,
            "quoted": self._do_quoted,
        }.get(action, lambda i: None)(node)

    @work(thread=True, group="tw-act")
    def _toggle(self, item, fn, field, count_field, delta, verb) -> None:
        t = item.tweet
        try:
            fn(t["id"])
            t[field] = not t.get(field, False)
            if count_field:
                t[count_field] = max(0, t.get(count_field, 0) + delta)
            self.app.call_from_thread(item.refresh_render)
            self.app.call_from_thread(self.notify, f"{verb} OK")
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(
                self.notify, f"{verb}失敗: {exc}", severity="error"
            )

    def action_media(self) -> None:
        item = self.current_tweet_item()
        if not item:
            return
        if not item.tweet.get("media"):
            self.notify("このツイートにメディアはありません")
            return
        self.app.push_screen(MediaModal(item.tweet))

    def action_open_author(self) -> None:
        item = self.current_tweet_item()
        if not item or not item.tweet.get("handle"):
            return
        self.app.open_profile(item.tweet["handle"])

    def action_reply(self) -> None:
        item = self.current_tweet_item()
        if item:
            self._do_reply(item)


# ---- プロフィール画面 --------------------------------------------------
class ProfileScreen(TweetActions, Screen):
    BINDINGS = [("escape", "app.pop_screen", "戻る")]

    def __init__(self, handle: str, is_self: bool):
        super().__init__()
        self.handle = handle
        self.is_self = is_self
        self.uid: str | None = None
        self.following = False
        self.muting = False
        self.blocking = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("読み込み中...", id="profile-header")
        with Horizontal(id="profile-actions"):
            if not self.is_self:
                yield Button("フォロー", variant="primary", id="btn-follow")
                yield Button("ミュート", id="btn-mute")
                yield Button("ブロック", id="btn-block")
        with TabbedContent(id="profile-tabs"):
            with TabPane("ツイート", id="p-tweets"):
                yield ListView(id="p-tweets-list")
            if self.is_self:
                with TabPane("いいね", id="p-likes"):
                    yield ListView(id="p-likes-list")
                with TabPane("ブクマ", id="p-bm"):
                    yield ListView(id="p-bm-list")
        yield Footer()

    def on_mount(self) -> None:
        self.load_profile()

    @work(thread=True)
    def load_profile(self) -> None:
        client = self.app.client
        data = client.user_by_screen_name(self.handle)
        result = data.get("data", {}).get("user", {}).get("result", {})
        legacy = result.get("legacy", {})
        core = result.get("core", {})
        self.uid = result.get("rest_id")
        self.following = legacy.get("following", False)
        self.muting = legacy.get("muting", False)
        self.blocking = legacy.get("blocking", False)
        name = legacy.get("name") or core.get("name", "")
        header = (
            f"[b]{escape(name)}[/b]  @{self.handle}  "
            f"フォロワー{legacy.get('followers_count','?')} / "
            f"フォロー{legacy.get('friends_count','?')}\n"
            f"{escape(legacy.get('description',''))}"
        )
        self.app.call_from_thread(
            self.query_one("#profile-header", Static).update, header
        )
        if not self.is_self:
            self.app.call_from_thread(self._update_action_btns)
        if not self.uid:
            return
        tweets = commands.extract_tweets(client.user_tweets(self.uid, count=30))[:30]
        self.app.call_from_thread(self._fill, "#p-tweets-list", tweets, self.is_self)
        if self.is_self:
            likes = commands.extract_tweets(client.likes(self.uid, count=30))[:30]
            self.app.call_from_thread(self._fill, "#p-likes-list", likes, False)
            bms = commands.extract_tweets(client.bookmarks(count=30))[:30]
            self.app.call_from_thread(self._fill, "#p-bm-list", bms, False)

    def _fill(self, list_id: str, tweets: list[dict], deletable: bool = False) -> None:
        lv = self.query_one(list_id, ListView)
        lv.clear()
        for t in tweets:
            lv.append(TweetItem(t, deletable=deletable))

    def _update_action_btns(self) -> None:
        def setlabel(bid, label, variant):
            try:
                b = self.query_one(bid, Button)
                b.label = label
                b.variant = variant
            except Exception:  # noqa: BLE001
                pass

        setlabel("#btn-follow", "フォロー中(解除)" if self.following else "フォロー",
                 "default" if self.following else "primary")
        setlabel("#btn-mute", "ミュート解除" if self.muting else "ミュート", "default")
        setlabel("#btn-block", "ブロック解除" if self.blocking else "ブロック",
                 "error" if not self.blocking else "default")

    def current_tweet_item(self) -> TweetItem | None:
        active = self.query_one("#profile-tabs", TabbedContent).active
        lid = {
            "p-tweets": "#p-tweets-list",
            "p-likes": "#p-likes-list",
            "p-bm": "#p-bm-list",
        }.get(active)
        if not lid:
            return None
        item = self.query_one(lid, ListView).highlighted_child
        return item if isinstance(item, TweetItem) else None

    @on(Button.Pressed, ".tw-btn")
    def _tw_btn(self, ev: Button.Pressed) -> None:
        ev.stop()
        self.dispatch_tweet_button(ev.button)

    @on(Button.Pressed, "#btn-follow")
    def _on_follow(self) -> None:
        if self.uid:
            self._relation("follow")

    @on(Button.Pressed, "#btn-mute")
    def _on_mute(self) -> None:
        if self.uid:
            self._relation("mute")

    @on(Button.Pressed, "#btn-block")
    def _on_block(self) -> None:
        if self.uid:
            self._relation("block")

    @work(thread=True)
    def _relation(self, kind: str) -> None:
        c = self.app.client
        try:
            if kind == "follow":
                (c.unfollow if self.following else c.follow)(self.uid)
                self.following = not self.following
                msg = "フォロー解除" if not self.following else "フォロー"
            elif kind == "mute":
                (c.unmute if self.muting else c.mute)(self.uid)
                self.muting = not self.muting
                msg = "ミュート解除" if not self.muting else "ミュート"
            else:  # block
                (c.unblock if self.blocking else c.block)(self.uid)
                self.blocking = not self.blocking
                msg = "ブロック解除" if not self.blocking else "ブロック"
            self.app.call_from_thread(self.notify, f"{msg}しました")
            self.app.call_from_thread(self._update_action_btns)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.notify, f"失敗: {exc}", severity="error")


# ---- 会話スレッド画面 --------------------------------------------------
class ConversationScreen(TweetActions, Screen):
    BINDINGS = [("escape", "app.pop_screen", "戻る")]

    def __init__(self, tweet_id: str):
        super().__init__()
        self.tweet_id = tweet_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("会話を読み込み中...", id="conv-header")
        yield ListView(id="conv-list")
        yield Footer()

    def on_mount(self) -> None:
        self.load_conv()

    @work(thread=True)
    def load_conv(self) -> None:
        try:
            data = self.app.client.tweet_detail(self.tweet_id)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(
                self.query_one("#conv-header", Static).update, f"取得失敗: {exc}"
            )
            return
        tweets = commands.extract_tweets(data)[:50]
        self.app.call_from_thread(self._fill, tweets)
        self.app.call_from_thread(
            self.query_one("#conv-header", Static).update,
            f"会話・リプライ({len(tweets)}件)  Esc で戻る",
        )

    def _fill(self, tweets: list[dict]) -> None:
        lv = self.query_one("#conv-list", ListView)
        lv.clear()
        for t in tweets:
            lv.append(TweetItem(t))

    def current_tweet_item(self) -> TweetItem | None:
        item = self.query_one("#conv-list", ListView).highlighted_child
        return item if isinstance(item, TweetItem) else None

    @on(Button.Pressed, ".tw-btn")
    def _tw_btn(self, ev: Button.Pressed) -> None:
        ev.stop()
        self.dispatch_tweet_button(ev.button)


# ---- アプリ本体 --------------------------------------------------------
class XcliApp(TweetActions, App):
    CSS = """
    Screen { layout: vertical; }
    ListView { height: 1fr; border: round $primary; }
    TweetItem { padding: 1 1; height: auto; border-bottom: solid $primary-darken-2; }
    SimpleItem { padding: 0 1; height: auto; border-bottom: dashed $primary-darken-3; }
    .tw-actions { height: auto; margin-top: 1; }
    .tw-btn { height: 1; min-width: 8; margin-right: 2; border: none; background: $boost; color: $text; }
    .tw-btn:hover { background: $primary; }
    .dm-del-btn { height: 1; min-width: 8; border: none; background: $error 40%; color: $text; }
    .dm-del-btn:hover { background: $error; }
    #topbar { height: auto; padding: 0 1; }
    #btn-me { min-width: 20; margin-right: 1; }
    #tl-compose { width: 1fr; }
    #logo-box { width: 100%; height: 100%; align: center middle; background: $background; }
    #logo-art { color: $primary; text-style: bold; width: auto; }
    #logo-sub { color: $text-muted; width: auto; content-align: center middle; margin-top: 1; }
    #confirm-box { width: 60%; height: auto; padding: 1 2; background: $panel; border: thick $error; }
    #confirm-buttons { height: auto; align: right middle; }
    #profile-header { height: auto; padding: 1 1; background: $panel; }
    #profile-actions { height: auto; padding: 0 1; }
    #compose-box { width: 80%; height: auto; padding: 1 2; background: $panel; border: thick $primary; }
    #compose-input { height: 8; }
    #compose-buttons { height: auto; align: right middle; }
    #media-box { width: 90%; height: 80%; padding: 1 2; background: $panel; border: thick $primary; }
    #search-controls { height: auto; }
    #search-adv { height: auto; }
    #search-input { width: 2fr; }
    #search-product { width: 1fr; }
    #dm-send-row { height: auto; }
    #status { height: auto; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        ("q", "quit", "終了"),
        ("f5", "refresh", "更新"),
    ]

    def __init__(self):
        super().__init__()
        self.client: XClient | None = None
        self.me: str = ""
        self._feeds: dict = {}  # list_id -> {cursor, fetch, deletable, loading, seen}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="topbar"):
            yield Button("自分のプロフィール", id="btn-me")
            yield Input(placeholder="いまどうしてる?  (Enter で投稿)", id="tl-compose")
        with TabbedContent(initial="following"):
            with TabPane("フォロー中", id="following"):
                yield ListView(id="following-list")
            with TabPane("おすすめ", id="foryou"):
                yield ListView(id="foryou-list")
            with TabPane("検索", id="search"):
                with Horizontal(id="search-controls"):
                    yield Input(placeholder="検索クエリ (Enter)", id="search-input")
                    yield Select(
                        [
                            ("最新", "latest"),
                            ("人気", "top"),
                            ("アカウント", "people"),
                            ("メディア", "media"),
                            ("リスト", "lists"),
                        ],
                        value="latest",
                        allow_blank=False,
                        id="search-product",
                    )
                with Horizontal(id="search-adv"):
                    yield Input(placeholder="from: ユーザー(@なし)", id="search-from")
                    yield Input(placeholder="最低いいね数", id="search-minfav")
                    yield Button("検索", variant="primary", id="search-go")
                yield ListView(id="search-list")
            with TabPane("通知", id="notif"):
                yield ListView(id="notif-list")
            with TabPane("DM", id="dm"):
                with Horizontal():
                    yield ListView(id="dm-conv-list")
                    yield ListView(id="dm-msg-list")
                with Horizontal(id="dm-send-row"):
                    yield Input(placeholder="メッセージ (Enter で送信)", id="dm-input")
        yield Static("起動中...", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Xcli"
        self.sub_title = "ターミナルUI"
        self.push_screen(LogoScreen())  # 起動スプラッシュ
        try:
            self.client = XClient()
        except Exception as exc:  # noqa: BLE001
            self._status(f"認証エラー: {exc}")
            return
        self.init_me()
        self.load_following()
        self.load_foryou()
        self.load_notifications()
        self.load_dm_inbox()

    @work(thread=True)
    def init_me(self) -> None:
        self.me = self.client.whoami()

    # ---- ユーティリティ ------------------------------------------------
    def _status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    def current_tweet_item(self) -> TweetItem | None:
        active = self.query_one(TabbedContent).active
        lid = {
            "following": "#following-list",
            "foryou": "#foryou-list",
            "search": "#search-list",
        }.get(active)
        if not lid:
            return None
        item = self.query_one(lid, ListView).highlighted_child
        return item if isinstance(item, TweetItem) else None

    def open_profile(self, handle: str) -> None:
        is_self = handle.lower() == (self.me or "").lower()
        self.push_screen(ProfileScreen(handle, is_self))

    def open_conversation(self, tweet_id: str) -> None:
        self.push_screen(ConversationScreen(tweet_id))

    # ---- データ読み込み ------------------------------------------------
    def _fill_tweets(self, list_id: str, tweets: list[dict]) -> None:
        lv = self.query_one(list_id, ListView)
        lv.clear()
        for t in tweets:
            lv.append(TweetItem(t))

    def _fill_simple(self, list_id: str, rows: list[tuple[str, dict]]) -> None:
        lv = self.query_one(list_id, ListView)
        lv.clear()
        for text, data in rows:
            lv.append(SimpleItem(text, data))

    @work(thread=True, group="feed")
    def _load_feed(self, list_id, fetch, reset, deletable=False) -> None:
        """カーソル対応の汎用フィード読み込み。reset=Falseで末尾に追記(追加読み込み)。"""
        st = self._feeds.setdefault(list_id, {"seen": set()})
        if st.get("loading"):
            return
        st["loading"] = True
        try:
            cursor = None if reset else st.get("cursor")
            if reset:
                st["seen"] = set()
            data = fetch(cursor)
            seen = st["seen"]
            fresh = [t for t in commands.extract_tweets(data) if t["id"] not in seen]
            for t in fresh:
                seen.add(t["id"])
            st["cursor"] = commands.extract_cursor(data)
            st["fetch"] = fetch
            st["deletable"] = deletable

            def apply():
                lv = self.query_one(list_id, ListView)
                if reset:
                    lv.clear()
                for t in fresh:
                    lv.append(TweetItem(t, deletable=deletable))

            self.app.call_from_thread(apply)
        finally:
            st["loading"] = False

    def load_following(self) -> None:
        self._status("フォロー中を取得中...")
        self._load_feed(
            "#following-list",
            lambda cur: self.client.home_timeline(count=30, cursor=cur, latest=True),
            reset=True,
        )

    def load_foryou(self) -> None:
        self._load_feed(
            "#foryou-list",
            lambda cur: self.client.home_timeline(count=30, cursor=cur, latest=False),
            reset=True,
        )

    @work(thread=True, exclusive=True, group="notif")
    def load_notifications(self) -> None:
        notifs = commands.extract_notifications(self.client.notifications(count=30))[:30]
        self.app.call_from_thread(
            self._fill_simple, "#notif-list", [(n["text"], n) for n in notifs]
        )

    @work(thread=True, exclusive=True, group="dm")
    def load_dm_inbox(self) -> None:
        convs = commands.parse_inbox(self.client.dm_inbox())[:30]
        rows = [(f"{c['with']}  —  {c['last'][:30]}", c) for c in convs]
        self.app.call_from_thread(self._fill_simple, "#dm-conv-list", rows)

    @work(thread=True, exclusive=True, group="dmmsg")
    def load_dm_messages(self, conv_id: str) -> None:
        ct = self.client.dm_conversation(conv_id).get("conversation_timeline", {})
        users = ct.get("users", {})
        rows = []
        for e in ct.get("entries", []):
            m = e.get("message")
            if not m:
                continue
            md = m.get("message_data", {})
            u = users.get(md.get("sender_id"), {})
            who = f"@{u.get('screen_name', '?')}"
            rows.append((int(m.get("id", 0)), f"{who}: {md.get('text','')}", m.get("id")))
        rows.sort(key=lambda x: x[0])
        self.app.call_from_thread(self._fill_dm_messages, rows)

    def _fill_dm_messages(self, rows: list) -> None:
        lv = self.query_one("#dm-msg-list", ListView)
        lv.clear()
        for _, text, mid in rows:
            lv.append(DMMsgItem(text, mid))

    def _do_search(self) -> None:
        q = self.query_one("#search-input", Input).value.strip()
        frm = self.query_one("#search-from", Input).value.strip()
        minf = self.query_one("#search-minfav", Input).value.strip()
        if frm:
            q += f" from:{frm.lstrip('@')}"
        if minf.isdigit():
            q += f" min_faves:{minf}"
        q = q.strip()
        if not q:
            return
        self.run_search(q, self.query_one("#search-product", Select).value)

    def run_search(self, query: str, product: str) -> None:
        prod_map = {
            "top": "Top", "latest": "Latest", "people": "People",
            "media": "Media", "lists": "Lists",
        }
        self._status(f"検索中: {query} [{product}]")
        if product in ("people", "lists"):
            self._feeds.pop("#search-list", None)  # 追加読み込み無効
            self._run_search_other(query, prod_map[product], product)
        else:
            # ツイート系はカーソル対応(追加読み込み可)
            self._load_feed(
                "#search-list",
                lambda cur: self.client.search(
                    query, product=prod_map[product], count=30, cursor=cur
                ),
                reset=True,
            )

    @work(thread=True, group="search")
    def _run_search_other(self, query: str, product: str, kind: str) -> None:
        data = self.client.search(query, product=product, count=30)
        if kind == "people":
            items = commands.extract_users(data)[:30]
            rows = [
                (f"[b]{u['name']}[/b] @{u['handle']}  (フォロワー{u['followers']})\n"
                 f"[dim]{u['desc'][:70]}[/dim]", u)
                for u in items
            ]
        else:
            items = commands.extract_lists(data)[:30]
            rows = [
                (f"[b]{x['name']}[/b] by @{x['by']}  メンバー{x['members']}", x)
                for x in items
            ]
        self.app.call_from_thread(self._fill_simple, "#search-list", rows)
        self.app.call_from_thread(self._status, f"検索 '{query}' [{kind}] {len(items)}件")

    @on(ListView.Highlighted)
    def _on_highlight(self, ev: ListView.Highlighted) -> None:
        """末尾までスクロールしたら追加読み込み(無限スクロール)。"""
        lv = ev.list_view
        if lv.id is None or ev.item is None:
            return
        st = self._feeds.get("#" + lv.id)
        if not st or st.get("loading") or not st.get("cursor"):
            return
        if lv.children and ev.item is lv.children[-1]:
            self._load_feed(
                "#" + lv.id, st["fetch"], reset=False, deletable=st.get("deletable", False)
            )

    # ---- イベント ------------------------------------------------------
    @on(Input.Submitted, "#search-input")
    @on(Input.Submitted, "#search-from")
    @on(Input.Submitted, "#search-minfav")
    @on(Button.Pressed, "#search-go")
    def _on_search(self, ev) -> None:
        self._do_search()

    @on(Button.Pressed, ".tw-btn")
    def _tw_btn(self, ev: Button.Pressed) -> None:
        ev.stop()
        self.dispatch_tweet_button(ev.button)

    @on(Input.Submitted, "#tl-compose")
    def _on_tl_compose(self, ev: Input.Submitted) -> None:
        text = ev.value.strip()
        if text:
            self.post_tweet(text)
            self.query_one("#tl-compose", Input).value = ""

    @on(Input.Submitted, "#dm-input")
    def _on_dm_send(self, ev: Input.Submitted) -> None:
        item = self.query_one("#dm-conv-list", ListView).highlighted_child
        if not isinstance(item, SimpleItem) or not ev.value.strip():
            return
        conv_id = item.data.get("conversation_id")
        other = conv_id.split("-")[-1] if conv_id else None
        if other:
            self.send_dm(other, ev.value.strip(), conv_id)
            self.query_one("#dm-input", Input).value = ""

    @on(ListView.Selected, "#dm-conv-list")
    def _on_conv_selected(self, ev: ListView.Selected) -> None:
        if isinstance(ev.item, SimpleItem):
            cid = ev.item.data.get("conversation_id")
            if cid:
                self.load_dm_messages(cid)

    @on(Button.Pressed, "#btn-me")
    def _on_btn_me(self) -> None:
        if self.me:
            self.open_profile(self.me)
        else:
            self.notify("自分の情報取得中です")

    @on(Button.Pressed, ".dm-del-btn")
    def _on_dm_del(self, ev: Button.Pressed) -> None:
        ev.stop()
        node = ev.button
        while node is not None and not isinstance(node, DMMsgItem):
            node = node.parent
        if node is None:
            return

        def cb(ok: bool | None) -> None:
            if ok:
                self._dm_delete(node)

        self.push_screen(ConfirmModal("このDMを削除しますか?(自分側のみ)"), cb)

    # ---- アクション ----------------------------------------------------
    def action_refresh(self) -> None:
        active = self.query_one(TabbedContent).active
        {
            "following": self.load_following,
            "foryou": self.load_foryou,
            "notif": self.load_notifications,
            "dm": self.load_dm_inbox,
        }.get(active, lambda: None)()

    @work(thread=True, group="post")
    def post_tweet(
        self, text: str, reply_to: str | None = None, quote_url: str | None = None
    ) -> None:
        try:
            data = self.client.create_tweet(
                text, reply_to=reply_to, attachment_url=quote_url
            )
            tid = commands._created_tweet_id(data)
            self.app.call_from_thread(
                self.notify, f"投稿しました: {tid}" if tid else "投稿応答不明"
            )
            self.app.call_from_thread(self.load_following)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.notify, f"投稿失敗: {exc}", severity="error")

    @work(thread=True, group="dmsend")
    def send_dm(self, recipient_id: str, text: str, conv_id: str) -> None:
        try:
            self.client.dm_send(recipient_id, text)
            self.app.call_from_thread(self.notify, "DM送信しました")
            self.app.call_from_thread(self.load_dm_messages, conv_id)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.notify, f"DM送信失敗: {exc}", severity="error")

    @work(thread=True, group="dmdel")
    def _dm_delete(self, item: DMMsgItem) -> None:
        try:
            self.client.dm_delete_message(item.msg_id)
            self.app.call_from_thread(self.notify, "DMを削除しました")
            self.app.call_from_thread(item.remove)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.notify, f"DM削除失敗: {exc}", severity="error")


def run() -> int:
    XcliApp().run()
    return 0
