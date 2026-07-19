"""端末内インライン画像表示。

iTerm2 インライン画像プロトコル(OSC 1337)を使う。
対応端末: iTerm2 / Warp / WezTerm / VS Code 統合ターミナル など。
非対応端末では文字化け(base64の羅列)になるだけなので、opt-in(--images)で使う。
"""
from __future__ import annotations

import base64
import io
import os

import requests


def terminal_hint() -> str:
    """ざっくり端末推定(警告表示用)。"""
    tp = os.environ.get("TERM_PROGRAM", "")
    if os.environ.get("WT_SESSION"):
        return "WindowsTerminal"
    return tp or os.environ.get("TERM", "unknown")


def iterm2_image(data: bytes, width_cells: int = 38) -> str:
    """画像バイト列を iTerm2 インライン画像のエスケープ列にする。"""
    b64 = base64.b64encode(data).decode("ascii")
    return (
        f"\033]1337;File=inline=1;size={len(data)}"
        f";width={width_cells};preserveAspectRatio=1:{b64}\a"
    )


def fetch_image(url: str, user_agent: str, timeout: int = 30) -> bytes | None:
    try:
        resp = requests.get(url, headers={"user-agent": user_agent}, timeout=timeout)
        if resp.status_code == 200:
            return resp.content
    except Exception:  # noqa: BLE001
        return None
    return None


def ansi_halfblock(data: bytes, cols: int = 40) -> str | None:
    """画像を ANSI truecolor 半ブロックのモザイクに変換(どの端末でも表示可)。

    1 文字セルに上下2ピクセルを詰め、'▀' の前景=上/背景=下の色で描く。
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None

    w, h = img.size
    rows = max(1, round(cols * h / (w * 2)))  # セル縦横比(1:2)を補正
    img = img.resize((cols, rows * 2))
    px = img.load()

    lines = []
    for r in range(rows):
        parts = []
        for c in range(cols):
            tr, tg, tb = px[c, r * 2][:3]
            br, bg, bb = px[c, r * 2 + 1][:3]
            parts.append(
                f"\033[38;2;{tr};{tg};{tb}m\033[48;2;{br};{bg};{bb}m▀"
            )
        parts.append("\033[0m")
        lines.append("".join(parts))
    return "\n".join(lines)


def render_photo(
    url: str, user_agent: str, width_cells: int = 40, protocol: str = "ansi"
) -> str | None:
    """画像URLを取得して端末表示用の文字列を返す。失敗時 None。

    protocol: "ansi"(半ブロック・全端末対応) / "iterm2"(iTerm2系のみ)。
    """
    data = fetch_image(url, user_agent)
    if not data:
        return None
    if protocol == "iterm2":
        return iterm2_image(data, width_cells=width_cells)
    return ansi_halfblock(data, cols=width_cells)
