"""Univocal OntoPrism axis contracts and NCIt source-role routing."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class StableGovernance(_StrictModel):
    status: Literal["stable"] = "stable"


class ProvisionalGovernance(_StrictModel):
    status: Literal["provisional"] = "provisional"
    since: date
    review_by: date
    review_trigger: str = Field(min_length=1)
    fallback_axis: str = Field(min_length=1)
    fallback_needs_review: bool = False
    evidence_count: int = Field(ge=1)


AxisGovernance = Annotated[
    StableGovernance | ProvisionalGovernance,
    Field(discriminator="status"),
]
AxisModality = Literal["asserted", "non-defining"]


class AxisContract(_StrictModel):
    """Human- and machine-readable contract for one normalized relation."""

    axis: str = Field(pattern=r"^op:[A-Za-z][A-Za-z0-9]*$")
    label: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    domain_code: str = Field(pattern=r"^C[0-9]+$")
    domain_label: str = Field(min_length=1)
    range_code: str = Field(pattern=r"^C[0-9]+$")
    range_label: str = Field(min_length=1)
    source_roles: tuple[str, ...] = ()
    provenance: tuple[str, ...] = Field(min_length=1)
    ro_parent: str | None = Field(default=None, pattern=r"^RO:[0-9]{7}$")
    governance: AxisGovernance = Field(default_factory=StableGovernance)
    modality: AxisModality = "asserted"


_DISEASE = ("C7057", "Disease, Disorder or Finding")
_ANATOMY = ("C12219", "Anatomic Structure, System, or Substance")
_CELL = ("C12508", "Cell")
_ABNORMAL_CELL = ("C12913", "Abnormal Cell")
_MOLECULAR = ("C3910", "Molecular Abnormality")
_ATTRIBUTE = ("C20189", "Property or Attribute")
_PROVENANCE = (
    "ONTOPRISM D22 univocal-relation policy",
    "ONTOPRISM D23 SME axis decisions",
    "NCIt concepts used as relation endpoint classes",
)


def _contract(
    axis: str,
    label: str,
    definition: str,
    endpoint_range: tuple[str, str],
    *source_roles: str,
    ro_parent: str | None = None,
    governance: AxisGovernance | None = None,
    modality: AxisModality = "asserted",
) -> AxisContract:
    return AxisContract(
        axis=axis,
        label=label,
        definition=definition,
        domain_code=_DISEASE[0],
        domain_label=_DISEASE[1],
        range_code=endpoint_range[0],
        range_label=endpoint_range[1],
        source_roles=source_roles,
        provenance=_PROVENANCE,
        ro_parent=ro_parent,
        governance=governance or StableGovernance(),
        modality=modality,
    )


_CONTRACT_SEQUENCE = (
    _contract(
        "op:PrimarySite",
        "primary site",
        "Relates a disease to the organ where its pathological process originated.",
        _ANATOMY,
        "R101",
        ro_parent="RO:0004026",
    ),
    _contract(
        "op:PrimarySubsite",
        "primary subsite",
        "Relates a disease to an organ component or localized site within its single "
        "primary-site umbrella; it does not assert an independent primary cancer.",
        _ANATOMY,
        "R101",
        ro_parent="RO:0004026",
        governance=ProvisionalGovernance(
            since=date(2026, 8, 3),
            review_by=date(2027, 8, 3),
            review_trigger="RO submission outcome or NCIt 27.x",
            fallback_axis="op:AssociatedRegion",
            evidence_count=3,
        ),
    ),
    _contract(
        "op:MetastaticSite",
        "metastatic site",
        "Relates a disease to an anatomic site containing a metastatic lesion.",
        _ANATOMY,
        "R102",
    ),
    _contract(
        "op:AssociatedSite",
        "associated site",
        "Relates a disease to a non-primary, non-metastatic associated anatomy.",
        _ANATOMY,
        "R100",
    ),
    _contract(
        "op:AssociatedRegion",
        "associated region",
        "Relates a disease to a region or tissue associated with its primary organ.",
        _ANATOMY,
        "R101",
    ),
    _contract(
        "op:AssociatedLineageClassification",
        "associated lineage classification",
        "Relates a disease to anatomy used by NCIt to classify tumor lineage, "
        "not site.",
        _ANATOMY,
        "R101",
        governance=ProvisionalGovernance(
            since=date(2026, 8, 3),
            review_by=date(2027, 8, 3),
            review_trigger="RO submission outcome or NCIt 27.x",
            fallback_axis="R101",
            fallback_needs_review=True,
            evidence_count=3,
        ),
    ),
    _contract(
        "op:NormalTissueOrigin",
        "normal tissue origin",
        "Relates a disease to the normal tissue in which the process begins.",
        _ANATOMY,
        "R103",
        modality="non-defining",
    ),
    _contract(
        "op:CellOrigin",
        "normal cell origin",
        "Relates a disease to the normal cell type in which the process begins.",
        _CELL,
        "R104",
    ),
    _contract(
        "op:CellType",
        "abnormal cell type",
        "Relates a disease to its characteristic neoplastic or abnormal cell type.",
        _ABNORMAL_CELL,
        "R105",
    ),
    _contract(
        "op:MolecularAbnormality",
        "molecular abnormality",
        "Relates a disease to a defining molecular abnormality present in it.",
        _MOLECULAR,
        "R106",
    ),
    _contract(
        "op:CytogeneticAbnormality",
        "cytogenetic abnormality",
        "Relates a disease to a defining chromosomal abnormality present in it.",
        _MOLECULAR,
        "R107",
    ),
    _contract(
        "op:ClinicalFinding",
        "clinical finding",
        "Relates a disease to a defining observation, sign, or symptom.",
        _DISEASE,
        "R108",
    ),
    _contract(
        "op:StageValue",
        "stage value",
        "Relates a disease to the extent-of-spread stage assigned to it.",
        _ATTRIBUTE,
        "R88",
    ),
    _contract(
        "op:StageSystem",
        "stage system",
        "Relates a disease to the staging manual or classification system in use.",
        _ATTRIBUTE,
        "R88",
    ),
    _contract(
        "op:Grade",
        "grade",
        "Relates a disease to a histopathologic grading value or system.",
        _ATTRIBUTE,
        "R110",
    ),
    _contract(
        "op:Morphology",
        "morphology",
        "Relates a disease to the morphology represented by its taxonomic genus.",
        _DISEASE,
    ),
    _contract(
        "op:Laterality",
        "laterality",
        "Relates a disease to a left, right, or bilateral qualifier.",
        _ATTRIBUTE,
    ),
    _contract(
        "op:WithFinding",
        "finding qualifier",
        "Relates a disease label to an explicitly present or absent finding qualifier.",
        _DISEASE,
    ),
    _contract(
        "op:AssociatedPriorDisease",
        "associated prior disease",
        "Relates a disease to a distinct disease previously present in the same "
        "patient, without by itself asserting causation or material transformation.",
        _DISEASE,
        "R126",
        governance=ProvisionalGovernance(
            since=date(2026, 8, 3),
            review_by=date(2027, 8, 3),
            review_trigger="RO submission outcome or NCIt 27.x",
            fallback_axis="R126",
            fallback_needs_review=True,
            evidence_count=1,
        ),
    ),
)

AXIS_CONTRACTS = {contract.axis: contract for contract in _CONTRACT_SEQUENCE}
_SOURCE_ROLE_TO_AXIS = {
    contract.source_roles[0]: contract.axis
    for contract in _CONTRACT_SEQUENCE
    if len(contract.source_roles) == 1
    and contract.axis
    not in {
        "op:AssociatedRegion",
        "op:AssociatedLineageClassification",
        "op:PrimarySubsite",
        "op:AssociatedPriorDisease",
        "op:StageSystem",
    }
}


def normalized_axis_for_role(role_code: str) -> str | None:
    """Return the direct normalized axis for a defining NCIt source role."""
    return _SOURCE_ROLE_TO_AXIS.get(role_code)
