"""候補者取り込みパッケージ（CSV / 貼り付けテキスト / ビズリーチ）。"""

from .base import CandidateSource
from .csv_source import CSVSource
from .text_source import TextSource

__all__ = ["CandidateSource", "CSVSource", "TextSource"]
