"""各コマンドの実装と、GraphQL 応答の見やすい整形。"""
from __future__ import annotations

import json
from typing import Any, Iterable

from . import config, endpoints, imaging
from .client import XApiError, XClient


# ---- 応答パース --------------------------------------------------------
def _walk(node: Any) -> Iterable[dict]:
    """ネストした応答を再帰的に走査し dict を全て列挙。"""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def media_urls(legacy: dict) -> list[dict]:
    """ツイートの legacy から実メディアURLを抽出(画像は原寸、動画は最高画質mp4)。"""
    out: list[dict] = []
    ext = legacy.get("extended_entities") or legacy.get("entities") or {}
    for m in ext.get("media", []):
        mtype = m.get("type")
        if mtype == "photo":
            url = m.get("media_url_https", "")
            if url:
                out.append({"type": "photo", "url": url + "?name=orig"})
        elif mtype in ("video", "animated_gif"):
            variants = (m.get("video_info", {}) or {}).get("variants", [])
            mp4 = [
                v
                for v in variants
                if v.get("content_type") == "video/mp4" and v.get("url")
            ]
            if mp4:
                best = max(mp4, key=lambda v: v.get("bitrate", 0))
                out.append({"type": mtype, "url": best["url"]})
    return out


def _unwrap_tweet(result: dict) -> dict:
    """TweetWithVisibilityResults ラッパーを剥がして実ツイートを返す。"""
    if result.get("__typename") == "TweetWithVisibilityResults":
        return result.get("tweet", {})
    return result


def _author(result: dict) -> tuple[str, str]:
    user_res = result.get("core", {}).get("user_results", {}).get("result", {})
    u_legacy = user_res.get("legacy", {})
    u_core = user_res.get("core", {})
    name = u_legacy.get("name") or u_core.get("name", "")
    handle = u_legacy.get("screen_name") or u_core.get("screen_name", "")
    return name, handle


def extract_tweets(data: dict, include_promoted: bool = False) -> list[dict]:
    """timeline 応答の「トップレベル項目」だけを抽出(引用・広告は除外)。

    itemContent.itemType == "TimelineTweet" のエントリのみを拾うことで、
    引用ツイートやおすすめモジュールの入れ子ツイートを混ぜない。
    """
    tweets: list[dict] = []
    seen: set[str] = set()
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        ic = node.get("itemContent")
        if not (isinstance(ic, dict) and ic.get("itemType") == "TimelineTweet"):
            continue
        if not include_promoted and ic.get("promotedMetadata"):
            continue  # 広告枠

        result = _unwrap_tweet(ic.get("tweet_results", {}).get("result", {}))
        legacy = result.get("legacy", {})
        tid = result.get("rest_id") or legacy.get("id_str")
        if not tid or tid in seen:
            continue
        seen.add(tid)

        # リツイートなら元ツイートを本文に、RT元表示も付ける
        rt_src = legacy.get("retweeted_status_result", {}).get("result")
        is_rt = bool(rt_src)
        name, handle = _author(result)
        if is_rt:
            disp_result = _unwrap_tweet(rt_src)
            disp = disp_result.get("legacy", {})
            src_name, src_handle = _author(disp_result)
            text = disp.get("full_text", legacy.get("full_text", ""))
            author = f"{src_name} ← RT: {name}"
            handle = src_handle or handle
        else:
            disp_result = result
            disp = legacy
            text = legacy.get("full_text", "")
            author = name

        # 引用ポスト(表示中ツイートが別ツイートを引用している場合)
        quoted = None
        qr = disp_result.get("quoted_status_result", {}).get("result")
        if qr:
            q = _unwrap_tweet(qr)
            ql = q.get("legacy", {})
            qn, qh = _author(q)
            quoted = {
                "id": q.get("rest_id") or ql.get("id_str"),
                "author": qn,
                "handle": qh,
                "text": ql.get("full_text", ""),
            }

        tweets.append(
            {
                "id": tid,
                "author": author,
                "handle": handle,
                "text": text,
                "likes": disp.get("favorite_count", 0),
                "retweets": disp.get("retweet_count", 0),
                "created_at": legacy.get("created_at", ""),
                "is_rt": is_rt,
                "media": media_urls(disp),
                "favorited": disp.get("favorited", False),
                "retweeted": disp.get("retweeted", False),
                "bookmarked": disp.get("bookmarked", False),
                "quoted": quoted,
            }
        )
    return tweets


