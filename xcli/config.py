"""設定と queryId/features キャッシュの読み書き。

設定は ~/.xcli/config.json(環境変数 XCLI_HOME で変更可)。
auth_token はアカウント資格情報なので、ユーザー自身がこのファイルに貼る運用。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XCLI_HOME", str(Path.home() / ".xcli")))
CONFIG_FILE = CONFIG_DIR / "config.json"
ENDPOINTS_FILE = CONFIG_DIR / "endpoints.json"

# x.com Web アプリが長年使っている公開 Bearer(固定値)。
DEFAULT_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs="
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    "auth_token": "",
    "ct0": "",
    "bearer": DEFAULT_BEARER,
    "lang": "ja",
    "user_agent": DEFAULT_USER_AGENT,
    "min_interval_sec": 2.0,  # 連続リクエストの最小間隔(レート制御)
    "show_images": False,  # true にすると取得系で画像を常にインライン表示
    "image_protocol": "ansi",  # ansi(全端末対応) / iterm2(iTerm2系のみ)
}


def ensure_config() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
    return CONFIG_FILE


def load_config() -> dict:
    ensure_config()
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {**DEFAULT_CONFIG, **data}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_endpoints() -> dict:
    if ENDPOINTS_FILE.exists():
        return json.loads(ENDPOINTS_FILE.read_text(encoding="utf-8"))
    return {}


def save_endpoints(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ENDPOINTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
