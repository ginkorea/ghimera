"""Explicit policy for native model-boundary research recovery."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.model_reconciliation_types import ModelReconciliationPolicy


class SourceCompletionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-completion-recovery/1"] = Field(alias="schema")
    execution: Literal["serial"]
    max_capsule_bytes: Annotated[int, Field(strict=True, gt=0)]


class SourceAcquisitionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.source-acquisition-recovery/1"] = Field(alias="schema")
    execution: Literal["serial"]
    max_capsule_bytes: Annotated[int, Field(strict=True, gt=0)]


class ResearchRecoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal[
        "ghimera.research-recovery/1",
        "ghimera.research-recovery/2",
        "ghimera.research-recovery/3",
        "ghimera.research-recovery/4",
        "ghimera.research-recovery/5",
        "ghimera.research-recovery/6",
    ] = Field(alias="schema")
    max_snapshot_bytes: Annotated[int, Field(strict=True, gt=0)]
    clock_policy: Literal["include_downtime"]
    tail_policy: Literal["acknowledged_model_return"]
    source_completion: SourceCompletionPolicy | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_reconciliation: ModelReconciliationPolicy | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    query_control: Literal["serial_acknowledged"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    source_acquisition: SourceAcquisitionPolicy | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def versioned(self) -> "ResearchRecoveryConfig":
        if self.schema_version in {
            "ghimera.research-recovery/4",
            "ghimera.research-recovery/5",
            "ghimera.research-recovery/6",
        }:
            if self.model_fields_set & {"source_completion", "model_reconciliation"}:
                raise ValueError("recovery /4-/6 cannot carry unrelated legacy controls")
            query = self.schema_version in {
                "ghimera.research-recovery/4",
                "ghimera.research-recovery/6",
            }
            acquisition = self.schema_version in {
                "ghimera.research-recovery/5",
                "ghimera.research-recovery/6",
            }
            if query:
                if self.query_control != "serial_acknowledged":
                    raise ValueError("query recovery requires explicit serial acknowledged control")
            elif "query_control" in self.model_fields_set:
                raise ValueError("source-only recovery cannot carry query control")
            if acquisition:
                if self.source_acquisition is None:
                    raise ValueError(
                        "acquired-source recovery requires explicit acquisition policy"
                    )
            elif "source_acquisition" in self.model_fields_set:
                raise ValueError("query-only recovery cannot carry source acquisition")
            return self
        if "source_acquisition" in self.model_fields_set:
            raise ValueError("source acquisition requires explicit recovery /5 or /6")
        if self.query_control is not None:
            raise ValueError("query control requires explicit recovery /4 or /6")
        if self.schema_version == "ghimera.research-recovery/3":
            if self.model_reconciliation is None:
                raise ValueError("recovery /3 requires explicit model reconciliation policy")
        elif self.model_reconciliation is not None:
            raise ValueError("model reconciliation requires explicit recovery /3")
        elif (self.schema_version == "ghimera.research-recovery/2") != (
            self.source_completion is not None
        ):
            raise ValueError("source completion requires explicit recovery /2 policy")
        return self
