"""Explicit native-page click mappings and retained landing-page evidence."""

import hashlib
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.browser_navigation_types import BrowserNavigationEvidence
from ghimera.source_session_types import origin_key, safe_path


class BrowserPaginationAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_url: str
    navigation_url: str
    selector: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def explicit_source(self) -> "BrowserPaginationAction":
        if self.source_url == self.navigation_url or any(
            origin_key(url) is None or safe_path(url) is None
            for url in (self.source_url, self.navigation_url)
        ):
            raise ValueError("pagination requires distinct exact safe landing and source URLs")
        return self


class BrowserPaginationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: BrowserPaginationAction
    landing_navigation: BrowserNavigationEvidence
    landing_dom: bytes = Field(repr=False)
    landing_dom_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def exact_original(self) -> "BrowserPaginationEvidence":
        if (
            not self.landing_dom
            or hashlib.sha256(self.landing_dom).hexdigest() != (self.landing_dom_sha256)
            or self.landing_navigation.request_url != self.action.navigation_url
        ):
            raise ValueError("pagination landing observation requires its actual DOM and chain")
        self.landing_dom.decode("utf-8", errors="strict")
        return self
