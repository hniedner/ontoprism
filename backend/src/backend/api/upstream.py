"""FastAPI mapping for sanitized remote live-service failures."""

from fastapi import HTTPException, status

from ontolib.repositories.upstream import UpstreamFailureError

_STATUS = {
    "unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "rate-limited": status.HTTP_429_TOO_MANY_REQUESTS,
}


def upstream_http_exception(error: UpstreamFailureError) -> HTTPException:
    """Map a typed upstream failure without exposing request or response content."""
    return HTTPException(
        _STATUS[error.state],
        {
            "state": error.state,
            "service": error.service,
            "message": str(error),
        },
    )
