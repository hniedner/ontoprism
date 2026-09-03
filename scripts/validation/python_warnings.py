"""Warning policy shared by the Python compatibility smoke and test lane."""

from __future__ import annotations

import re
import warnings

STARLETTE_ANYIO_ALIAS_WARNING = (
    "The anyio.abc.BlockingPortal alias is deprecated, use "
    "anyio.from_thread.BlockingPortal instead."
)
STARLETTE_ANYIO_ALIAS_MODULE = "starlette.testclient"
_STARLETTE_ANYIO_ALIAS_MODULE_PATTERN = re.escape(STARLETTE_ANYIO_ALIAS_MODULE)
PYTEST_WARNING_ARGUMENTS = (
    "-W",
    (
        "ignore:"
        f"{STARLETTE_ANYIO_ALIAS_WARNING}:DeprecationWarning:"
        f"{STARLETTE_ANYIO_ALIAS_MODULE}"
    ),
    "-W",
    r"error::DeprecationWarning:ontolib(\.|$)",
    "-W",
    r"error::DeprecationWarning:backend(\.|$)",
    "-W",
    r"error::DeprecationWarning:scripts(\.|$)",
)


def configure_compatibility_warnings() -> None:
    """Reject deprecations except the exact observed Starlette AnyIO alias warning."""
    warnings.filterwarnings("error", category=DeprecationWarning)
    warnings.filterwarnings(
        "ignore",
        message=STARLETTE_ANYIO_ALIAS_WARNING,
        category=DeprecationWarning,
        module=_STARLETTE_ANYIO_ALIAS_MODULE_PATTERN,
    )
