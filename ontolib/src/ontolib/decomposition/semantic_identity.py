"""Explicit engine/rules versions plus the packaged policy inputs used by runs."""

from importlib.resources import files

from ontolib.common.boundary_models import canonical_json_sha256, sha256_hex
from ontolib.decomposition.normalized_group_policy import (
    load_packaged_normalized_group_policy,
)

# Bump for output-changing engine logic; formatting/source layout is not identity.
ENGINE_VERSION = "decomposition-engine-v4"
# Bump when generic fillers, inherited core roles, or qualifier-genus rules change.
RULES_VERSION = "decomposition-rules-v1"


def routing_implementation_identity() -> str:
    """Bind deliberate versions and real policy inputs, never Python source bytes.

    The active collapse-veto policy is separately bound in RunFingerprint. The
    normalized grouping policy retains its validated policy_identity; the qualifier
    resource has no such field, so its exact packaged bytes are hashed here.
    """
    qualifier = files("ontolib.decomposition").joinpath(
        "data/morphology-qualifier-genera-26.07d.json"
    )
    return canonical_json_sha256(
        {
            "engine_version": ENGINE_VERSION,
            "rules_version": RULES_VERSION,
            "normalized_group_policy": (
                load_packaged_normalized_group_policy().policy_identity
            ),
            "morphology_qualifier_policy": sha256_hex(qualifier.read_bytes()),
        }
    )
