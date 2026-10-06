"""Caller-supplied authorized headers; no browser, environment or login discovery."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.source_session_types import (
    SourceSessionPolicy,
    SourceSessionUse,
    credential_header,
    validate_sessions,
)


class SourceCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    headers: Annotated[tuple[tuple[str, SecretStr], ...], Field(min_length=1, repr=False)]

    @model_validator(mode="after")
    def single_lines(self) -> "SourceCredentials":
        if len({name for name, _ in self.headers}) != len(self.headers):
            raise ValueError("source credentials require unique header names")
        for name, secret in self.headers:
            value = secret.get_secret_value()
            if (
                not credential_header(name)
                or not value
                or any(ord(char) < 32 or ord(char) > 126 for char in value)
            ):
                raise ValueError("source credentials require declared single-line ASCII values")
        return self


@dataclass(frozen=True)
class SelectedSourceSession:
    evidence: SourceSessionUse
    headers: tuple[tuple[str, SecretStr], ...]


class SourceSessions:
    """Immutable binding; every redirect/resource selects independently by URL."""

    def __init__(
        self,
        policies: tuple[SourceSessionPolicy, ...],
        credentials: Mapping[str, SourceCredentials] | None = None,
    ) -> None:
        validate_sessions(policies)
        supplied = dict(credentials or {})
        if set(supplied) != {policy.session_id for policy in policies}:
            raise GhimeraRefused(RefusalCode.SOURCE_SESSION_UNAVAILABLE)
        if any(
            set(policy.header_names) != {name for name, _ in supplied[policy.session_id].headers}
            for policy in policies
        ):
            raise GhimeraRefused(RefusalCode.SOURCE_SESSION_UNAVAILABLE)
        self._bindings = tuple((policy, supplied[policy.session_id]) for policy in policies)

    def select(self, url: str) -> SelectedSourceSession | None:
        for policy, credential in self._bindings:
            if policy.permits(url):
                return SelectedSourceSession(
                    evidence=SourceSessionUse(
                        schema="chimera.source-session-use/1",
                        session_id=policy.session_id,
                        request_url=url,
                        origin=policy.origin,
                        header_names=policy.header_names,
                        policy_digest=policy.content_digest(),
                    ),
                    headers=credential.headers,
                )
        return None