def extract_cursor(data: dict, cursor_type: str = "Bottom") -> str | None:
    """次ページ取得用の Bottom カーソルを応答から抜く。"""
    for node in _walk(data):
        if (
            isinstance(node, dict)
            and node.get("cursorType") == cursor_type
            and node.get("value")
        ):
            return node["value"]
    return None


def _collect(fetch, count: int, extractor=extract_tweets, max_pages: int = 60) -> list[dict]:
    """fetch(cursor)->応答 をカーソルで辿り count 件集める。

    extractor で項目種別(ツイート/ユーザー/リスト)を切替。重複除去しつつ、
    指定件数に達するか、新規が尽きるか、max_pages に達したら止める。
    """
    items: list[dict] = []
    seen: set[str] = set()
    cursor: str | None = None
    for _ in range(max_pages):
        data = fetch(cursor)
        page = extractor(data)
        fresh = [t for t in page if t["id"] not in seen]
        for t in fresh:
            seen.add(t["id"])
        items.extend(fresh)
        if len(items) >= count:
            break
        cursor = extract_cursor(data)
        if not cursor or not fresh:
            break  # フィード末尾
    return items[:count]


def extract_users(data: dict) -> list[dict]:
    """検索(People)応答からユーザーを抽出。"""
    users: list[dict] = []
    seen: set[str] = set()
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        ic = node.get("itemContent")
        if not (isinstance(ic, dict) and ic.get("itemType") == "TimelineUser"):
            continue
        res = ic.get("user_results", {}).get("result", {})
        uid = res.get("rest_id")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        legacy = res.get("legacy", {})
        core = res.get("core", {})
        users.append(
            {
                "id": uid,
                "name": legacy.get("name") or core.get("name", ""),
                "handle": legacy.get("screen_name") or core.get("screen_name", ""),
                "desc": legacy.get("description", ""),
                "followers": legacy.get("followers_count", 0),
            }
        )
    return users


def extract_lists(data: dict) -> list[dict]:
    """検索(Lists)応答からリストを抽出。"""
    lists: list[dict] = []
    seen: set[str] = set()
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        ic = node.get("itemContent")
        if not (isinstance(ic, dict) and ic.get("itemType") == "TimelineTwitterList"):
            continue
        lst = ic.get("list", {})
        lid = lst.get("id_str") or str(lst.get("id", ""))
        if not lid or lid in seen:
            continue
        seen.add(lid)
        owner = (
            lst.get("user_results", {})
            .get("result", {})
            .get("legacy", {})
            .get("screen_name", "")
        )
        lists.append(
            {
                "id": lid,
                "name": lst.get("name", ""),
                "members": lst.get("member_count", 0),
                "subscribers": lst.get("subscriber_count", 0),
                "desc": lst.get("description", ""),
                "by": owner,
            }
        )
    return lists


def _print_users(users: list[dict]) -> None:
    if not users:
        print("(ユーザーが見つかりませんでした)")
        return
    for u in users:
        print(f"\n{u['name']} (@{u['handle']})  id={u['id']}  フォロワー{u['followers']}")
        if u["desc"]:
            print(f"  {u['desc']}")


def _print_lists(lists: list[dict]) -> None:
    if not lists:
        print("(リストが見つかりませんでした)")
        return
    for x in lists:
        by = f" by @{x['by']}" if x["by"] else ""
        print(f"\n[{x['id']}] {x['name']}{by}  メンバー{x['members']} 購読{x['subscribers']}")
        if x["desc"]:
            print(f"  {x['desc']}")


