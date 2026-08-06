"""Morphology-to-organ resolution tables used by D58/D59.

These hand-maintained tables are keyed by the resolved parent morphology emitted by
the walker, not by the concept being decomposed. They are used as tiebreakers when the
walker returns multiple site candidates.

The durable source-bound routing and scoring contracts are recorded in
``docs/DECISIONS.md`` D58/D59.
"""

# Maps resolved parent morphology code → routed organ code.
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
    "C4878": "C12468",  # Lung Carcinoma (legacy engine morphology)
    "C4917": "C12468",  # Lung Small Cell Carcinoma
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
    # BUG (deferred to post-attestation rebuild, see D-R5 and §0b item 1): C4008 is
    # "Recurrent Gallbladder Carcinoma", not Uterine Carcinosarcoma, and C12316 is
    # Corpus Uteri, not Uterus. Both tokens of this comment were wrong. No concept in
    # the M1 cohort resolves C4008 as its parent morphology, so the entry does not
    # currently fire -- but it is not SME-validated and must not be relied on.
    # Replacement key is undetermined: this table is keyed by resolved parent
    # morphology, so it requires observed walker output, not a concept code.
    # Do not "fix" the key before attestation.
    "C4008": "C12316",
    "C7558": "C12316",  # Endometrial Carcinoma → Corpus Uteri
    # --- Esophagus / GEJ composite staging site ---
    "C3513": "C203674",  # Esophageal Carcinoma → Esophagus and GEJ
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

# Organ components/localized sites within the single primary-site umbrella. These are
# not independent primary cancers and therefore use op:PrimarySubsite (Q3/Q4).
MORPHOLOGY_TO_PRIMARY_SUBSITES: dict[str, frozenset[str]] = {
    "C4878": frozenset({"C12683"}),  # Bronchus within lung-cancer umbrella
    "C4917": frozenset({"C12683"}),
    "C7558": frozenset({"C32514"}),  # Endometrial cavity within corpus uteri
    "C3513": frozenset({"C12389"}),  # Esophagus within AJCC v7 composite site
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


def primary_subsites_for_morphology(morphology_code: str | None) -> frozenset[str]:
    """Return reviewed primary-subsite fillers for one morphology context."""
    if morphology_code is None:
        return frozenset()
    return MORPHOLOGY_TO_PRIMARY_SUBSITES.get(morphology_code, frozenset())
