"""候補者ソースの共通インターフェース。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import Candidate


class CandidateSource(ABC):
    """候補者を逐次生成するソース。"""

    @abstractmethod
    def iter_candidates(self) -> Iterator[Candidate]:
        ...

    def __iter__(self) -> Iterator[Candidate]:
        return self.iter_candidates()
