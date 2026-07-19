"""X 非公式 GraphQL クライアント。ヘッダ組立・レート制御・features 自己修復を担う。"""
from __future__ import annotations

import json
import re
import time

import requests

from . import config, endpoints
from .transaction import TransactionGenerator

API_ROOT = "https://x.com/i/api"
API_BASE = f"{API_ROOT}/graphql"
REST_BASE = f"{API_ROOT}/1.1"
UPLOAD_URL = "https://upload.x.com/i/media/upload.json"

# 「features cannot be null: a, b」からキー名を抜く。
_MISSING_FEATURE_RE = re.compile(
    r"following features cannot be null:\s*([A-Za-z0-9_,\s]+)"
)


class XApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(XApiError):
    pass


class XClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or config.load_config()
        if not self.cfg.get("auth_token") or not self.cfg.get("ct0"):
            raise AuthError(
                "auth_token / ct0 が未設定です。~/.xcli/config.json に貼ってください "
                "(DevTools > Application > Cookies)。"
            )
        self.session = requests.Session()
        self.session.headers.update(self._base_headers())
        self.session.cookies.set("auth_token", self.cfg["auth_token"], domain=".x.com")
        self.session.cookies.set("ct0", self.cfg["ct0"], domain=".x.com")
        self._txgen = TransactionGenerator(user_agent=self.cfg["user_agent"])
        self._last_request = 0.0

    # ---- ヘッダ ----------------------------------------------------------
    def _base_headers(self) -> dict:
        return {
            "authorization": f"Bearer {self.cfg['bearer']}",
            "x-csrf-token": self.cfg["ct0"],
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": self.cfg["lang"],
            "content-type": "application/json",
            "user-agent": self.cfg["user_agent"],
            "referer": "https://x.com/",
            "origin": "https://x.com",
            "accept": "*/*",
            "accept-language": f"{self.cfg['lang']},en;q=0.8",
        }

    def _throttle(self) -> None:
        wait = self.cfg["min_interval_sec"] - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    # ---- 低レベル GraphQL 実行 ------------------------------------------
    def _request(
        self,
        operation: str,
        method: str,
        variables: dict,
        features: dict | None,
        field_toggles: dict | None = None,
    ) -> dict:
        query_id = endpoints.get_query_id(operation)
        if not query_id:
            raise XApiError(
                f"{operation} の queryId が不明です。`xcli sync` を実行してください。"
            )
        url = f"{API_BASE}/{query_id}/{operation}"
        features = dict(features) if features else {}

        for attempt in range(4):
            self._throttle()
            txid = self._txgen.generate(method, url)
            headers = {"x-client-transaction-id": txid}

            if method == "GET":
                params = {"variables": json.dumps(variables, separators=(",", ":"))}
                if features:
                    params["features"] = json.dumps(features, separators=(",", ":"))
                if field_toggles:
                    params["fieldToggles"] = json.dumps(
                        field_toggles, separators=(",", ":")
                    )
                resp = self.session.get(url, params=params, headers=headers, timeout=30)
            else:
                payload = {"variables": variables, "queryId": query_id}
                if features:
                    payload["features"] = features
                if field_toggles:
                    payload["fieldToggles"] = field_toggles
                resp = self.session.post(url, json=payload, headers=headers, timeout=30)

            # レート制限
            if resp.status_code == 429:
                reset = resp.headers.get("x-rate-limit-reset")
                raise XApiError(
                    f"レート制限(429)。reset={reset}", status=429, body=resp.text[:300]
                )
            if resp.status_code in (401, 403):
                raise AuthError(
                    f"認証エラー({resp.status_code})。Cookie 期限切れの可能性。",
                    status=resp.status_code,
                    body=resp.text[:300],
                )

            # features 不足 → 自動補完してリトライ
            missing = self._missing_features(resp)
            if missing and attempt < 3:
                for key in missing:
                    features.setdefault(key, False)
                continue

            try:
                data = resp.json()
            except ValueError:
                raise XApiError(
                    f"JSON でない応答(status {resp.status_code})",
                    status=resp.status_code,
                    body=resp.text[:300],
                )

            if isinstance(data, dict) and data.get("errors"):
                msg = "; ".join(e.get("message", "?") for e in data["errors"])
                # errors でも data がある(部分成功)ケースは通す
                if not data.get("data"):
                    raise XApiError(msg, status=resp.status_code, body=json.dumps(data)[:400])
            return data

        raise XApiError(f"{operation}: リトライ上限に達しました。")

    @staticmethod
    def _missing_features(resp: requests.Response) -> list[str]:
        if resp.status_code not in (400,):
            return []
        m = _MISSING_FEATURE_RE.search(resp.text)
        if not m:
            return []
        return [k.strip() for k in m.group(1).split(",") if k.strip()]

    # ---- メディアアップロード(upload.x.com、チャンク方式)-----------------
    def _upload_headers(self) -> dict:
        return {
            "authorization": f"Bearer {self.cfg['bearer']}",
            "x-csrf-token": self.cfg["ct0"],
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "user-agent": self.cfg["user_agent"],
            "referer": "https://x.com/",
            "origin": "https://x.com",
        }

    def upload_media(self, file_path: str) -> str:
        """画像/動画/GIF を INIT→APPEND→FINALIZE でアップロードし media_id を返す。"""
        import mimetypes
        import os
        import time

        size = os.path.getsize(file_path)
        mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        if mime.startswith("video"):
            category = "tweet_video"
        elif mime == "image/gif":
            category = "tweet_gif"
        else:
            category = "tweet_image"

        def _post(params, **kw):
            self._throttle()
            txid = self._txgen.generate("POST", UPLOAD_URL)
            h = {**self._upload_headers(), "x-client-transaction-id": txid}
            r = self.session.post(UPLOAD_URL, params=params, headers=h, timeout=120, **kw)
            if r.status_code >= 400:
                raise XApiError(
                    f"アップロード失敗({params.get('command')} {r.status_code})",
                    status=r.status_code,
                    body=r.text[:300],
                )
            return r

        # INIT
        r = _post(
            {
                "command": "INIT",
                "total_bytes": size,
                "media_type": mime,
                "media_category": category,
            }
        )
        media_id = r.json()["media_id_string"]

        # APPEND(4MB チャンク)。command 等はクエリでなく multipart フォームで送る。
        chunk_size = 4 * 1024 * 1024
        with open(file_path, "rb") as f:
            idx = 0
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self._throttle()
                txid = self._txgen.generate("POST", UPLOAD_URL)
                # content-type: None で session 既定の application/json を外し、
                # requests に multipart/form-data 境界を自動設定させる。
                h = {
                    **self._upload_headers(),
                    "x-client-transaction-id": txid,
                    "content-type": None,
                }
                r = self.session.post(
                    UPLOAD_URL,
                    data={
                        "command": "APPEND",
                        "media_id": media_id,
                        "segment_index": idx,
                    },
                    files={"media": ("blob", chunk, "application/octet-stream")},
                    headers=h,
                    timeout=120,
                )
                if r.status_code >= 400:
                    raise XApiError(
                        f"アップロード失敗(APPEND {r.status_code})",
                        status=r.status_code,
                        body=r.text[:300],
                    )
                idx += 1

        # FINALIZE
        info = _post({"command": "FINALIZE", "media_id": media_id}).json()

        # 動画等は処理完了までポーリング
        pi = info.get("processing_info")
        while pi and pi.get("state") in ("pending", "in_progress"):
            time.sleep(pi.get("check_after_secs", 3))
            self._throttle()
            txid = self._txgen.generate("GET", UPLOAD_URL)
            h = {**self._upload_headers(), "x-client-transaction-id": txid}
            st = self.session.get(
                UPLOAD_URL,
                params={"command": "STATUS", "media_id": media_id},
                headers=h,
                timeout=30,
            ).json()
            pi = st.get("processing_info")
            if pi and pi.get("state") == "failed":
                raise XApiError(f"メディア処理に失敗: {pi.get('error')}")
        return media_id

    # ---- 高レベル操作 ----------------------------------------------------
    def create_tweet(
        self,
        text: str,
        reply_to: str | None = None,
        media_ids: list[str] | None = None,
        attachment_url: str | None = None,
    ) -> dict:
        entities = [{"media_id": m, "tagged_users": []} for m in (media_ids or [])]
        variables = {
            "tweet_text": text,
            "dark_request": False,
            "media": {"media_entities": entities, "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        }
        if attachment_url:  # 引用ツイート
            variables["attachment_url"] = attachment_url
        if reply_to:
            variables["reply"] = {
                "in_reply_to_tweet_id": reply_to,
                "exclude_reply_user_ids": [],
            }
        return self._request(
            "CreateTweet", "POST", variables, endpoints.get_features("CreateTweet")
        )

    def delete_tweet(self, tweet_id: str) -> dict:
        variables = {"tweet_id": tweet_id, "dark_request": False}
        return self._request("DeleteTweet", "POST", variables, None)

    # ---- REST 1.1(フォロー等、GraphQL でない操作)-----------------------
    def _rest_post(self, path: str, form: dict) -> dict:
        url = f"{REST_BASE}/{path}"
        self._throttle()
        txid = self._txgen.generate("POST", url)
        headers = {
            "x-client-transaction-id": txid,
            "content-type": "application/x-www-form-urlencoded",
        }
        resp = self.session.post(url, data=form, headers=headers, timeout=30)
        if resp.status_code in (401, 403):
            raise AuthError(
                f"認証エラー({resp.status_code})。Cookie 期限切れの可能性。",
                status=resp.status_code,
                body=resp.text[:300],
            )
        if resp.status_code == 429:
            raise XApiError("レート制限(429)。時間を空けてください。", status=429)
        if not resp.content:
            return {}  # 削除系は空ボディで成功を返すことがある
        try:
            data = resp.json()
        except ValueError:
            raise XApiError(
                f"JSON でない応答(status {resp.status_code})",
                status=resp.status_code,
                body=resp.text[:300],
            )
        if isinstance(data, dict) and data.get("errors"):
            msg = "; ".join(
                e.get("message", "?") for e in data["errors"]
            )
            raise XApiError(msg, status=resp.status_code, body=json.dumps(data)[:400])
        return data

    def dm_delete_message(self, message_id: str) -> dict:
        return self._rest_post("dm/destroy.json", {"id": message_id})

    def dm_delete_conversation(self, conversation_id: str) -> dict:
        return self._rest_post(
            f"dm/conversation/{conversation_id}/delete.json", {}
        )

    def _rest_post_json(self, path: str, body: dict) -> dict:
        url = f"{API_ROOT}/{path}"
        self._throttle()
        txid = self._txgen.generate("POST", url)
        resp = self.session.post(
            url, json=body, headers={"x-client-transaction-id": txid}, timeout=30
        )
        if resp.status_code in (401, 403):
            raise AuthError(
                f"認証エラー({resp.status_code})。Cookie 期限切れの可能性。",
                status=resp.status_code,
                body=resp.text[:300],
            )
        if resp.status_code == 429:
            raise XApiError("レート制限(429)。時間を空けてください。", status=429)
        try:
            data = resp.json()
        except ValueError:
            raise XApiError(
                f"JSON でない応答(status {resp.status_code})",
                status=resp.status_code,
                body=resp.text[:300],
            )
        if isinstance(data, dict) and data.get("errors"):
            msg = "; ".join(e.get("message", "?") for e in data["errors"])
            raise XApiError(msg, status=resp.status_code, body=json.dumps(data)[:400])
        return data

    def whoami(self) -> str:
        """認証中アカウントの screen_name を返す。"""
        url = "https://api.x.com/1.1/account/settings.json"
        self._throttle()
        txid = self._txgen.generate("GET", url)
        r = self.session.get(
            url, headers={"x-client-transaction-id": txid}, timeout=30
        )
        try:
            return r.json().get("screen_name", "")
        except ValueError:
            return ""

    def dm_inbox(self) -> dict:
        params = {
            "nsfw_filtering_enabled": "false",
            "filter_low_quality": "true",
            "include_quality": "all",
            "dm_secret_conversations_enabled": "false",
            "cards_platform": "Web-12",
            "include_cards": 1,
            "include_ext_alt_text": "true",
            "include_quote_count": "true",
            "include_reply_count": 1,
            "tweet_mode": "extended",
            "include_ext_views": "true",
            "dm_users": "true",
            "include_groups": "true",
            "include_inbox_timelines": "true",
            "supports_reactions": "true",
        }
        return self._rest_get("1.1/dm/inbox_initial_state.json", params)

    def dm_conversation(self, conversation_id: str, max_id: str | None = None) -> dict:
        params = {
            "context": "FETCH_DM_CONVERSATION",
            "include_profile_interstitial_type": 1,
            "cards_platform": "Web-12",
            "include_cards": 1,
            "include_ext_alt_text": "true",
            "include_quote_count": "true",
            "include_reply_count": 1,
            "tweet_mode": "extended",
            "include_ext_views": "true",
            "dm_users": "false",
            "supports_reactions": "true",
            "count": 50,
        }
        if max_id:
            params["max_id"] = max_id
        return self._rest_get(f"1.1/dm/conversation/{conversation_id}.json", params)

    def dm_send(self, recipient_id: str, text: str) -> dict:
        body = {
            "event": {
                "type": "message_create",
                "message_create": {
                    "target": {"recipient_id": recipient_id},
                    "message_data": {"text": text},
                },
            }
        }
        return self._rest_post_json(
            "1.1/direct_messages/events/new.json", body
        )

    def _rest_get(self, path: str, params: dict) -> dict:
        url = f"{API_ROOT}/{path}"
        self._throttle()
        txid = self._txgen.generate("GET", url)
        resp = self.session.get(
            url, params=params, headers={"x-client-transaction-id": txid}, timeout=30
        )
        if resp.status_code in (401, 403):
            raise AuthError(
                f"認証エラー({resp.status_code})。Cookie 期限切れの可能性。",
                status=resp.status_code,
                body=resp.text[:300],
            )
        if resp.status_code == 429:
            raise XApiError("レート制限(429)。時間を空けてください。", status=429)
        try:
            return resp.json()
        except ValueError:
            raise XApiError(
                f"JSON でない応答(status {resp.status_code})",
                status=resp.status_code,
                body=resp.text[:300],
            )

    def notifications(self, count: int = 40, cursor: str | None = None) -> dict:
        params = {
            "include_profile_interstitial_type": 1,
            "include_blocking": 1,
            "include_blocked_by": 1,
            "include_followed_by": 1,
            "include_want_retweets": 1,
            "include_mute_edge": 1,
            "include_can_dm": 1,
            "include_can_media_tag": 1,
            "skip_status": 1,
            "cards_platform": "Web-12",
            "include_cards": 1,
            "include_ext_alt_text": "true",
            "include_quote_count": "true",
            "include_reply_count": 1,
            "tweet_mode": "extended",
            "include_ext_views": "true",
            "include_entities": "true",
            "include_user_entities": "true",
            "include_ext_media_availability": "true",
            "count": count,
            "ext": "mediaStats,highlightedLabel",
        }
        if cursor:
            params["cursor"] = cursor
        return self._rest_get("2/notifications/all.json", params)

    def follow(self, user_id: str) -> dict:
        return self._rest_post("friendships/create.json", {"user_id": user_id})

    def unfollow(self, user_id: str) -> dict:
        return self._rest_post("friendships/destroy.json", {"user_id": user_id})

    def mute(self, user_id: str) -> dict:
        return self._rest_post("mutes/users/create.json", {"user_id": user_id})

    def unmute(self, user_id: str) -> dict:
        return self._rest_post("mutes/users/destroy.json", {"user_id": user_id})

    def block(self, user_id: str) -> dict:
        return self._rest_post("blocks/create.json", {"user_id": user_id})

    def unblock(self, user_id: str) -> dict:
        return self._rest_post("blocks/destroy.json", {"user_id": user_id})

    def like(self, tweet_id: str) -> dict:
        return self._request("FavoriteTweet", "POST", {"tweet_id": tweet_id}, None)

    def unlike(self, tweet_id: str) -> dict:
        return self._request("UnfavoriteTweet", "POST", {"tweet_id": tweet_id}, None)

    def retweet(self, tweet_id: str) -> dict:
        variables = {"tweet_id": tweet_id, "dark_request": False}
        return self._request("CreateRetweet", "POST", variables, None)

    def unretweet(self, tweet_id: str) -> dict:
        variables = {"source_tweet_id": tweet_id, "dark_request": False}
        return self._request("DeleteRetweet", "POST", variables, None)

    def bookmark(self, tweet_id: str) -> dict:
        return self._request("CreateBookmark", "POST", {"tweet_id": tweet_id}, None)

    def unbookmark(self, tweet_id: str) -> dict:
        return self._request("DeleteBookmark", "POST", {"tweet_id": tweet_id}, None)

    def tweet(self, tweet_id: str) -> dict:
        variables = {
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        }
        return self._request(
            "TweetResultByRestId",
            "GET",
            variables,
            endpoints.get_features("TweetResultByRestId"),
            field_toggles={
                "withArticleRichContentState": False,
                "withArticlePlainText": False,
                "withGrokAnalyze": False,
                "withDisallowedReplyControls": False,
            },
        )

    def tweet_detail(self, tweet_id: str, cursor: str | None = None) -> dict:
        """ツイート詳細+会話(リプライツリー)を取得。"""
        variables = {
            "focalTweetId": tweet_id,
            "with_rux_injections": False,
            "rankingMode": "Relevance",
            "includePromotedContent": False,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor
        return self._request(
            "TweetDetail",
            "GET",
            variables,
            endpoints.get_features("TweetDetail"),
            field_toggles={
                "withArticleRichContentState": True,
                "withArticlePlainText": False,
                "withGrokAnalyze": False,
                "withDisallowedReplyControls": False,
            },
        )

    def user_by_screen_name(self, screen_name: str) -> dict:
        variables = {"screen_name": screen_name, "withGrokTranslatedBio": False}
        return self._request(
            "UserByScreenName",
            "GET",
            variables,
            endpoints.get_features("UserByScreenName"),
            field_toggles={"withAuxiliaryUserLabels": True, "withPayments": False},
        )

    def user_tweets(self, user_id: str, count: int = 20, cursor: str | None = None) -> dict:
        variables = {
            "userId": user_id,
            "count": count,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor
        return self._request(
            "UserTweets",
            "GET",
            variables,
            endpoints.get_features("UserTweets"),
            field_toggles={"withArticlePlainText": False},
        )

    def likes(self, user_id: str, count: int = 20, cursor: str | None = None) -> dict:
        variables = {
            "userId": user_id,
            "count": count,
            "includePromotedContent": False,
            "withClientEventToken": False,
            "withBirdwatchNotes": False,
            "withVoice": True,
            "withV2Timeline": True,
        }
        if cursor:
            variables["cursor"] = cursor
        return self._request(
            "Likes",
            "GET",
            variables,
            endpoints.get_features("Likes"),
            field_toggles={"withArticlePlainText": False},
        )

    def bookmarks(self, count: int = 20, cursor: str | None = None) -> dict:
        variables = {"count": count, "includePromotedContent": False}
        if cursor:
            variables["cursor"] = cursor
        return self._request(
            "Bookmarks", "GET", variables, endpoints.get_features("Bookmarks")
        )

    def search(
        self,
        query: str,
        product: str = "Latest",
        count: int = 20,
        cursor: str | None = None,
    ) -> dict:
        """検索。product は Top/Latest/People/Media/Lists。"""
        variables = {
            "rawQuery": query,
            "count": count,
            "querySource": "typed_query",
            "product": product,
        }
        if cursor:
            variables["cursor"] = cursor
        return self._request(
            "SearchTimeline", "GET", variables, endpoints.get_features("SearchTimeline")
        )

    def home_timeline(
        self, count: int = 20, cursor: str | None = None, latest: bool = True
    ) -> dict:
        """ホームタイムライン取得。latest=True はフォロー中・時系列(既定)。"""
        operation = "HomeLatestTimeline" if latest else "HomeTimeline"
        variables = {
            "count": count,
            "includePromotedContent": False,
            "latestControlAvailable": True,
            "requestContext": "launch",
        }
        if cursor:
            variables["cursor"] = cursor
        return self._request(
            operation,
            "POST",
            variables,
            endpoints.get_features(operation),
        )
