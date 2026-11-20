"""Offline precision@k scoring with exact, normalized class matching.

Evaluation labels are intentionally separate from search ranking. The search
path returns image IDs first; only then does the evaluator read their labels.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def normalize_class(value: object) -> str:
    """Normalize a class label without changing its meaning."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().casefold().split())


@dataclass(frozen=True, slots=True)
class ClassPrecision:
    query: str
    expected_class: str
    indexed_images: int
    k: int
    hits: int
    relevant: int
    precision: float

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "expectedClass": self.expected_class,
            "indexedImages": self.indexed_images,
            "k": self.k,
            "hits": self.hits,
            "relevant": self.relevant,
            "precision": self.precision,
        }


@dataclass(frozen=True, slots=True)
class PrecisionReport:
    k: int
    collection: str
    model: str
    total_indexed_images: int
    unlabeled_images: int
    results: tuple[ClassPrecision, ...]

    @property
    def macro_precision(self) -> float:
        if not self.results:
            return 0.0
        return sum(item.precision for item in self.results) / len(self.results)

    def as_dict(self) -> dict[str, object]:
        return {
            "metric": f"precision@{self.k}",
            "method": "exact normalized requestedClass match after vector ranking",
            "groundTruth": "evaluation_metadata.requestedClass (weak labels)",
            "ranking": "EmbeddingGemma 2 text embedding + L2 normalization + Milvus COSINE",
            "collection": self.collection,
            "model": self.model,
            "k": self.k,
            "totalIndexedImages": self.total_indexed_images,
            "unlabeledImages": self.unlabeled_images,
            "classesEvaluated": len(self.results),
            "macroPrecision": self.macro_precision,
            "results": [item.as_dict() for item in self.results],
        }


def score_ranking(
    *,
    expected_class: str,
    ranked_classes: Sequence[object],
    k: int,
) -> tuple[int, float]:
    """Return relevant count and precision@k using k as the fixed denominator.

    Missing or malformed labels are non-relevant. No fuzzy match, aliases, tags,
    titles, or semantic label matching are used.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    expected = normalize_class(expected_class)
    if not expected:
        raise ValueError("expected_class must not be empty")
    relevant = sum(normalize_class(label) == expected for label in ranked_classes[:k])
    return relevant, relevant / k
