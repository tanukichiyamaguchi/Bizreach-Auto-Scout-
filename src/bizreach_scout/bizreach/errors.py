"""ビズリーチ連携の例外型。"""

from __future__ import annotations


class BizreachAuthError(RuntimeError):
    """認証切れ（PasswordExpired / Unauthorized 等）。

    リトライしても回復しない恒久的な失敗として扱い、サイクル全体を
    エラー終了（exit 非0）へ導く。GitHub Actions 上で「成功に見えるのに
    1件も送れていない」事故を防ぐ。
    """
