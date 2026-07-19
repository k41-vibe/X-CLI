"""Xcli エントリポイント。`xcli <command>` / `python -m xcli <command>`。"""
from __future__ import annotations

import argparse
import sys

from . import __version__, commands
from .client import AuthError, XApiError


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="結果をJSONで出力(エクスポート/連携用)"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xcli", description="X(Twitter)非公式 GraphQL API の自己利用 CLI"
    )
    p.add_argument("--version", action="version", version=f"xcli {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("post", help="ツイートを投稿")
    sp.add_argument("text", help="本文")
    sp.add_argument("--reply", help="返信先ツイートID", default=None)
    sp.add_argument(
        "--media", nargs="+", metavar="FILE", help="添付する画像/動画/GIF(複数可)"
    )
    sp.set_defaults(func=commands.cmd_post)

    sth = sub.add_parser("thread", help="スレッド投稿(連続ツイート)")
    sth.add_argument("texts", nargs="+", help="各ツイート本文(順に並べる)")
    sth.add_argument("--reply", help="スレッドの起点にする既存ツイートID", default=None)
    sth.set_defaults(func=commands.cmd_thread)

    sd = sub.add_parser("delete", help="ツイートを削除")
    sd.add_argument("tweet_id", help="削除するツイートID")
    sd.set_defaults(func=commands.cmd_delete)

    for name, help_text, func in [
        ("like", "いいね", commands.cmd_like),
        ("unlike", "いいね解除", commands.cmd_unlike),
        ("rt", "リツイート", commands.cmd_rt),
        ("unrt", "リツイート解除", commands.cmd_unrt),
        ("bookmark", "ブックマーク", commands.cmd_bookmark),
        ("unbookmark", "ブックマーク解除", commands.cmd_unbookmark),
    ]:
        sm = sub.add_parser(name, help=help_text)
        sm.add_argument("tweet_id", help="対象ツイートID")
        sm.set_defaults(func=func)

    for name, help_text, func in [
        ("follow", "フォロー(鍵垢はリクエスト送信)", commands.cmd_follow),
        ("unfollow", "フォロー解除", commands.cmd_unfollow),
        ("mute", "ミュート", commands.cmd_mute),
        ("unmute", "ミュート解除", commands.cmd_unmute),
        ("block", "ブロック", commands.cmd_block),
        ("unblock", "ブロック解除", commands.cmd_unblock),
    ]:
        sr = sub.add_parser(name, help=help_text)
        sr.add_argument("target", help="ハンドル(@なし)または user_id")
        sr.set_defaults(func=func)

    sq = sub.add_parser("quote", help="引用して投稿")
    sq.add_argument("tweet_id", help="引用元ツイートID")
    sq.add_argument("text", help="本文")
    sq.set_defaults(func=commands.cmd_quote)

    sconv = sub.add_parser("conversation", aliases=["replies"], help="会話・リプライツリーを表示")
    sconv.add_argument("tweet_id", help="ツイートID")
    sconv.add_argument("-n", "--count", type=int, default=30, help="取得件数")
    _add_json(sconv)
    sconv.set_defaults(func=commands.cmd_conversation)

    swho = sub.add_parser("whoami", help="認証中の自分のハンドルを表示")
    swho.set_defaults(func=commands.cmd_whoami)

    su = sub.add_parser("user", help="ユーザー情報(+任意でツイート)を取得")
    su.add_argument("handle", help="スクリーンネーム(@なし)")
    su.add_argument("--tweets", action="store_true", help="最新ツイートも取得")
    su.add_argument("-n", "--count", type=int, default=20, help="取得件数")
    su.add_argument("--images", action="store_true", help="画像を端末にインライン表示")
    su.add_argument("--no-images", action="store_true", help="画像表示を無効(config が on の時)")
    _add_json(su)
    su.set_defaults(func=commands.cmd_user)

    st = sub.add_parser("timeline", help="ホームタイムラインを取得")
    st.add_argument("-n", "--count", type=int, default=20, help="取得件数")
    st.add_argument(
        "--algo", action="store_true", help="おすすめ(アルゴ順)。既定はフォロー中・時系列"
    )
    st.add_argument("--images", action="store_true", help="画像を端末にインライン表示")
    st.add_argument("--no-images", action="store_true", help="画像表示を無効(config が on の時)")
    _add_json(st)
    st.set_defaults(func=commands.cmd_timeline)

    sdms = sub.add_parser("dms", help="DM受信箱(会話一覧)")
    sdms.add_argument("-n", "--count", type=int, default=20, help="表示件数")
    _add_json(sdms)
    sdms.set_defaults(func=commands.cmd_dms)

    sdm = sub.add_parser("dm", help="DMを送信")
    sdm.add_argument("target", help="送信先ハンドル(@なし)または user_id")
    sdm.add_argument("text", help="本文")
    sdm.set_defaults(func=commands.cmd_dm)

    sdmr = sub.add_parser("dm-read", help="会話の全メッセージを表示")
    sdmr.add_argument("target", help="ハンドル または 会話ID(数字-数字)")
    _add_json(sdmr)
    sdmr.set_defaults(func=commands.cmd_dm_read)

    sdmd = sub.add_parser("dm-delete", help="DMメッセージを削除")
    sdmd.add_argument("message_id", help="削除するメッセージID(dm-read で確認)")
    sdmd.set_defaults(func=commands.cmd_dm_delete)

    sdmdc = sub.add_parser("dm-delete-conv", help="会話ごと削除")
    sdmdc.add_argument("target", help="ハンドル または 会話ID")
    sdmdc.set_defaults(func=commands.cmd_dm_delete_conv)

    smedia = sub.add_parser("media", help="ツイートの画像/動画URLを取得")
    smedia.add_argument("tweet_id", help="対象ツイートID")
    smedia.add_argument("--show", action="store_true", help="画像を端末にインライン表示")
    smedia.add_argument("--open", action="store_true", help="既定ブラウザで開く")
    smedia.add_argument("--download", metavar="DIR", help="指定フォルダに保存")
    smedia.set_defaults(func=commands.cmd_media)

    sl = sub.add_parser("likes", help="いいね一覧(自分の分のみ見える)")
    sl.add_argument("target", help="ハンドル(@なし)または user_id")
    sl.add_argument("-n", "--count", type=int, default=20, help="取得件数")
    sl.add_argument("--images", action="store_true", help="画像を端末にインライン表示")
    sl.add_argument("--no-images", action="store_true", help="画像表示を無効(config が on の時)")
    _add_json(sl)
    sl.set_defaults(func=commands.cmd_likes)

    sb = sub.add_parser("bookmarks", help="ブックマーク一覧")
    sb.add_argument("-n", "--count", type=int, default=20, help="取得件数")
    sb.add_argument("--images", action="store_true", help="画像を端末にインライン表示")
    sb.add_argument("--no-images", action="store_true", help="画像表示を無効(config が on の時)")
    _add_json(sb)
    sb.set_defaults(func=commands.cmd_bookmarks)

    sn = sub.add_parser("notifications", aliases=["notif"], help="通知一覧")
    sn.add_argument("-n", "--count", type=int, default=40, help="取得件数")
    _add_json(sn)
    sn.set_defaults(func=commands.cmd_notifications)

    ssearch = sub.add_parser("search", help="検索(タブは --tab で指定)")
    ssearch.add_argument("query", help="検索クエリ")
    ssearch.add_argument(
        "--tab",
        choices=["top", "latest", "people", "media", "lists"],
        default="latest",
        help="最新/人気/アカウント/メディア/リスト(既定: latest)",
    )
    ssearch.add_argument("-n", "--count", type=int, default=20, help="取得件数")
    ssearch.add_argument("--images", action="store_true", help="画像を端末にインライン表示")
    ssearch.add_argument("--no-images", action="store_true", help="画像表示を無効(config が on の時)")
    _add_json(ssearch)
    ssearch.set_defaults(func=commands.cmd_search)

    stui = sub.add_parser("tui", help="ターミナルUIを起動")
    stui.add_argument("--lang", choices=["ja", "en"], default="ja", help="UI言語(既定 ja)")
    stui.add_argument("--en", action="store_true", help="英語UIで起動(--lang en の別名)")
    stui.set_defaults(func=commands.cmd_tui)

    ss = sub.add_parser("sync", help="公開JSから queryId を再取得してキャッシュ")
    ss.set_defaults(func=commands.cmd_sync)

    sc = sub.add_parser("config", help="設定の表示 / 画像表示ON/OFF")
    sc.add_argument(
        "--images",
        choices=["on", "off"],
        help="画像インライン表示の既定を保存(on/off)",
    )
    sc.add_argument(
        "--protocol",
        choices=["ansi", "iterm2"],
        help="画像表示方式を保存(ansi=全端末 / iterm2=iTerm2系のみ)",
    )
    sc.set_defaults(func=commands.cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows コンソールでの日本語文字化けを防ぐ。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AuthError as exc:
        print(f"[認証エラー] {exc}", file=sys.stderr)
        return 2
    except XApiError as exc:
        print(f"[APIエラー] {exc}", file=sys.stderr)
        if exc.body:
            print(exc.body, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
