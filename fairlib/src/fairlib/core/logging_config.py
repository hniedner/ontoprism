"""Logging helper.

A thin wrapper over the stdlib logger namespaced under ``fairlib`` so all library
logs share a root and can be configured in one place by the application.
"""

import logging

_ROOT = "fairlib"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``fairlib`` root namespace.

    ``get_logger(__name__)`` from ``fairlib.terminologies.x`` returns that logger
    directly; a non-fairlib name is nested under ``fairlib.`` so stray library logs
    are still captured by the root's configuration.
    """
    if name == _ROOT or name.startswith(f"{_ROOT}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT}.{name}")
