"""Named failures for malformed NCIt source labels."""

from __future__ import annotations

from collections.abc import Mapping


class SourceLabelError(ValueError):
    """A source entity does not have exactly one usable stated label."""


class ConceptLabelError(SourceLabelError):
    """One or more requested concepts lack one exact stated label."""

    def __init__(
        self,
        problems: Mapping[str, str],
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self.problems = dict(problems)
        self.labels = dict(labels or {})
        super().__init__(
            "; ".join(
                f"concept {code} {reason}" for code, reason in self.problems.items()
            )
        )


class GenusLabelError(SourceLabelError):
    """A named genus lacks one exact stated label."""
