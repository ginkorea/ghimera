"""Lightweight explicit model choices for offline Docling PDF conversion."""

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.document_order import ReadingOrderPolicy

Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


def relative_artifact(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and not any(part in {"", ".", ".."} for part in value.split("/"))
        and "\\" not in value
        and not any(ord(char) < 32 for char in value)
    )


class RuntimePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    version: Annotated[str, Field(pattern=r"^[0-9][a-zA-Z0-9.+_-]*$")]


class OfflineOcr(BaseModel):
    """One named recognizer per run; no auto-language or download fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    language: Annotated[str, Field(pattern=r"^(?:iso:)?[a-zA-Z][a-zA-Z0-9_-]*$")]
    model_size: Literal["tiny", "small", "medium"]
    detection_path: str
    classification_path: str
    recognition_path: str
    recognition_keys_path: str | None = None
    mode: Literal["full_page", "default", "layout_regions", "pdf_aware_layout_regions"]
    scale: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    text_score: Probability

    def artifact_paths(self) -> tuple[str, ...]:
        values = (self.detection_path, self.classification_path, self.recognition_path)
        return values + (
            (self.recognition_keys_path,) if self.recognition_keys_path is not None else ()
        )

    @model_validator(mode="after")
    def paths_are_relative(self) -> "OfflineOcr":
        if not all(relative_artifact(value) for value in self.artifact_paths()):
            raise ValueError("OCR files must be explicit relative paths in the artifact manifest")
        return self


class OfflineTesseract(BaseModel):
    """Explicit offline packs for Docling's in-process native OCR binding.

    Native work stays inside the owned parser process and its deadline. Unlike
    the CLI adapter, it cannot leave an OCR subprocess behind on cancellation.
    Pack order is preference order; no script guessing or system data fallback.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    engine: Literal["tesserocr"]
    languages: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")], ...],
        Field(min_length=1),
    ]
    data_directory: str
    native_version: Annotated[str, Field(pattern=r"^tesseract [0-9]+\.[0-9]+\.[0-9]+$")]
    page_segmentation: Literal[1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    orientation: Literal["preserve", "detect"]
    minimum_orientation_confidence: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    mode: Literal["full_page", "default", "layout_regions", "pdf_aware_layout_regions"]
    scale: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def explicit_packs(self) -> "OfflineTesseract":
        if not relative_artifact(self.data_directory):
            raise ValueError("tessdata must be an explicit relative artifact directory")
        if len(set(self.languages)) != len(self.languages) or "osd" in self.languages:
            raise ValueError("OCR languages must be unique text packs, not orientation data")
        return self

    def artifact_paths(self) -> tuple[str, ...]:
        # Pinned Docling 2.134.0 initializes the OSD reader even for explicit
        # languages. Admit that auxiliary file too, rather than use system data.
        return tuple(
            f"{self.data_directory}/{language}.traineddata" for language in (*self.languages, "osd")
        )


class PdfModels(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.pdf-models/1", "ghimera.pdf-models/2"] = Field(alias="schema")
    layout_repository: Annotated[
        str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*/[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    ]
    layout_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    layout_engine: Literal["onnxruntime", "transformers"]
    layout_model_filename: str
    layout_score_threshold: Probability
    table_mode: Literal["fast", "accurate"]
    table_cell_matching: bool
    reading_order: ReadingOrderPolicy
    images_scale: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    runtime_packages: Annotated[tuple[RuntimePackage, ...], Field(min_length=1)]
    ocr: OfflineOcr | OfflineTesseract | None

    @model_validator(mode="after")
    def coherent(self) -> "PdfModels":
        if not relative_artifact(self.layout_model_filename):
            raise ValueError("layout model file must be relative to its admitted repository")
        if isinstance(self.ocr, OfflineTesseract) != (
            self.schema_version == "ghimera.pdf-models/2"
        ):
            raise ValueError("the tesserocr engine requires the explicit PDF models/2 contract")
        names = {package.name for package in self.runtime_packages}
        required = {"docling-ibm-models", "torch", "torchvision", "transformers"}
        if self.layout_engine == "onnxruntime" or isinstance(self.ocr, OfflineOcr):
            required.add("onnxruntime")
        if isinstance(self.ocr, OfflineOcr):
            required.add("rapidocr")
        if isinstance(self.ocr, OfflineTesseract):
            required.add("tesserocr")
        if len(names) != len(self.runtime_packages) or not required <= names:
            raise ValueError("PDF runtime must pin each required dependency exactly once")
        return self

    def required_artifact_paths(self, *, with_tables: bool = True) -> tuple[str, ...]:
        layout = self.layout_repository.replace("/", "--")
        values: tuple[str, ...] = (
            f"{layout}/config.json",
            f"{layout}/preprocessor_config.json",
            f"{layout}/{self.layout_model_filename}",
        )
        if with_tables:
            table = f"docling-project--docling-models/model_artifacts/tableformer/{self.table_mode}"
            values += (
                f"{table}/tm_config.json",
                f"{table}/tableformer_{self.table_mode}.safetensors",
            )
        if self.ocr is not None:
            values += self.ocr.artifact_paths()
        return values
