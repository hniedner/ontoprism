"""Exception hierarchy for fairlib.

Names mirror fairdata's so ported modules slot in unchanged; the hierarchy is
deliberately minimal and grows only as real callers need new types.
"""


class FAIRDataError(Exception):
    """Base class for all fairlib errors."""


class StorageError(FAIRDataError):
    """A graph/SPARQL/persistence operation failed."""
