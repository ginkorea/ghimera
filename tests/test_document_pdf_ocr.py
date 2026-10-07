"""The worker maps explicit native OCR policy, without auto/download fallback."""

import json
import subprocess
import sys

from tests.test_document_extraction import config
from tests.test_document_models import artifacts, model_policy, tesseract_policy


def configured(tmp_path, policy):
    root, entries = artifacts(tmp_path, policy)
    return config(
        tmp_path,
        pdf_pipeline="standard",
        do_ocr=True,
        pdf_models=policy.model_dump(by_alias=True),
        artifacts_directory=str(root),
        artifacts=entries,
    ).document_extraction


def worker_options(cfg):
    # Heavy vendor imports belong only in an owned child, just like production.
    # Importing in pytest's parent would break lightweight-import conformance.
    script = """
import json, sys
from ghimera.document_config import DocumentExtractionConfig
from ghimera import document_pipeline
from ghimera.refusals import GhimeraRefused
config = DocumentExtractionConfig.model_validate_json(sys.stdin.read())
# This fixture checks option mapping, not installed runtime admission. Real
# package/native-engine admission is exercised by the separate PDF acceptance.
pins = {pin.name: pin.version for pin in config.pdf_models.runtime_packages}
document_pipeline.version = pins.__getitem__
try:
    options = document_pipeline.pdf_options(config)
    snapshot = options.model_dump(mode="json")
    snapshot["ocr_options"] = options.ocr_options.model_dump(mode="json")
    snapshot["ocr_options"]["kind"] = options.ocr_options.kind
    print(json.dumps(snapshot))
except GhimeraRefused as exc:
    print(json.dumps({"refused": exc.code.value}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=cfg.model_dump_json(by_alias=True),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def test_pdf_pacific_policy_preserves_packs_order_path_and_no_cli(tmp_path):
    cfg = configured(tmp_path, tesseract_policy())
    options = worker_options(cfg)
    assert options["ocr_options"]["kind"] == "tesserocr"
    assert options["ocr_options"]["lang"] == [
        "chi_sim",
        "chi_tra",
        "jpn",
        "jpn_vert",
        "kor",
        "fil",
        "eng",
    ]
    assert options["ocr_options"]["path"] == str(cfg.artifacts_directory / "tessdata")
    assert options["ocr_options"]["psm"] == 6
    assert options["ocr_options"]["mode"] == "full_page"
    assert not options["enable_remote_services"]
    assert not options["allow_external_plugins"]
    assert options["accelerator_options"]["device"] == "cpu"


def test_pdf_pacific_rejects_changed_native_engine_before_model_initialization(tmp_path):
    policy = tesseract_policy()
    cfg = configured(
        tmp_path,
        tesseract_policy(ocr=dict(policy.ocr.model_dump(), native_version="tesseract 5.4.0")),
    )
    assert worker_options(cfg) == {"refused": "adapter_contract"}


def test_legacy_pdf_recognizer_keeps_its_existing_options(tmp_path):
    cfg = configured(tmp_path, model_policy())
    options = worker_options(cfg)
    assert options["ocr_options"]["kind"] == "rapidocr"
    assert options["ocr_options"]["lang"] == ["en"]
    assert options["ocr_options"]["rec_model_path"] == str(
        cfg.artifacts_directory / "ocr/recognition.onnx"
    )
