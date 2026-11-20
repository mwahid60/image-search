"""Run offline precision@k evaluation against a Milvus Lite snapshot.

Example inside the backend container:

    python -m app.evaluate --db-path /tmp/milvus-evaluation.db --k 10

The database should be a snapshot copied while no ingestion is running. This
keeps the live Milvus Lite file single-process and leaves production data intact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.evaluation import ClassPrecision, PrecisionReport, normalize_class, score_ranking
from app.integrations.embeddings import LlamaCppEmbeddingsClient
from app.integrations.vector_store import MilvusVectorStore
from app.utils import l2_normalize


def _evaluation_class(row: dict[str, Any]) -> str:
    metadata = row.get("evaluation_metadata")
    if not isinstance(metadata, dict):
        return ""
    return normalize_class(metadata.get("requestedClass"))


def _read_all_metadata(client: Any, collection: str) -> list[dict[str, Any]]:
    rows = client.query(
        collection_name=collection,
        filter="",
        output_fields=["image_id", "evaluation_metadata"],
        limit=16_384,
    )
    if len(rows) >= 16_384:
        raise RuntimeError("Evaluation requires pagination for collections of 16,384+ rows")
    return rows


def _read_hit_metadata(client: Any, collection: str, image_ids: Sequence[str]) -> dict[str, str]:
    if not image_ids:
        return {}
    rows = client.query(
        collection_name=collection,
        filter=f"image_id in {json.dumps(list(image_ids))}",
        output_fields=["image_id", "evaluation_metadata"],
        limit=len(image_ids),
    )
    return {
        str(row.get("image_id") or ""): _evaluation_class(row)
        for row in rows
        if row.get("image_id")
    }


def _selected_classes(
    rows: list[dict[str, Any]], requested: set[str], min_images: int
) -> tuple[list[str], Counter[str], int]:
    labels = [_evaluation_class(row) for row in rows]
    counts = Counter(label for label in labels if label)
    unlabeled = len(labels) - sum(counts.values())
    classes = sorted(
        label
        for label, count in counts.items()
        if count >= min_images and (not requested or label in requested)
    )
    if requested:
        unknown = sorted(requested - set(counts))
        if unknown:
            raise ValueError(f"Classes not found in Milvus: {', '.join(unknown)}")
    return classes, counts, unlabeled


async def _run(args: argparse.Namespace, settings: Settings) -> PrecisionReport:
    from pymilvus import MilvusClient

    client = MilvusClient(uri=args.db_path)
    embeddings = LlamaCppEmbeddingsClient(settings)
    vectors = MilvusVectorStore(settings, client=client)
    try:
        rows = _read_all_metadata(client, settings.milvus_collection)
        classes, counts, unlabeled = _selected_classes(rows, args.class_names, args.min_images)
        results: list[ClassPrecision] = []
        for expected_class in classes:
            # Search uses only the query vector. Evaluation metadata is absent
            # from SEARCH_OUTPUT_FIELDS and cannot influence ranking.
            raw_vector = await embeddings.embed_text(expected_class)
            query_vector = l2_normalize(raw_vector, dimension=settings.embedding_dimension)
            hits = await vectors.search(query_vector, args.k)

            # Ground truth is read only after ranking, for returned IDs only.
            labels = _read_hit_metadata(
                client, settings.milvus_collection, [hit.image_id for hit in hits]
            )
            relevant, precision = score_ranking(
                expected_class=expected_class,
                ranked_classes=[labels.get(hit.image_id) for hit in hits],
                k=args.k,
            )
            results.append(
                ClassPrecision(
                    query=expected_class,
                    expected_class=expected_class,
                    indexed_images=counts[expected_class],
                    k=args.k,
                    hits=len(hits),
                    relevant=relevant,
                    precision=precision,
                )
            )
        if not results:
            raise ValueError("No classes matched the evaluation filters")
        return PrecisionReport(
            k=args.k,
            collection=settings.milvus_collection,
            model=settings.llama_model,
            total_indexed_images=len(rows),
            unlabeled_images=unlabeled,
            results=tuple(results),
        )
    finally:
        await embeddings.close()
        await vectors.close()


def _print_report(report: PrecisionReport) -> None:
    print(f"Collection: {report.collection}")
    print(f"Model: {report.model}")
    print(f"Indexed images: {report.total_indexed_images}")
    print("Ground truth: exact requestedClass match (weak labels)")
    print()
    print(f"{'Class':<24} {'Images':>7} {'Hits':>5} {'Relevant':>9} {'Precision':>11}")
    print("-" * 62)
    for item in report.results:
        print(
            f"{item.expected_class:<24} {item.indexed_images:>7} {item.hits:>5} "
            f"{item.relevant:>9} {item.precision:>10.4f}"
        )
    print("-" * 62)
    print(f"Macro precision@{report.k}: {report.macro_precision:.4f}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default="",
        help="Milvus Lite snapshot path (default: MILVUS_DB_PATH)",
    )
    parser.add_argument("--k", type=int, default=10, help="Ranking depth (default: 10)")
    parser.add_argument(
        "--classes",
        default="",
        help="Optional comma-separated class subset; default: all indexed classes",
    )
    parser.add_argument(
        "--min-images",
        type=int,
        default=1,
        help="Exclude classes with fewer indexed images (default: 1)",
    )
    parser.add_argument("--output", default="", help="Optional JSON report path")
    parser.add_argument(
        "--llama-base-url",
        default="",
        help="Optional llama.cpp URL override (default: LLAMA_BASE_URL)",
    )
    args = parser.parse_args(argv)
    args.class_names = {
        label for value in args.classes.split(",") if (label := normalize_class(value))
    }
    if args.k < 1:
        parser.error("--k must be at least 1")
    if args.min_images < 1:
        parser.error("--min-images must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    if args.llama_base_url:
        settings = settings.model_copy(update={"llama_base_url": args.llama_base_url.rstrip("/")})
    if not args.db_path:
        args.db_path = settings.milvus_db_path
    if args.k > settings.search_max_top_k:
        raise SystemExit(f"--k must not exceed {settings.search_max_top_k}")

    report = asyncio.run(_run(args, settings))
    _print_report(report)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n")
        print(f"JSON report: {output}")


if __name__ == "__main__":
    main()
