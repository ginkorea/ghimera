"""Typed spend/assistance outcomes shared without importing a browser driver."""

from ghimera.human_browser_types import AssistanceObservation
from ghimera.refusals import FetchCancelled, FetchFailure, RefusalCode


class HumanCaptureFailure(FetchFailure):
    def __init__(
        self, code: RefusalCode, bytes_read: int, observations: tuple[AssistanceObservation, ...]
    ) -> None:
        self.assistance = observations
        super().__init__(code, bytes_read)


class HumanCaptureCancelled(FetchCancelled):
    def __init__(self, bytes_read: int, observations: tuple[AssistanceObservation, ...]) -> None:
        self.assistance = observations
        super().__init__(bytes_read)
