"""Logging helper.

A thin wrapper over the stdlib logger namespaced under ``ontolib`` so all library
logs share a root and can be configured in one place by the application.
"""

import logging

_ROOT = "ontolib"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``ontolib`` root namespace.

    ``get_logger(__name__)`` from ``ontolib.terminologies.x`` returns that logger
    directly; a non-ontolib name is nested under ``ontolib.`` so stray library logs
    are still captured by the root's configuration.
    """
    if name == _ROOT or name.startswith(f"{_ROOT}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT}.{name}")
