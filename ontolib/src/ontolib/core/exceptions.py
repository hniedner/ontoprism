"""Exception hierarchy for ontolib.

Names mirror fairdata's so ported modules slot in unchanged; the hierarchy is
deliberately minimal and grows only as real callers need new types.
"""


class FAIRDataError(Exception):
    """Base class for all ontolib errors."""


class StorageError(FAIRDataError):
    """A graph/SPARQL/persistence operation failed."""
