"""Re-query helper that double checks Neo4j persisted finding IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from services.graph_repo import GraphRepo

__all__ = ["FindingVerificationResult", "FindingVerifier"]


@dataclass(frozen=True)
class FindingVerificationResult:
    expected: Sequence[str]
    actual: Sequence[str]

    @property
    def matches(self) -> bool:
        return set(self.expected) == set(self.actual)


class FindingVerifier:
    """Ensure that persisted Neo4j findings match what the pipeline attempted to upsert."""

    def __init__(self, repo: GraphRepo) -> None:
        self._repo = repo

    def verify(self, image_id: str, expected_ids: Iterable[str]) -> FindingVerificationResult:
        expected_unique = sorted({fid for fid in expected_ids if isinstance(fid, str) and fid})
        actual_ids = self._repo.fetch_finding_ids(image_id, expected_unique if expected_unique else None)
        actual_unique = sorted({fid for fid in actual_ids if isinstance(fid, str) and fid})
        return FindingVerificationResult(expected=expected_unique, actual=actual_unique)
