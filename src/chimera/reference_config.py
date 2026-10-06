"""Operator policy for following observed sources, independent of extraction."""

import ipaddress
import string
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]


class ReferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.references/1"] = Field(alias="schema")
    follow_document_references: bool
    discover_cited_by: bool
    outside_scope: Literal["refuse", "observed_public"]
    denied_hosts: tuple[str, ...]
    max_hops: Positive = 1
    max_parents: Positive
    max_candidates_per_parent: Positive
    max_queued_per_run: Positive
    max_extra_hosts: Positive
    cited_by_query_budget: Annotated[int, Field(strict=True, ge=0)]
    cited_by_query_template: Annotated[str, Field(min_length=1)]
    max_query_title_chars: Positive

    @model_validator(mode="after")
    def coherent(self) -> "ReferenceConfig":
        for host in self.denied_hosts:
            if (
                host != host.lower()
                or host.endswith(".")
                or "." not in host
                or any(
                    not label or not label.replace("-", "").isalnum() for label in host.split(".")
                )
            ):
                raise ValueError("reference policy requires exact lowercase DNS/onion hosts")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("reference policy cannot name IP literals")
        fields = tuple(string.Formatter().parse(self.cited_by_query_template))
        if any(
            key is not None and (key not in {"title", "url"} or spec or conversion)
            for _, key, spec, conversion in fields
        ) or not {"title", "url"} <= {key for _, key, _, _ in fields}:
            raise ValueError("cited-by template must use only plain {title} and {url} fields")
        if self.discover_cited_by and not self.cited_by_query_budget:
            raise ValueError("cited-by discovery requires a query budget")
        return self
