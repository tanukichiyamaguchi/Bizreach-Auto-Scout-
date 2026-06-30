"""ビズリーチ画面のセレクタ定義。

重要: ここに定義したセレクタは「想定値」です。ビズリーチのUIは変更されるため、
実運用前に必ずブラウザの開発者ツールで実際のセレクタを確認し、
config/bizreach_selectors.yaml で上書きしてください（このファイルを書き換えなくても良い）。

URL もアカウント種別（採用企業/エージェント）により異なる場合があります。
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from ..config import project_root


@dataclass
class Selectors:
    # --- URL ---
    login_url: str = "https://cr-support.jp/login"  # 採用企業向けログイン（要確認）
    base_url: str = "https://cr-support.jp"
    search_url: str = "https://cr-support.jp/search"  # 候補者検索（要確認）

    # --- ログイン ---
    login_email: str = "input[name='email'], input[type='email']"
    login_password: str = "input[name='password'], input[type='password']"
    login_submit: str = "button[type='submit']"
    logged_in_marker: str = "text=ログアウト"  # ログイン成功の判定要素（要確認）

    # --- 検索結果 ---
    result_card: str = "[data-testid='candidate-card'], .candidate-list-item"
    result_member_no: str = "[data-testid='member-no'], .member-no"
    result_link: str = "a[href*='/candidate/'], a[href*='/resume/']"
    next_page: str = "a[rel='next'], button[aria-label='次へ']"

    # --- プロフィール ---
    profile_root: str = "main, [role='main'], .resume-detail"
    profile_member_no: str = "[data-testid='member-no'], .member-no"

    # --- スカウト送信 ---
    scout_button: str = "text=スカウト, text=プラチナスカウト, [data-testid='scout-button']"
    scout_subject: str = "input[name='subject'], [data-testid='scout-subject']"
    scout_body: str = "textarea[name='body'], [data-testid='scout-body']"
    scout_send: str = "[data-testid='scout-send'], button:has-text('送信')"
    scout_confirm: str = "button:has-text('送信する'), button:has-text('OK')"
    scout_sent_marker: str = "text=送信しました, text=スカウトを送信"


def load_selectors() -> Selectors:
    """config/bizreach_selectors.yaml があれば上書きして返す。"""
    path = project_root() / "config" / "bizreach_selectors.yaml"
    base = Selectors()
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            if hasattr(base, k) and v:
                setattr(base, k, v)
    return base