def _print_tweets(tweets: list[dict], show_images: bool = False, cfg: dict | None = None) -> None:
    cfg = cfg or {}
    ua = cfg.get("user_agent", "")
    proto = cfg.get("image_protocol", "ansi")
    if not tweets:
        print("(ツイートが見つかりませんでした)")
        return
    for t in tweets:
        head = f"@{t['handle']}" if t["handle"] else t["author"]
        print(f"\n[{t['id']}] {t['author']} ({head})")
        print(f"  {t['text']}")
        for m in t.get("media", []):
            if show_images and m["type"] == "photo":
                esc = imaging.render_photo(m["url"], ua, protocol=proto)
                if esc:
                    print(esc)
                    continue
            print(f"  [{m['type']}] {m['url']}")
        print(f"  ♥{t['likes']}  RT{t['retweets']}  {t['created_at']}")


# ---- コマンド ----------------------------------------------------------
def _created_tweet_id(data: dict) -> str | None:
    return (
        data.get("data", {})
        .get("create_tweet", {})
        .get("tweet_results", {})
        .get("result", {})
        .get("rest_id")
    )


def _upload_all(client: XClient, files: list[str]) -> list[str]:
    ids = []
    for path in files:
        print(f"アップロード中: {path} ...")
        ids.append(client.upload_media(path))
    return ids


