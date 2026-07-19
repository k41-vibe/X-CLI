"""GraphQL 操作の queryId / features レジストリ。

queryId は X の JS デプロイ毎に変わるため:
  1. 出荷時デフォルト(下記 DEFAULT_QUERY_IDS = 2026-07 実測)を持ちつつ
  2. `xcli sync` で公開 JS バンドル(main.js + 共有チャンク)から最新を再取得し
     ~/.xcli/endpoints.json にキャッシュして上書きする。
features は欠けると 400 になるが、client 側で不足キーを自動補完リトライする。
"""
from __future__ import annotations

import re
import requests

from . import config

# 2026-07-19 に abs.twimg.com/main.js から実測した queryId。
DEFAULT_QUERY_IDS = {
    "CreateTweet": "hIL9XdleMYEtVXOZVbr8Bg",
    "DeleteTweet": "nxpZCY2K-I6QoFHAHeojFQ",
    "UserByScreenName": "2qvSHpkWTMS9i0zJAwDNiA",
    "UserTweets": "6r5OLCC_wFH4CpRyXKuAmQ",
    "FavoriteTweet": "lI07N6Otwv1PhnEgXILM7A",
    "UnfavoriteTweet": "ZYKSe-w7KEslx3JhSIk5LA",
    "CreateRetweet": "mbRO74GrOvSfRcJnlMapnQ",
    "DeleteRetweet": "ZyZigVsNiFO6v1dEks1eWg",
    "CreateBookmark": "aoDbu3RHznuiSkQ9aNM67Q",
    "DeleteBookmark": "Wlmlj2-xzyS1GN3a6cj-mQ",
    # フォロー中・時系列フィード(2026-07 実測)。timeline コマンド既定。
    "HomeLatestTimeline": "lyhT5o5ECF6_kYqTqpUUew",
    # おすすめ・アルゴ順(2026-07 実測)。timeline --algo で使用。
    "HomeTimeline": "lqfNCpeO0wydVAAXAbAU5w",
    # 検索(2026-07 実測)。product 変数で Top/Latest/People/Media/Lists 切替。
    "SearchTimeline": "hz_94eVAtrtQo_vO3my7Rw",
    # いいね一覧(2026-07 実測)。
    "Likes": "4X8QeWbeJ0jwGHaXSxExRw",
    # ブックマーク一覧(2026-07 実測)。別バンドルのため sync では取れず手取り。
    "Bookmarks": "LoLaMO4GuHLEPJOhH9kjAw",
    # 単体ツイート取得(2026-07 実測)。media コマンドで使用。
    "TweetResultByRestId": "4hhGRbehkcUVTKf8n0f0xw",
    # ツイート詳細+会話(リプライツリー)。
    "TweetDetail": "rZA6K31W4E90vZKBmxXV3g",
}

# tweet/timeline 描画用 features のスーパーセット(2026-07 実測)。
# CreateTweet / UserTweets / HomeTimeline はこれをベースに使う。
TWEET_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

# プロフィール取得用 features(2026-07 実測、UserByScreenName)。
USER_FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

# 操作ごとの既定 features。空 dict の操作(DeleteTweet 等)は features 不要。
DEFAULT_FEATURES = {
    "CreateTweet": TWEET_FEATURES,
    "UserTweets": TWEET_FEATURES,
    "HomeTimeline": TWEET_FEATURES,
    "HomeLatestTimeline": TWEET_FEATURES,
    "SearchTimeline": TWEET_FEATURES,
    "Likes": TWEET_FEATURES,
    "Bookmarks": TWEET_FEATURES,
    "TweetResultByRestId": TWEET_FEATURES,
    "TweetDetail": TWEET_FEATURES,
    "UserByScreenName": USER_FEATURES,
    "DeleteTweet": {},
    "CreateRetweet": {},
    "FavoriteTweet": {},
}

# main.js 以外に走査する共有チャンク(HomeTimeline 等が入る)。
_HOME_URL = "https://x.com/"
_JS_RE = re.compile(
    r"https://abs\.twimg\.com/responsive-web/client-web[^\"']*?\.js"
)
_QID_RE = re.compile(r'queryId:"([^"]+)",operationName:"([^"]+)"')


def get_query_id(operation: str) -> str | None:
    cache = config.load_endpoints().get("query_ids", {})
    return cache.get(operation) or DEFAULT_QUERY_IDS.get(operation)


def get_features(operation: str) -> dict:
    cache = config.load_endpoints().get("features", {})
    if operation in cache:
        return dict(cache[operation])
    return dict(DEFAULT_FEATURES.get(operation, {}))


def sync(user_agent: str, verbose: bool = True) -> dict:
    """公開 JS バンドルを走査して queryId マップを再構築しキャッシュする。"""
    session = requests.Session()
    session.headers["User-Agent"] = user_agent

    def log(msg: str):
        if verbose:
            print(msg)

    # 1. ログアウト状態のシェル HTML から JS URL を集める。
    shell = session.get(_HOME_URL, timeout=20).text
    js_urls = set(_JS_RE.findall(shell))
    # main.js は必ず含める。
    log(f"shell から {len(js_urls)} 個の JS を検出")

    query_ids: dict[str, str] = {}
    for url in sorted(js_urls):
        try:
            body = session.get(url, timeout=20).text
        except Exception as exc:  # noqa: BLE001
            log(f"  skip {url.split('/')[-1]}: {exc}")
            continue
        found = _QID_RE.findall(body)
        for qid, op in found:
            query_ids[op] = qid
        if found:
            log(f"  {url.split('/')[-1]}: {len(found)} ops")

    # 出荷時デフォルトで穴埋め(バンドルに無かった操作)。
    for op, qid in DEFAULT_QUERY_IDS.items():
        query_ids.setdefault(op, qid)

    data = config.load_endpoints()
    data["query_ids"] = query_ids
    config.save_endpoints(data)
    log(f"合計 {len(query_ids)} 操作の queryId を保存: {config.ENDPOINTS_FILE}")
    return query_ids
