"""Morphology-to-organ resolution table (D23 SME-validated).

The SME established the governing principle: R101 = the named organ
(``tmp/plans/D23-site-specific-resolution-and-role-naming.md``, Part 1).
This module encodes the validated morphology→organ mapping for the concepts
in the ``ontolib/tests/decomposition/golden/`` golden set. It is used as a
tiebreaker when the walker returns multiple R101 candidates — the matching
organ is preferred over generic or data-quality-issue alternatives.

References
----------
- D23 site-specific resolution: ``tmp/plans/sme_decisions.json``
- SME workbook: ``tmp/plans/SME_Review_Workbook.xlsx``
"""

# Maps morphology code → SME-validated organ code.
# Keys are *parent* morphology codes that the walker resolves for each
# concept — these are the resolved morphology at the top of the genus chain,
# not the concept's own ``owl:intersectionOf`` morphology restriction.
MORPHOLOGY_TO_ORGAN: dict[str, str] = {
    # --- Thyroid ---
    # SME: C12400 (Thyroid Gland), NOT C75102 (Thyroid)
    "C3879": "C12400",  # Thyroid Gland Medullary Carcinoma
    "C3878": "C12400",  # Anaplastic Thyroid Carcinoma
    "C4912": "C12400",  # Thyroid Gland Papillary Carcinoma
    "C3868": "C12400",  # Thyroid Gland Follicular Carcinoma
    "C40384": "C12400",  # Thyroid Gland Carcinoma
    # --- Gastric ---
    # SME: C12391 (Stomach), NOT C13307 (Gastric — data-quality issue)
    "C2851": "C12391",  # Gastric Adenocarcinoma
    # --- Colorectal composite ---
    # C208097 walker gap: SME wants C19184 (Colon, Rectum), not C12382/C12736
    "C2955": "C19184",  # Colorectal Carcinoma
    # --- Esophageal composite ---
    # SME: C203674 (Esophagus and Gastroesophageal Junction)
    "C3889": "C203674",  # Esophageal Squamous Cell Carcinoma
    "C4911": "C203674",  # Esophageal Adenocarcinoma
    # --- Cervical ---
    "C4004": "C12311",  # Cervical Carcinoma → Cervix Uteri
    # --- Lung ---
    "C4874": "C12468",  # Non-Small Cell Lung Carcinoma → Lung
    "C4915": "C12468",  # Small Cell Lung Carcinoma
    # --- Breast ---
    "C4017": "C12971",  # Breast Carcinoma → Breast
    # --- Pancreatic ---
    "C3844": "C12393",  # Pancreatic Carcinoma → Pancreas
    # --- Gallbladder ---
    "C3860": "C12377",  # Gallbladder Carcinoma → Gallbladder
    # --- Laryngeal ---
    "C5017": "C12420",  # Laryngeal Squamous Cell Carcinoma → Larynx
    # --- Ovarian ---
    "C4908": "C12404",  # Ovarian Carcinoma → Ovary
    # --- Prostate ---
    "C4905": "C12410",  # Prostatic Carcinoma → Prostate
    # --- Testicular ---
    "C6274": "C12412",  # Testicular Non-Seminomatous Germ Cell Tumor → Testis
    # --- Vulvar ---
    "C4223": "C12408",  # Vulvar Carcinoma → Vulva
    # --- Oral Cavity ---
    "C5980": "C12421",  # Oral Cavity Squamous Cell Carcinoma → Oral Cavity
    # --- Uterine ---
    "C4008": "C12316",  # Uterine Carcinosarcoma → Uterus
    # --- Bone ---
    "C3711": "C12366",  # Osteosarcoma → Bone
    # --- Lip ---
    "C4021": "C12470",  # Basal Cell Carcinoma → Lip
    # --- Fallopian Tube ---
    "C3843": "C12403",  # Fallopian Tube Carcinoma → Fallopian Tube
    # --- Hypopharyngeal ---
    "C4035": "C12246",  # Hypopharyngeal Carcinoma → Hypopharynx
    # --- Small Intestine ---
    "C3734": "C12386",  # Small Intestine Adenocarcinoma → Small Intestine
    # --- Urethral ---
    "C3834": "C12417",  # Urethral Carcinoma → Urethra
}


def organ_for_morphology(morphology_code: str | None) -> str | None:
    """Return the SME-validated organ code for *morphology_code*, or ``None``.

    Parameters
    ----------
    morphology_code :
        The resolved parent-morphology code (found at the top of the genus
        chain by the walker).

    Returns
    -------
    The organ code from ``MORPHOLOGY_TO_ORGAN``, or ``None`` if no mapping
    exists for this morphology.
    """
    if morphology_code is None:
        return None
    return MORPHOLOGY_TO_ORGAN.get(morphology_code)