def cmd_post(args) -> int:
    client = XClient()
    media_ids = _upload_all(client, args.media) if args.media else None
    data = client.create_tweet(args.text, reply_to=args.reply, media_ids=media_ids)
    tid = _created_tweet_id(data)
    if tid:
        print(f"投稿しました: https://x.com/i/status/{tid}  (id={tid})")
        return 0
    print("投稿応答を解釈できませんでした:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1000])
    return 1


def cmd_thread(args) -> int:
    client = XClient()
    reply_to = args.reply  # 既存ツイートへのスレッド接続も可
    ids = []
    for i, text in enumerate(args.texts):
        data = client.create_tweet(text, reply_to=reply_to)
        tid = _created_tweet_id(data)
        if not tid:
            print(f"{i + 1}番目の投稿に失敗しました:")
            print(json.dumps(data, ensure_ascii=False, indent=2)[:600])
            return 1
        ids.append(tid)
        reply_to = tid  # 次は直前のツイートへ返信
        print(f"{i + 1}/{len(args.texts)}: https://x.com/i/status/{tid}")
    print(f"スレッド投稿完了({len(ids)}件)")
    return 0


def cmd_delete(args) -> int:
    client = XClient()
    data = client.delete_tweet(args.tweet_id)
    if data.get("data", {}).get("delete_tweet") is not None:
        print(f"削除しました: {args.tweet_id}")
        return 0
    print("削除応答を解釈できませんでした:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1000])
    return 1


def _dump_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _want_images(args, cfg: dict) -> bool:
    """画像表示の有効判定。config の show_images を既定に、--images/--no-images で上書き。"""
    if getattr(args, "no_images", False):
        return False
    if getattr(args, "images", False):
        return True
    return bool(cfg.get("show_images", False))


def _report_mutation(data: dict, label: str, tweet_id: str) -> int:
    if (data.get("data") or {}) and not data.get("errors"):
        print(f"{label}: {tweet_id}")
        return 0
    print(f"{label} 失敗:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:600])
    return 1


def cmd_like(args) -> int:
    data = XClient().like(args.tweet_id)
    return _report_mutation(data, "いいねしました", args.tweet_id)


def cmd_unlike(args) -> int:
    data = XClient().unlike(args.tweet_id)
    return _report_mutation(data, "いいね解除しました", args.tweet_id)


def cmd_rt(args) -> int:
    data = XClient().retweet(args.tweet_id)
    return _report_mutation(data, "リツイートしました", args.tweet_id)


def cmd_unrt(args) -> int:
    data = XClient().unretweet(args.tweet_id)
    return _report_mutation(data, "リツイート解除しました", args.tweet_id)


def cmd_bookmark(args) -> int:
    data = XClient().bookmark(args.tweet_id)
    return _report_mutation(data, "ブックマークしました", args.tweet_id)


def cmd_unbookmark(args) -> int:
    data = XClient().unbookmark(args.tweet_id)
    return _report_mutation(data, "ブックマーク解除しました", args.tweet_id)


def _resolve_user_id(client: XClient, target: str) -> tuple[str, str]:
    """target が数字なら user_id とみなす。ハンドルなら解決して (id, handle) を返す。"""
    if target.isdigit():
        return target, target
    handle = target.lstrip("@")
    data = client.user_by_screen_name(handle)
    result = data.get("data", {}).get("user", {}).get("result", {})
    uid = result.get("rest_id")
    if not uid:
        raise XApiError(f"ユーザー @{handle} が見つかりませんでした。")
    return uid, handle


def _relation_cmd(args, method, verb: str) -> int:
    client = XClient()
    uid, label = _resolve_user_id(client, args.target)
    data = method(client, uid)
    if data.get("id") or data.get("id_str"):
        print(f"{verb}: @{label}")
        return 0
    print(f"{verb} 失敗:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:400])
    return 1


def cmd_mute(args) -> int:
    return _relation_cmd(args, lambda c, u: c.mute(u), "ミュートしました")


def cmd_unmute(args) -> int:
    return _relation_cmd(args, lambda c, u: c.unmute(u), "ミュート解除しました")


def cmd_block(args) -> int:
    return _relation_cmd(args, lambda c, u: c.block(u), "ブロックしました")


def cmd_unblock(args) -> int:
    return _relation_cmd(args, lambda c, u: c.unblock(u), "ブロック解除しました")


def cmd_quote(args) -> int:
    client = XClient()
    # 引用元の著者ハンドルを取得して正規のURLを組む
    data = client.tweet(args.tweet_id)
    result = _unwrap_tweet(
        data.get("data", {}).get("tweetResult", {}).get("result", {})
    )
    handle = _author(result)[1] or "i/web"
    url = f"https://x.com/{handle}/status/{args.tweet_id}"
    d = client.create_tweet(args.text, attachment_url=url)
    tid = _created_tweet_id(d)
    if tid:
        print(f"引用投稿しました: https://x.com/i/status/{tid}  (id={tid})")
        return 0
    print("投稿応答を解釈できませんでした:")
    print(json.dumps(d, ensure_ascii=False, indent=2)[:600])
    return 1


def cmd_conversation(args) -> int:
    client = XClient()
    data = client.tweet_detail(args.tweet_id)
    tweets = extract_tweets(data)[: args.count]
    if getattr(args, "json", False):
        _dump_json(tweets)
        return 0
    _print_tweets(tweets)
    return 0


def cmd_whoami(args) -> int:
    print("@" + XClient().whoami())
    return 0


def cmd_follow(args) -> int:
    client = XClient()
    uid, label = _resolve_user_id(client, args.target)
    data = client.follow(uid)
    who = data.get("screen_name") or label
    if data.get("follow_request_sent"):
        print(f"フォローリクエストを送りました(鍵垢): @{who}")
        return 0
    if data.get("id") or data.get("id_str"):
        print(f"フォローしました: @{who}")
        return 0
    print(f"応答を解釈できませんでした(@{who}):")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:400])
    return 1


def cmd_unfollow(args) -> int:
    client = XClient()
    uid, label = _resolve_user_id(client, args.target)
    data = client.unfollow(uid)
    who = data.get("screen_name") or label
    if data.get("id") or data.get("id_str"):
        print(f"フォロー解除しました: @{who}")
        return 0
    print(f"応答を解釈できませんでした(@{who}):")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:400])
    return 1


