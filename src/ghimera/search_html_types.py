"""Bounded passive search parsing, separate from source fetching and model calls."""

from typing import Annotated

from pydantic import Field, model_validator

from ghimera.models import Page, Record
from ghimera.refusals import RefusalCode
from ghimera.research_types import SearchHit


class SearchHtmlRequest(Record):
    page: Page
    encoding: Annotated[str, Field(min_length=1)]
    limit: Annotated[int, Field(strict=True, gt=0)]


class SearchHtmlResponse(Record):
    hits: tuple[SearchHit, ...] | None = None
    refusal: RefusalCode | None = None

    @model_validator(mode="after")
    def one_outcome(self) -> "SearchHtmlResponse":
        if (self.hits is None) == (self.refusal is None):
            raise ValueError("search parsing requires one outcome")
        return self
