"""Ephemeral run-owned browser collaborators, never serialized credentials/state."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.browser_download_stream import DownloadSpend
from ghimera.browser_navigation_types import BrowserNavigationAdmission, Digest, Identifier
from ghimera.source_session_types import origin_key, safe_path


class BrowserSourceAction(BaseModel):
    """Known request budget reservation, not total browser traffic or completion."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.browser-source-action/1"] = Field(alias="schema")
    action: Literal["main_frame_navigation", "inline_fetch"]
    url: str
    policy_digest: Digest
    target_id: Identifier

    @model_validator(mode="after")
    def explicit_source(self) -> "BrowserSourceAction":
        if origin_key(self.url) is None or safe_path(self.url) is None:
            raise ValueError("browser source actions require exact HTTP(S) URLs")
        return self


class BrowserSourceAdmission(BrowserNavigationAdmission, Protocol):
    async def admit_inline(self, url: str) -> None: ...

    async def yield_to_human(self) -> None: ...


class BrowserOutputBudget(Protocol):
    @property
    def bytes_read(self) -> int: ...

    def reading(self, maximum: int) -> AbstractAsyncContextManager[DownloadSpend]: ...


@dataclass(frozen=True)
class BrowserOperation:
    admission: BrowserSourceAdmission
    output: BrowserOutputBudget
