from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import Executor, Future
from typing import Protocol

from adapters.scorer._base import ScorerResult
from core.domain import Artifact


class AsyncScorer(Protocol):
    def score_optional(self, session_id: str, artifact: Artifact) -> ScorerResult: ...


def dispatch_scorers(
    session_id: str,
    artifact: Artifact,
    scorers: Sequence[AsyncScorer],
    *,
    executor: Executor,
) -> tuple[Future[ScorerResult], ...]:
    return tuple(
        executor.submit(scorer.score_optional, session_id, artifact)
        for scorer in scorers
    )
