"""Manifest and configuration contract tests."""

from __future__ import annotations

import json

import pytest
from app.models.manifest import FetchManifest
from pydantic import ValidationError
from tests.support import make_settings, manifest_document, manifest_image, png_bytes


def test_settings_required_prefixes() -> None:
    settings = make_settings()
    assert settings.image_prefix == "images/bank-mas/"
    assert settings.jobs_prefix == "jobs/bank-mas/"
    assert settings.manifest_key("fetch-1") == "jobs/bank-mas/fetch-1/fetch-manifest.json"
    assert settings.ingestion_status_key("fetch-1") == "jobs/bank-mas/fetch-1/ingestion-status.json"
    assert settings.is_image_key("images/bank-mas/abc.png")
    assert not settings.is_image_key("images/legacy/abc.png")
    assert not settings.is_image_key("images/bank-mas/../abc.png")
    assert not settings.is_image_key("images/bank-mas/")


def test_prefixes_are_configurable_and_normalized() -> None:
    settings = make_settings(image_prefix="custom/images", jobs_prefix="/custom/jobs/")
    assert settings.image_prefix == "custom/images/"
    assert settings.jobs_prefix == "/custom/jobs/"
    assert settings.is_image_key("custom/images/x.jpg")


def test_manifest_parsing_keeps_evaluation_metadata_verbatim() -> None:
    data = png_bytes()
    document = manifest_document(images=[manifest_image(data)])
    manifest = FetchManifest.model_validate(document)
    assert manifest.job_id == "fetch-test-001"
    assert manifest.status == "COMPLETED"
    assert manifest.requested_classes == ["dog", "cat"]
    image = manifest.images[0]
    assert image.evaluation_metadata["title"] == "A dog in the snow"
    assert image.evaluation_metadata["tags"] == ["dog", "snow"]
    assert image.content_type == "image/png"
    assert image.width == 16


def test_manifest_accepts_extra_evaluation_fields() -> None:
    data = png_bytes()
    image = manifest_image(data)
    image["evaluationMetadata"]["unknownField"] = {"nested": [1, 2]}
    manifest = FetchManifest.model_validate(manifest_document(images=[image]))
    assert manifest.images[0].evaluation_metadata["unknownField"] == {"nested": [1, 2]}


def test_manifest_serializes_camel_case() -> None:
    data = png_bytes()
    manifest = FetchManifest.model_validate(
        manifest_document(
            images=[manifest_image(data)],
            failed_downloads=[{"requestedClass": "cat"}],
        )
    )
    dumped = manifest.model_dump(by_alias=True, mode="json")
    assert dumped["jobId"] == "fetch-test-001"
    assert dumped["failedDownloads"][0]["requestedClass"] == "cat"
    assert dumped["images"][0]["objectKey"].startswith("images/bank-mas/")
    assert dumped["images"][0]["evaluationMetadata"]["requestedClass"] == "dog"
    json.dumps(dumped)


def test_manifest_requires_job_id_and_images_list() -> None:
    with pytest.raises(ValidationError):
        FetchManifest.model_validate({"status": "COMPLETED"})
    with pytest.raises(ValidationError):
        FetchManifest.model_validate({"jobId": "fetch-1", "status": "COMPLETED", "images": "x"})


def test_manifest_status_helpers() -> None:
    data = png_bytes()
    completed = FetchManifest.model_validate(manifest_document(images=[manifest_image(data)]))
    failed = FetchManifest.model_validate(manifest_document(images=[], status="FAILED"))
    assert completed.is_ingestable is True
    assert failed.is_ingestable is False
