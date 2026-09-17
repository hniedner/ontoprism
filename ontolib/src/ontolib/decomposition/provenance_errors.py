"""Errors shared by provenance persistence and publication protocols."""


class RunStateError(RuntimeError):
    """A requested run/work-item transition is not currently valid."""