def cmd_user(args) -> int:
    client = XClient()
    data = client.user_by_screen_name(args.handle)
    result = data.get("data", {}).get("user", {}).get("result", {})
    legacy = result.get("legacy", {})
    core = result.get("core", {})
    name = legacy.get("name") or core.get("name", "")
    screen = legacy.get("screen_name") or core.get("screen_name", args.handle)
    uid = result.get("rest_id")
    if not uid:
        print("ユーザーが見つかりませんでした:")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:800])
        return 1

    profile = {
        "id": uid,
        "name": name,
        "handle": screen,
        "description": legacy.get("description", ""),
        "followers": legacy.get("followers_count"),
        "following": legacy.get("friends_count"),
        "tweets_count": legacy.get("statuses_count"),
    }
    tweets = []
    if args.tweets:
        page = min(max(args.count, 20), 100)
        tweets = _collect(
            lambda cur: client.user_tweets(uid, count=page, cursor=cur), args.count
        )

    if getattr(args, "json", False):
        _dump_json({"profile": profile, "tweets": tweets} if args.tweets else profile)
        return 0

    print(f"{name} (@{screen})  id={uid}")
    print(f"  {profile['description']}")
    print(
        f"  フォロワー {profile['followers']} / "
        f"フォロー {profile['following']} / "
        f"ツイート {profile['tweets_count']}"
    )
    if args.tweets:
        print(f"\n--- 最新ツイート({args.count}件) ---")
        _print_tweets(tweets, _want_images(args, client.cfg), client.cfg)
        if len(tweets) < args.count:
            print(f"\n(取得できたのは {len(tweets)} 件。フィード末尾に達しました)")
    return 0


def cmd_timeline(args) -> int:
    client = XClient()
    page = min(max(args.count, 20), 40)
    tweets = _collect(
        lambda cur: client.home_timeline(
            count=page, cursor=cur, latest=not args.algo
        ),
        args.count,
    )
    if getattr(args, "json", False):
        _dump_json(tweets)
        return 0
    _print_tweets(tweets, _want_images(args, client.cfg), client.cfg)
    if len(tweets) < args.count:
        print(f"\n(取得できたのは {len(tweets)} 件。フィード末尾に達しました)")
    return 0


def parse_inbox(data: dict) -> list[dict]:
    """DM 受信箱を会話ごとに {相手, 最新メッセージ, 時刻, conversation_id} へ整形。"""
    from collections import Counter

    st = data.get("inbox_initial_state", {})
    users = st.get("users", {})
    convs = st.get("conversations", {})
    entries = st.get("entries", [])

    # 自分の user_id = 全会話に共通して現れる参加者
    cnt: Counter = Counter()
    for c in convs.values():
        for p in c.get("participants", []):
            cnt[p["user_id"]] += 1
    self_id = cnt.most_common(1)[0][0] if cnt else None

    # 会話ごとの最新メッセージ
    latest: dict[str, tuple[int, str]] = {}
    for e in entries:
        m = e.get("message")
        if not m:
            continue
        cid = m.get("conversation_id")
        mid = int(m.get("id", 0) or 0)
        txt = m.get("message_data", {}).get("text", "")
        if cid and (cid not in latest or mid > latest[cid][0]):
            latest[cid] = (mid, txt)

    out = []
    for cid, c in convs.items():
        others = [
            p["user_id"] for p in c.get("participants", []) if p["user_id"] != self_id
        ]
        names = []
        for uid in others:
            u = users.get(uid, {})
            nm = u.get("name", "")
            sn = u.get("screen_name", uid)
            names.append(f"{nm}(@{sn})" if nm else f"@{sn}")
        out.append(
            {
                "conversation_id": cid,
                "with": ", ".join(names) or cid,
                "last": latest.get(cid, (0, ""))[1],
                "ts": int(c.get("sort_timestamp", 0) or 0),
            }
        )
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def cmd_dms(args) -> int:
    import datetime

    client = XClient()
    convs = parse_inbox(client.dm_inbox())[: args.count]
    if getattr(args, "json", False):
        _dump_json(convs)
        return 0
    if not convs:
        print("(会話がありません)")
        return 0
    for c in convs:
        when = ""
        if c["ts"]:
            when = datetime.datetime.fromtimestamp(c["ts"] / 1000).strftime(
                "%m/%d %H:%M"
            )
        print(f"\n[{when}] {c['with']}")
        if c["last"]:
            print(f"  {c['last']}")
        print(f"  conv={c['conversation_id']}")
    return 0


