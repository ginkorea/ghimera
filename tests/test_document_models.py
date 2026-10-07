"""Full PDF policy must name models and OCR, never borrow vendor defaults."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.document_config import DocumentExtractionConfig
from ghimera.document_models import OfflineTesseract, PdfModels
from ghimera.documents import check_artifacts
from ghimera.refusals import GhimeraRefused
from tests.test_document_extraction import config


def model_policy(**updates):
    data = dict(
        schema="chimera.pdf-models/1",
        layout_repository="docling-project/docling-layout-heron-onnx",
        layout_revision="40bde044036bb181c130ddf6c51792187268748f",
        layout_engine="onnxruntime",
        layout_model_filename="model.onnx",
        layout_score_threshold=0.3,
        table_mode="accurate",
        table_cell_matching=True,
        reading_order=dict(
            schema="chimera.reading-order/1",
            method="xy_cut",
            column_direction="left_to_right",
            min_column_gap_ratio=0.015,
            min_row_gap_ratio=0.015,
            max_recursion_depth=32,
        ),
        images_scale=2.0,
        runtime_packages=[
            dict(name=n, version=v)
            for n, v in (
                ("docling-ibm-models", "4.0.3"),
                ("torch", "2.9.1+cpu"),
                ("torchvision", "0.24.1+cpu"),
                ("transformers", "4.57.6"),
                ("onnxruntime", "1.23.2"),
                ("rapidocr", "3.9.1"),
            )
        ],
        ocr=dict(
            language="en",
            model_size="small",
            detection_path="ocr/detection.onnx",
            classification_path="ocr/classification.onnx",
            recognition_path="ocr/recognition.onnx",
            recognition_keys_path=None,
            mode="full_page",
            scale=3.0,
            text_score=0.5,
        ),
    )
    data.update(updates)
    return PdfModels.model_validate(data)


def artifacts(tmp_path, policy):
    root = tmp_path / "models"
    entries = []
    for name in policy.required_artifact_paths():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        body = name.encode()
        target.write_bytes(body)
        entries.append(
            dict(path=name, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        )
    return root, entries


def test_standard_pdf_requires_explicit_models_and_all_referenced_artifacts(tmp_path):
    policy = model_policy()
    root, entries = artifacts(tmp_path, policy)
    cfg = config(
        tmp_path,
        pdf_pipeline="standard",
        do_ocr=True,
        pdf_models=policy.model_dump(by_alias=True),
        artifacts_directory=str(root),
        artifacts=entries,
    )
    check_artifacts(cfg.document_extraction)
    for updates in (dict(pdf_models=None), dict(artifacts=entries[1:])):
        with pytest.raises(ValidationError):
            cfg.document_extraction.model_validate(
                dict(cfg.document_extraction.model_dump(by_alias=True), **updates)
            )
    (root / entries[0]["path"]).write_bytes(b"changed")
    with pytest.raises(GhimeraRefused):
        check_artifacts(cfg.document_extraction)


def test_pdf_model_policy_rejects_unpinned_or_ambiguous_choices():
    for updates in (
        dict(layout_revision="main"),
        dict(layout_repository="../model"),
        dict(layout_model_filename="../model.onnx"),
        dict(layout_score_threshold=float("nan")),
        dict(runtime_packages=[]),
        dict(images_scale=0),
        dict(layout_engine="auto"),
    ):
        with pytest.raises(ValidationError):
            model_policy(**updates)
    policy = model_policy()
    raw = policy.model_dump(by_alias=True)
    raw["ocr"]["recognition_path"] = "/outside/models.onnx"
    with pytest.raises(ValidationError):
        PdfModels.model_validate(raw)
    raw = policy.model_dump(by_alias=True)
    raw["ocr"]["recognition_keys_path"] = ""
    with pytest.raises(ValidationError):
        PdfModels.model_validate(raw)
    raw = policy.model_dump(by_alias=True)
    raw["runtime_packages"] += (raw["runtime_packages"][0],)
    with pytest.raises(ValidationError):
        PdfModels.model_validate(raw)


def test_ocr_selection_is_explicit_and_native_config_identity_stays_stable(tmp_path):
    baseline = config(tmp_path).document_extraction
    raw = baseline.model_dump(by_alias=True)
    assert "pdf_models" not in raw
    expected = hashlib.sha256(baseline.model_dump_json(by_alias=True).encode()).hexdigest()
    assert baseline.content_digest() == expected
    with pytest.raises(ValidationError):
        config(tmp_path, pdf_models=model_policy().model_dump(by_alias=True))
    policy = model_policy(ocr=None)
    root, entries = artifacts(tmp_path, policy)
    with pytest.raises(ValidationError):
        config(
            tmp_path,
            pdf_pipeline="standard",
            do_ocr=True,
            pdf_models=policy.model_dump(by_alias=True),
            artifacts_directory=str(root),
            artifacts=entries,
        )


def test_pdf_model_manifest_has_explicit_layout_table_ocr_paths():
    policy = model_policy()
    paths = policy.required_artifact_paths()
    assert "docling-project--docling-layout-heron-onnx/model.onnx" in paths
    assert (
        "docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json"
        in paths
    )
    assert "ocr/recognition.onnx" in paths
    # Changing operational/model choices changes the policy evidence.
    assert model_policy(table_mode="fast").model_dump_json() != policy.model_dump_json()
    assert "main" not in json.dumps(policy.model_dump(mode="json"))
    assert all(not Path(p).is_absolute() for p in paths)


def test_shipped_standard_recipe_parses_without_using_local_models():
    with Path("examples/documents-standard.toml").open("rb") as stream:
        policy = DocumentExtractionConfig.model_validate(tomllib.load(stream))
    assert policy.pdf_models.ocr.language == "en"
    assert policy.pdf_models.layout_revision == "40bde044036bb181c130ddf6c51792187268748f"
    assert len(policy.artifacts) == 8


def test_explicit_chinese_recipe_preserves_the_published_english_profile():
    def read(name):
        with (Path("examples") / name).open("rb") as stream:
            return DocumentExtractionConfig.model_validate(tomllib.load(stream))

    english = read("documents-standard.toml")
    chinese = read("documents-chinese-rapidocr.toml")
    assert english.pdf_models.ocr.language == "en"
    assert chinese.pdf_models.ocr.language == "ch"
    assert chinese.artifacts == english.artifacts
    assert chinese.pdf_models.runtime_packages == english.pdf_models.runtime_packages
    assert chinese.pdf_models.layout_revision == english.pdf_models.layout_revision
    assert chinese.content_digest() != english.content_digest()
    assert chinese.worker_python.as_posix().startswith("/path/to/")
    assert chinese.artifacts_directory.as_posix().startswith("/path/to/")
    assert chinese.work_directory.as_posix().startswith("/path/to/")


def test_published_native_recipe_keeps_its_pre_extension_digest():
    with Path("examples/documents-native.toml").open("rb") as stream:
        policy = DocumentExtractionConfig.model_validate(tomllib.load(stream))
    # Independently computed with the actual be0eccc (published 0.2.0) class.
    assert (
        policy.content_digest()
        == "1909013fa9610d348f93efbfbd3ef8c3fd2cbc752f396b8f7317117f7a9b1a8a"
    )


def tesseract_policy(**updates):
    raw = model_policy().model_dump(by_alias=True)
    raw["schema"] = "ghimera.pdf-models/2"
    raw["runtime_packages"] = [
        pin for pin in raw["runtime_packages"] if pin["name"] != "rapidocr"
    ] + [dict(name="tesserocr", version="2.11.0")]
    raw["ocr"] = dict(
        engine="tesserocr",
        languages=["chi_sim", "chi_tra", "jpn", "jpn_vert", "kor", "fil", "eng"],
        data_directory="tessdata",
        native_version="tesseract 5.5.1",
        page_segmentation=6,
        orientation="preserve",
        minimum_orientation_confidence=15.0,
        mode="full_page",
        scale=3.0,
    )
    raw.update(updates)
    return PdfModels.model_validate(raw)


def test_pacific_pdf_requires_explicit_packs_and_orientation_data(tmp_path):
    policy = tesseract_policy()
    assert isinstance(policy.ocr, OfflineTesseract)
    paths = policy.required_artifact_paths()
    assert "tessdata/chi_sim.traineddata" in paths
    assert "tessdata/chi_tra.traineddata" in paths
    assert "tessdata/jpn_vert.traineddata" in paths
    assert "tessdata/fil.traineddata" in paths
    assert "tessdata/osd.traineddata" in paths
    root, entries = artifacts(tmp_path, policy)
    cfg = config(
        tmp_path,
        pdf_pipeline="standard",
        do_ocr=True,
        pdf_models=policy.model_dump(by_alias=True),
        artifacts_directory=str(root),
        artifacts=entries,
    )
    check_artifacts(cfg.document_extraction)
    for absent in ("tessdata/osd.traineddata", "tessdata/chi_tra.traineddata"):
        with pytest.raises(ValidationError):
            config(
                tmp_path,
                pdf_pipeline="standard",
                do_ocr=True,
                pdf_models=policy.model_dump(by_alias=True),
                artifacts_directory=str(root),
                artifacts=[entry for entry in entries if entry["path"] != absent],
            )


def test_new_pdf_engine_cannot_change_legacy_schema_or_borrow_defaults():
    baseline = tesseract_policy().model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        PdfModels.model_validate(dict(baseline, schema="chimera.pdf-models/1"))
    for update in (
        dict(languages=[]),
        dict(languages=["eng", "eng"]),
        dict(languages=["../chi_sim"]),
        dict(languages=["iso:zh"]),
        dict(data_directory="/usr/share/tessdata"),
        dict(page_segmentation=0),
        dict(page_segmentation=2),
        dict(page_segmentation=14),
        dict(native_version="auto"),
        dict(scale=0),
    ):
        with pytest.raises(ValidationError):
            tesseract_policy(ocr=dict(baseline["ocr"], **update))
    with pytest.raises(ValidationError):
        tesseract_policy(runtime_packages=model_policy().model_dump()["runtime_packages"])


def test_shipped_pacific_recipe_declares_distinct_scripts_and_native_tagalog_pack():
    with Path("examples/documents-pacific.toml").open("rb") as stream:
        cfg = DocumentExtractionConfig.model_validate(tomllib.load(stream))
    assert isinstance(cfg.pdf_models.ocr, OfflineTesseract)
    packs = set(cfg.pdf_models.ocr.languages)
    assert {"chi_sim", "chi_tra", "jpn", "jpn_vert", "kor", "fil"} <= packs
    assert {"tl", "ja", "ko", "zh", "id", "ms", "vi", "th"} <= set(cfg.languages)
    assert len(cfg.artifacts) == 17
