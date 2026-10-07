"""Explicit terminal HumanAssistant; cancellable reads, never credential collection."""

import asyncio
import json
import os
import sys
import time
from typing import Annotated, Literal, TextIO
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from ghimera.human_browser_types import AssistanceDecision, BrowserAssistanceRequest


class TerminalAssistanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.terminal-assistance/1"] = Field(alias="schema")
    # "decline " + a full SHA256 + newline is 73 bytes. Shorter caps cannot
    # express the required exact-request decision wire.
    max_response_bytes: Annotated[int, Field(strict=True, ge=73)]
    max_response_attempts: Annotated[int, Field(strict=True, gt=0)]


class TerminalHumanAssistant:
    """Borrow terminal streams. No input() thread, terminal mutation or FD close.

    Requires a POSIX selector-capable event loop and canonical terminal input.
    A lock permits one active prompt; its wait counts against the request's
    existing human-assistance deadline. Late answers cannot approve a different
    request because each answer includes the full immutable request digest.
    """

    def __init__(self, policy: TerminalAssistanceConfig, *, input_fd: int, output: TextIO) -> None:
        self._policy = TerminalAssistanceConfig.model_validate(policy.model_dump())
        if not os.isatty(input_fd) or not output.isatty():
            raise ValueError("human assistance requires interactive terminal input and output")
        self._input_fd, self._output = input_fd, output
        self._lock = asyncio.Lock()

    @classmethod
    def from_standard_streams(cls, policy: TerminalAssistanceConfig) -> "TerminalHumanAssistant":
        return cls(policy, input_fd=sys.stdin.fileno(), output=sys.stderr)

    async def _readline(self) -> bytes:
        loop = asyncio.get_running_loop()
        completed: asyncio.Future[bytes] = loop.create_future()
        received = bytearray()

        def ready() -> None:
            if completed.done():
                return
            try:
                part = os.read(self._input_fd, self._policy.max_response_bytes + 1 - len(received))
                if not part:
                    completed.set_exception(OSError("human assistance input closed"))
                    return
                received.extend(part)
                if len(received) > self._policy.max_response_bytes:
                    completed.set_exception(ValueError("human assistance input exceeded its cap"))
                elif b"\n" in received:
                    completed.set_result(bytes(received))
            except OSError:
                completed.set_exception(OSError("human assistance terminal read failed"))

        try:
            loop.add_reader(self._input_fd, ready)
        except (NotImplementedError, PermissionError):
            raise ValueError("terminal assistance needs a selector-capable event loop") from None
        try:
            return await completed
        finally:
            loop.remove_reader(self._input_fd)

    @staticmethod
    def _display_url(url: str) -> str:
        parts = urlsplit(url)
        # Never display query/fragment credentials or userinfo. ASCII JSON
        # rendering below also escapes terminal controls and bidi characters.
        authority = parts.hostname or ""
        if ":" in authority:
            authority = "[" + authority + "]"
        if parts.port is not None:
            authority += ":" + str(parts.port)
        return urlunsplit((parts.scheme, authority, parts.path, "", ""))

    async def assist(self, request: BrowserAssistanceRequest) -> AssistanceDecision:
        request = BrowserAssistanceRequest.model_validate(request.model_dump())
        digest = request.content_digest()
        action: Literal["resume", "decline", "timeout"] = "timeout"
        remaining = request.deadline_unix_seconds - time.time()
        if remaining <= 0:
            return AssistanceDecision(request_digest=digest, action=action)
        try:
            async with asyncio.timeout(remaining), self._lock:
                prompt = {
                    "reason": request.reason,
                    "source": self._display_url(request.final_url),
                    "session_id": request.session_id,
                    "target_id": request.target_id,
                    "capture_id": request.capture_id,
                    "request_digest": digest,
                    "deadline_unix_seconds": request.deadline_unix_seconds,
                }
                self._output.write(
                    "human_assistance:" + json.dumps(prompt, ensure_ascii=True) + "\n"
                )
                self._output.write(
                    "Act in the selected browser, then reply: resume "
                    + digest
                    + " OR decline "
                    + digest
                    + "\n"
                )
                self._output.flush()
                for _ in range(self._policy.max_response_attempts):
                    try:
                        answer = (await self._readline()).decode("ascii").strip()
                    except UnicodeDecodeError:
                        answer = ""
                    if answer == "resume " + digest:
                        action = "resume"
                        break
                    if answer == "decline " + digest:
                        action = "decline"
                        break
                    self._output.write("human_assistance_response_invalid\n")
                    self._output.flush()
                else:
                    raise ValueError("human assistance exhausted its response attempts")
        except TimeoutError:
            pass
        return AssistanceDecision(request_digest=digest, action=action)