def cmd_dm(args) -> int:
    client = XClient()
    uid, label = _resolve_user_id(client, args.target)
    data = client.dm_send(uid, args.text)
    ev = data.get("event", {})
    if ev.get("id") or ev.get("type") == "message_create":
        print(f"DMを送信しました → @{label}")
        return 0
    print("送信応答を解釈できませんでした:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:500])
    return 1


def _resolve_conversation(client: XClient, target: str) -> str:
    """target が conv_id(数字-数字)ならそのまま。ハンドルなら受信箱から会話を探す。"""
    if "-" in target and all(p.isdigit() for p in target.split("-")):
        return target
    uid, handle = _resolve_user_id(client, target)
    convs = client.dm_inbox().get("inbox_initial_state", {}).get("conversations", {})
    for cid, c in convs.items():
        if any(p["user_id"] == uid for p in c.get("participants", [])):
            return cid
    raise XApiError(f"@{handle} との会話が見つかりません(DM履歴がない可能性)。")


def cmd_dm_read(args) -> int:
    import datetime

    client = XClient()
    conv_id = _resolve_conversation(client, args.target)
    ct = client.dm_conversation(conv_id).get("conversation_timeline", {})
    users = ct.get("users", {})
    msgs = []
    for e in ct.get("entries", []):
        m = e.get("message")
        if not m:
            continue
        md = m.get("message_data", {})
        msgs.append(
            {
                "id": m.get("id"),
                "sender": md.get("sender_id"),
                "text": md.get("text", ""),
                "time": int(md.get("time", 0) or 0),
            }
        )
    msgs.sort(key=lambda x: int(x["id"]))  # 古い順
    if getattr(args, "json", False):
        _dump_json(msgs)
        return 0
    if not msgs:
        print("(メッセージがありません)")
        return 0
    for mm in msgs:
        u = users.get(mm["sender"], {})
        who = f"@{u.get('screen_name', mm['sender'])}"
        when = ""
        if mm["time"]:
            when = datetime.datetime.fromtimestamp(mm["time"] / 1000).strftime(
                "%m/%d %H:%M"
            )
        print(f"[{when}] {who}: {mm['text']}")
        print(f"    (msg={mm['id']})")
    return 0


def cmd_dm_delete(args) -> int:
    client = XClient()
    client.dm_delete_message(args.message_id)
    print(f"DMメッセージを削除しました: {args.message_id}")
    return 0


def cmd_dm_delete_conv(args) -> int:
    client = XClient()
    conv_id = _resolve_conversation(client, args.target)
    client.dm_delete_conversation(conv_id)
    print(f"会話を削除しました: {conv_id}")
    return 0


def cmd_media(args) -> int:
    import os
    import webbrowser

    import requests

    client = XClient()
    data = client.tweet(args.tweet_id)
    result = _unwrap_tweet(
        data.get("data", {}).get("tweetResult", {}).get("result", {})
    )
    legacy = result.get("legacy", {})
    media = media_urls(legacy)
    if not media:
        print("このツイートにメディアはありません。")
        return 0
    proto = client.cfg.get("image_protocol", "ansi")
    for i, m in enumerate(media):
        print(f"[{i}] {m['type']}: {m['url']}")
        if getattr(args, "show", False) and m["type"] == "photo":
            esc = imaging.render_photo(
                m["url"], client.cfg["user_agent"], width_cells=50, protocol=proto
            )
            if esc:
                print(esc)

    if args.open:
        for m in media:
            webbrowser.open(m["url"])
        print(f"(ブラウザで {len(media)} 件を開きました)")

    if args.download:
        os.makedirs(args.download, exist_ok=True)
        headers = {"user-agent": client.cfg["user_agent"]}
        for i, m in enumerate(media):
            ext = ".jpg" if m["type"] == "photo" else ".mp4"
            path = os.path.join(args.download, f"{args.tweet_id}_{i}{ext}")
            resp = requests.get(m["url"], headers=headers, timeout=60)
            with open(path, "wb") as f:
                f.write(resp.content)
            print(f"保存: {path} ({len(resp.content)//1024} KB)")
    return 0


def cmd_likes(args) -> int:
    client = XClient()
    uid, label = _resolve_user_id(client, args.target)
    page = min(max(args.count, 20), 40)
    tweets = _collect(
        lambda cur: client.likes(uid, count=page, cursor=cur), args.count
    )
    if getattr(args, "json", False):
        _dump_json(tweets)
        return 0
    print(f"--- @{label} のいいね({len(tweets)}件) ---")
    _print_tweets(tweets, _want_images(args, client.cfg), client.cfg)
    return 0


def cmd_bookmarks(args) -> int:
    client = XClient()
    page = min(max(args.count, 20), 40)
    tweets = _collect(
        lambda cur: client.bookmarks(count=page, cursor=cur), args.count
    )
    if getattr(args, "json", False):
        _dump_json(tweets)
        return 0
    print(f"--- ブックマーク({len(tweets)}件) ---")
    _print_tweets(tweets, _want_images(args, client.cfg), client.cfg)
    return 0


def extract_notifications(data: dict) -> list[dict]:
    """REST 2 通知応答から通知メッセージを抽出(いいね/フォロー/RT等)。"""
    go = data.get("globalObjects", {})
    notifs = go.get("notifications", {})
    out = []
    for nid, n in notifs.items():
        out.append(
            {
                "id": nid,
                "text": n.get("message", {}).get("text", ""),
                "ts": int(n.get("timestampMs", 0) or 0),
            }
        )
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def cmd_notifications(args) -> int:
    import datetime

    client = XClient()
    data = client.notifications(count=args.count)
    notifs = extract_notifications(data)[: args.count]
    if getattr(args, "json", False):
        _dump_json(notifs)
        return 0
    if not notifs:
        print("(通知がありませんでした。メンション系は含まれない場合があります)")
        return 0
    for n in notifs:
        when = ""
        if n["ts"]:
            when = datetime.datetime.fromtimestamp(n["ts"] / 1000).strftime(
                "%m/%d %H:%M"
            )
        print(f"[{when}] {n['text']}")
    return 0


_SEARCH_TABS = {
    "top": ("Top", "extract_tweets"),
    "latest": ("Latest", "extract_tweets"),
    "people": ("People", "extract_users"),
    "media": ("Media", "extract_tweets"),
    "lists": ("Lists", "extract_lists"),
}


def cmd_search(args) -> int:
    client = XClient()
    product = _SEARCH_TABS[args.tab][0]
    if args.tab == "people":
        extractor, printer = extract_users, _print_users
    elif args.tab == "lists":
        extractor, printer = extract_lists, _print_lists
    else:
        extractor, printer = extract_tweets, _print_tweets

    page = min(max(args.count, 20), 40)
    items = _collect(
        lambda cur: client.search(
            args.query, product=product, count=page, cursor=cur
        ),
        args.count,
        extractor,
    )
    if getattr(args, "json", False):
        _dump_json(items)
        return 0
    if printer is _print_tweets:
        _print_tweets(items, _want_images(args, client.cfg), client.cfg)
    else:
        printer(items)
    if len(items) < args.count:
        print(f"\n(取得できたのは {len(items)} 件)")
    return 0


def cmd_tui(args) -> int:
    from . import tui

    lang = "en" if getattr(args, "en", False) else getattr(args, "lang", "ja")
    return tui.run(lang)


def cmd_sync(args) -> int:
    cfg = config.load_config()
    endpoints.sync(cfg["user_agent"])
    return 0


def cmd_config(args) -> int:
    path = config.ensure_config()
    cfg = config.load_config()
    changed = False
    if getattr(args, "images", None) is not None:
        cfg["show_images"] = args.images == "on"
        changed = True
    if getattr(args, "protocol", None) is not None:
        cfg["image_protocol"] = args.protocol
        changed = True
    if changed:
        config.save_config(cfg)
        print(
            f"show_images={cfg['show_images']} / image_protocol={cfg['image_protocol']}"
        )
        return 0
    masked = {
        **cfg,
        "auth_token": (cfg["auth_token"][:6] + "...") if cfg["auth_token"] else "(未設定)",
        "ct0": (cfg["ct0"][:6] + "...") if cfg["ct0"] else "(未設定)",
    }
    print(f"設定ファイル: {path}")
    print(json.dumps(masked, ensure_ascii=False, indent=2))
    return 0
