"""Sanitized failure categories shared by remote live-service clients."""

from typing import Literal

from ontolib.core.exceptions import StorageError

UpstreamService = Literal["clinicaltrials", "pubmed"]
UpstreamFailureState = Literal["unavailable", "timeout", "rate-limited"]


class UpstreamFailureError(StorageError):
    """A remote-service failure safe to expose without request or response content."""

    def __init__(
        self,
        service: UpstreamService,
        state: UpstreamFailureState,
        message: str,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.state = state


class UpstreamUnavailableError(UpstreamFailureError):
    def __init__(self, service: UpstreamService, message: str) -> None:
        super().__init__(service, "unavailable", message)


class UpstreamTimeoutError(UpstreamFailureError):
    def __init__(self, service: UpstreamService, message: str) -> None:
        super().__init__(service, "timeout", message)


class UpstreamRateLimitedError(UpstreamFailureError):
    def __init__(self, service: UpstreamService, message: str) -> None:
        super().__init__(service, "rate-limited", message)
