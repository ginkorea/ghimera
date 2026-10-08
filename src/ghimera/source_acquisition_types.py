"""A local return observation, never an assertion about an unknown remote outcome."""

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

if TYPE_CHECKING:
    from ghimera.config import GhimeraConfig
    from ghimera.models import LedgerRow


class SourceAcquisitionReturn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-acquisition-return/1"] = Field(alias="schema")
    operation_id: Digest
    request_sha256: Digest
    page_sha256: Digest
    source_sha256: Digest
    source_url: Annotated[str, Field(min_length=1)]
    kind: Literal["fetched", "owned_file"]


def validate_acquisition_rows(config: "GhimeraConfig", rows: tuple["LedgerRow", ...]) -> None:
    policy = config.research_recovery
    if any(row.source_acquisition is not None for row in rows) and (
        policy is None or policy.source_acquisition is None
    ):
        raise ValueError("local source returns require explicit acquisition recovery policy")
    operations = [
        row.source_acquisition.operation_id for row in rows if row.source_acquisition is not None
    ]
    if len(operations) != len(set(operations)):
        raise ValueError("one original source operation cannot acquire twice")
