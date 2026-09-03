"""Warning policy shared by the Python compatibility smoke and test lane."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from typing import Literal

STARLETTE_ANYIO_ALIAS_WARNING = (
    "The anyio.abc.BlockingPortal alias is deprecated, use "
    "anyio.from_thread.BlockingPortal instead."
)
STARLETTE_ANYIO_ALIAS_MODULE = "starlette.testclient"


@dataclass(frozen=True, slots=True)
class CompatibilityWarningFilter:
    """One warning rule projected into Python and pytest configuration."""

    action: Literal["error", "ignore"]
    category: type[Warning]
    message_literal: str
    module_pattern: str

    @property
    def message_pattern(self) -> str:
        """Return the literal message as an anchored regular expression."""
        return rf"{re.escape(self.message_literal)}\Z" if self.message_literal else ""


COMPATIBILITY_WARNING_FILTERS = (
    CompatibilityWarningFilter("error", DeprecationWarning, "", ""),
    CompatibilityWarningFilter(
        "ignore",
        DeprecationWarning,
        STARLETTE_ANYIO_ALIAS_WARNING,
        rf"{re.escape(STARLETTE_ANYIO_ALIAS_MODULE)}\Z",
    ),
)


def _pytest_filter(rule: CompatibilityWarningFilter) -> str:
    return ":".join(
        (
            rule.action,
            rule.message_pattern,
            rule.category.__name__,
            rule.module_pattern,
        )
    )


PYTEST_WARNING_ARGUMENTS = (
    "--override-ini",
    "filterwarnings="
    + "\n".join(_pytest_filter(rule) for rule in COMPATIBILITY_WARNING_FILTERS),
)


def configure_compatibility_warnings() -> None:
    """Install the global deprecation error policy and its exact exception."""
    for rule in COMPATIBILITY_WARNING_FILTERS:
        warnings.filterwarnings(
            rule.action,
            message=rule.message_pattern,
            category=rule.category,
            module=rule.module_pattern,
        )
