"""Unit tests for offline precision@k scoring."""

from __future__ import annotations

import pytest
from app.evaluation import (
    ClassPrecision,
    PrecisionReport,
    normalize_class,
    score_ranking,
)


def test_normalize_class_is_casefolded_and_whitespace_safe() -> None:
    assert normalize_class("  Red   APPLE ") == "red apple"
    assert normalize_class(None) == ""
    assert normalize_class(123) == ""


def test_score_ranking_uses_exact_normalized_class_match() -> None:
    relevant, precision = score_ranking(
        expected_class="Dog",
        ranked_classes=["dog", "cat", " DOG ", None, "dog", "ship"],
        k=6,
    )
    assert relevant == 3
    assert precision == 0.5


def test_score_ranking_uses_fixed_k_denominator_and_ignores_tail() -> None:
    relevant, precision = score_ranking(
        expected_class="dog",
        ranked_classes=["dog", "cat"],
        k=10,
    )
    assert relevant == 1
    assert precision == 0.1


def test_missing_labels_and_aliases_are_not_relevant() -> None:
    relevant, precision = score_ranking(
        expected_class="boat",
        ranked_classes=[None, "ship", "boat"],
        k=3,
    )
    assert relevant == 1
    assert precision == pytest.approx(1 / 3)


@pytest.mark.parametrize("k", [0, -1])
def test_score_ranking_rejects_invalid_k(k: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        score_ranking(expected_class="dog", ranked_classes=[], k=k)


def test_report_computes_macro_precision() -> None:
    rows = tuple(
        ClassPrecision(
            query=label,
            expected_class=label,
            indexed_images=20,
            k=10,
            hits=10,
            relevant=int(score * 10),
            precision=score,
        )
        for label, score in (("dog", 0.8), ("cat", 0.6))
    )
    report = PrecisionReport(
        k=10,
        collection="image_embeddings",
        model="embeddinggemma-2",
        total_indexed_images=40,
        unlabeled_images=0,
        results=rows,
    )
    assert report.macro_precision == pytest.approx(0.7)
    assert report.as_dict()["classesEvaluated"] == 2
    assert report.as_dict()["metric"] == "precision@10"
