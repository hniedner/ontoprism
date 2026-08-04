"""Generate the M1 v14 cancer-stage semantic-bundle review candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ontolib.decomposition.semantic_bundles import (
    SemanticBundle,
    SemanticBundleMember,
    SemanticBundleRule,
    SourceFactReference,
    generate_semantic_bundles,
)

if TYPE_CHECKING:
    from ontolib.decomposition.score import Constituent

_NCIT_RELEASE = "26.07d"
_WORKBOOK_SHA256 = "a538de0772df786da39f0eaeb9c374e837b71e58b567d6f045f126225e759cc8"
_SOURCE_AUDIT_SHA256 = (
    "aec3910bc5a72c5132c996c52861534c615912f5001928b1cd374f7d566fadfb"
)
_ENGINE_EVIDENCE_SHA256 = (
    "42e33238c7b18985263f780a165ad42d1230bb620a2aac8edf11748cf661f74f"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceSource:
    evidence_id: str
    title: str
    uri: str
    access: str
    supports: str


EVIDENCE_SOURCES = (
    EvidenceSource(
        evidence_id="ncit-26.07d-role-audit",
        title="M1 #57 complete contracted-role audit for NCIt 26.07d",
        uri="M1-57_Complete_Role_Audit_2607d_v1.json",
        access="local hash-pinned evidence",
        supports="Exact R88 source occurrences, anchors, depths, and canonical groups.",
    ),
    EvidenceSource(
        evidence_id="mcode-4.0.0-cancer-stage",
        title="mCODE 4.0.0 Cancer Stage Profile",
        uri=(
            "https://hl7.org/fhir/us/mcode/STU4/"
            "StructureDefinition-mcode-cancer-stage.html"
        ),
        access="public",
        supports=(
            "A cancer-stage observation has a required stage type, optional method, "
            "and at most one stage value."
        ),
    ),
    EvidenceSource(
        evidence_id="mcode-4.0.0-stage-type",
        title="mCODE 4.0.0 Cancer Stage Type Value Set",
        uri=(
            "https://hl7.org/fhir/us/mcode/STU4/"
            "ValueSet-mcode-cancer-stage-type-vs.html"
        ),
        access="public; terminology licenses still apply",
        supports=(
            "NCIt AJCC v6-v9 and FIGO 2009/2018 stage-type codes used by this pilot."
        ),
    ),
    EvidenceSource(
        evidence_id="mcode-4.0.0-stage-method",
        title="mCODE 4.0.0 Cancer Staging Method Value Set",
        uri=(
            "https://hl7.org/fhir/us/mcode/STU4/"
            "ValueSet-mcode-cancer-staging-method-vs.html"
        ),
        access="public; terminology licenses still apply",
        supports="NCIt C141685 as the VALG staging method.",
    ),
    EvidenceSource(
        evidence_id="ajcc-official-staging-system",
        title="AJCC Cancer Staging System",
        uri=(
            "https://www.facs.org/quality-programs/cancer-programs/"
            "american-joint-committee-on-cancer/version-9/"
        ),
        access="licensed/restricted AJCC content",
        supports=(
            "AJCC authority, edition/version distinction, and the cervical Version 9 "
            "protocol. No restricted staging tables are reproduced."
        ),
    ),
    EvidenceSource(
        evidence_id="figo-cervix-2009",
        title="Revised FIGO staging for carcinoma of the cervix",
        uri="https://doi.org/10.1016/j.ijgo.2009.02.009",
        access="publisher terms apply",
        supports="The 2009 FIGO cervical staging revision.",
    ),
    EvidenceSource(
        evidence_id="figo-cervix-2018",
        title="Revised FIGO staging for carcinoma of the cervix uteri",
        uri="https://doi.org/10.1002/ijgo.12749",
        access="publisher terms apply",
        supports="The 2018 FIGO cervical staging revision.",
    ),
    EvidenceSource(
        evidence_id="figo-endometrial-2023",
        title="FIGO staging of endometrial cancer: 2023",
        uri="https://doi.org/10.1002/ijgo.14923",
        access="publisher terms apply",
        supports="The 2023 FIGO endometrial staging revision.",
    ),
    EvidenceSource(
        evidence_id="nci-pdq-sclc",
        title="Small Cell Lung Cancer Treatment (PDQ), Health Professional Version",
        uri="https://www.cancer.gov/types/lung/hp/small-cell-lung-treatment-pdq",
        access="public",
        supports=(
            "VALG as an SCLC staging system and the limited/extensive-stage "
            "distinction."
        ),
    ),
)


def _source_member(
    role: str,
    filler_code: str,
    *,
    root_code: str,
    anchor_code: str,
    depth: int,
    group_id: str,
) -> SemanticBundleMember:
    axis = "op:StageValue" if role == "stage-value" else "op:StageSystem"
    return SemanticBundleMember(
        role=role,
        axis=axis,
        filler_code=filler_code,
        source_facts=(
            SourceFactReference(
                ncit_release=_NCIT_RELEASE,
                root_code=root_code,
                role_code="R88",
                filler_code=filler_code,
                anchor_code=anchor_code,
                depth=depth,
                source_group_id=group_id,
            ),
        ),
    )


def _external_method(
    filler_code: str, evidence_ids: tuple[str, ...]
) -> SemanticBundleMember:
    return SemanticBundleMember(
        role="staging-method",
        axis="op:StageSystem",
        filler_code=filler_code,
        source_facts=(),
        evidence_ids=evidence_ids,
    )


def _rule(
    rule_id: str,
    subject_code: str,
    name: str,
    system: SemanticBundleMember,
    value: SemanticBundleMember,
    *,
    authority: str,
    version: str,
    evidence_ids: tuple[str, ...],
) -> SemanticBundleRule:
    return SemanticBundleRule(
        rule_id=rule_id,
        subject_code=subject_code,
        kind="cancer-stage-classification",
        name=name,
        members=(system, value),
        qualifiers=(("authority", authority), ("version", version)),
        evidence_ids=evidence_ids,
    )


_AUDIT = "ncit-26.07d-role-audit"
_MCODE = "mcode-4.0.0-cancer-stage"
_MCODE_TYPE = "mcode-4.0.0-stage-type"
_AJCC = "ajcc-official-staging-system"

_C115057_VALUE = _source_member(
    "stage-value",
    "C27966",
    root_code="C115057",
    anchor_code="C8033",
    depth=1,
    group_id="aae21beea65bfffe56c6588e65262894bf6edcf8f9f2bf47eac6b70f2cac3726",
)
_C27787_VALUE = _source_member(
    "stage-value",
    "C27970",
    root_code="C27787",
    anchor_code="C9074",
    depth=1,
    group_id="73f53459b912b33478b591c3ac93958b7f413cb5f39cf6e11e7102eef18309ac",
)

STAGE_BUNDLE_RULES = (
    _rule(
        "stage-c115057-ajcc-v6",
        "C115057",
        "AJCC v6 Stage I lip and oral cavity squamous cell carcinoma",
        _source_member(
            "stage-type",
            "C90529",
            root_code="C115057",
            anchor_code="C132736",
            depth=2,
            group_id=(
                "247877db9643ab31d55755ee76369cdcac320a5573ca31b3b7971f53998ad454"
            ),
        ),
        _C115057_VALUE,
        authority="AJCC",
        version="6",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c115057-ajcc-v7",
        "C115057",
        "AJCC v7 Stage I lip and oral cavity squamous cell carcinoma",
        _source_member(
            "stage-type",
            "C90530",
            root_code="C115057",
            anchor_code="C132736",
            depth=2,
            group_id=(
                "247877db9643ab31d55755ee76369cdcac320a5573ca31b3b7971f53998ad454"
            ),
        ),
        _C115057_VALUE,
        authority="AJCC",
        version="7",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c101539-ajcc-v7",
        "C101539",
        "AJCC v7 Stage I differentiated thyroid carcinoma under 45 years",
        _source_member(
            "stage-type",
            "C140961",
            root_code="C101539",
            anchor_code="C101539",
            depth=0,
            group_id=(
                "2c871f775d0fad5572b10f0df1f62aa9be26d04a5f023de0f529105184690cce"
            ),
        ),
        _source_member(
            "stage-value",
            "C27966",
            root_code="C101539",
            anchor_code="C87543",
            depth=1,
            group_id=(
                "4e04d8d24287f1200e60bc5fb443fd569b06864d083e25299398e2ab6914e66c"
            ),
        ),
        authority="AJCC",
        version="7",
        evidence_ids=(_AUDIT, _MCODE, _AJCC),
    ),
    _rule(
        "stage-c132677-ajcc-v8",
        "C132677",
        "AJCC v8 Stage III unknown primary tumor with metastatic cervical adenopathy",
        _source_member(
            "stage-type",
            "C132248",
            root_code="C132677",
            anchor_code="C132676",
            depth=1,
            group_id=(
                "b1b92d49c5fb310194984326ad672d6ecd31068739420acafaeb2dfc5cad3802"
            ),
        ),
        _source_member(
            "stage-value",
            "C27970",
            root_code="C132677",
            anchor_code="C132677",
            depth=0,
            group_id=(
                "c4ba770a80dc43b78a3b9f7a66b78e3d34eacaed1f6d7ab879870a6b63be5bd5"
            ),
        ),
        authority="AJCC",
        version="8",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c181564-ajcc-v9",
        "C181564",
        "AJCC v9 Stage I cervical cancer",
        _source_member(
            "stage-type",
            "C180901",
            root_code="C181564",
            anchor_code="C181562",
            depth=1,
            group_id=(
                "7f10ca575c6631d1d39f940924f7cdfd555c0df4aee153b54e17f8605e28f754"
            ),
        ),
        _source_member(
            "stage-value",
            "C27966",
            root_code="C181564",
            anchor_code="C181564",
            depth=0,
            group_id=(
                "2295ee2a6aa7b2f09a0c61ad1feb622edf8ad54439a9459a4b4c8dcc1dd27cb8"
            ),
        ),
        authority="AJCC",
        version="9",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c186620-figo-2009",
        "C186620",
        "FIGO 2009 Stage I cervical cancer",
        _source_member(
            "stage-type",
            "C186618",
            root_code="C186620",
            anchor_code="C186619",
            depth=1,
            group_id=(
                "b7b9600ed1d8ea5b0ca11c21b03d4189a4223293763787bcad7a92fb9fee4e0c"
            ),
        ),
        _source_member(
            "stage-value",
            "C27966",
            root_code="C186620",
            anchor_code="C186620",
            depth=0,
            group_id=(
                "63391dd6710a39cd789c2bca0600a15bb9bd9bf6dd729bab957c458d95e1f29a"
            ),
        ),
        authority="FIGO",
        version="2009",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, "figo-cervix-2009"),
    ),
    _rule(
        "stage-c162226-figo-2018",
        "C162226",
        "FIGO 2018 Stage I cervical cancer",
        _source_member(
            "stage-type",
            "C186617",
            root_code="C162226",
            anchor_code="C162225",
            depth=1,
            group_id=(
                "d5ab55bccdef1e0cecf9d5ef3a1a2bf588319abb319c373589b5221b37b5885a"
            ),
        ),
        _source_member(
            "stage-value",
            "C96244",
            root_code="C162226",
            anchor_code="C162226",
            depth=0,
            group_id=(
                "5a495fb01b3c8576e7b951f2a9de8a11c8bd58d854a04ae92120fac5f11cd9a2"
            ),
        ),
        authority="FIGO",
        version="2018",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, "figo-cervix-2018"),
    ),
    _rule(
        "stage-c206219-figo-2023",
        "C206219",
        "FIGO 2023 Stage I endometrial cancer",
        _source_member(
            "stage-type",
            "C206211",
            root_code="C206219",
            anchor_code="C206217",
            depth=1,
            group_id=(
                "24a061ad6be30f67c8ab0fdbd73987f4baf0be8c8805b52a755e6a77495a184b"
            ),
        ),
        _source_member(
            "stage-value",
            "C96244",
            root_code="C206219",
            anchor_code="C206219",
            depth=0,
            group_id=(
                "558cfd1c54a0ec108027aee0739c6f50150af2fceaa017781ebe207f8244eb75"
            ),
        ),
        authority="FIGO",
        version="2023",
        evidence_ids=(_AUDIT, _MCODE, "figo-endometrial-2023"),
    ),
    _rule(
        "stage-c6135-ajcc-v7",
        "C6135",
        "AJCC v7 Stage III thyroid gland medullary carcinoma",
        _source_member(
            "stage-type",
            "C90530",
            root_code="C6135",
            anchor_code="C141041",
            depth=1,
            group_id=(
                "6f9a00ec0975f63a9213951a287752ee720de86ae997c95f5c0655d1e8551c45"
            ),
        ),
        _source_member(
            "stage-value",
            "C27970",
            root_code="C6135",
            anchor_code="C6135",
            depth=0,
            group_id=(
                "71b0af99dd55fb44e10b66a6b17e6efa926455cf2b4ff7788680968fa28c5723"
            ),
        ),
        authority="AJCC",
        version="7",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c35756-ajcc-v7",
        "C35756",
        "AJCC v7 Stage IIIB lung small cell carcinoma with pleural effusion",
        _source_member(
            "stage-type",
            "C90530",
            root_code="C35756",
            anchor_code="C91232",
            depth=4,
            group_id=(
                "a1aaa29d4b372c7e7138d244e3ebcb6637f9b86926dfa0486140c67ddcd0e3e7"
            ),
        ),
        _source_member(
            "stage-value",
            "C27978",
            root_code="C35756",
            anchor_code="C5647",
            depth=2,
            group_id=(
                "dd345e6d5490e8217b1453e6549a6de96c6a41d3c3851cc67b4606f224d036dd"
            ),
        ),
        authority="AJCC",
        version="7",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c35756-valg-extensive",
        "C35756",
        "VALG extensive-stage lung small cell carcinoma with pleural effusion",
        _external_method(
            "C141685",
            ("mcode-4.0.0-stage-method", "nci-pdq-sclc"),
        ),
        _source_member(
            "stage-value",
            "C28064",
            root_code="C35756",
            anchor_code="C9049",
            depth=1,
            group_id=(
                "5a86123986099e1e3e3223f922abfc601887daeb2f2b5046f9589d477300d6e0"
            ),
        ),
        authority="VALG",
        version="limited-extensive",
        evidence_ids=(
            _AUDIT,
            _MCODE,
            "mcode-4.0.0-stage-method",
            "nci-pdq-sclc",
        ),
    ),
    _rule(
        "stage-c89995-ajcc-v7",
        "C89995",
        "AJCC v7 Stage III colon cancer",
        _source_member(
            "stage-type",
            "C90530",
            root_code="C89995",
            anchor_code="C91223",
            depth=2,
            group_id=(
                "f6b9daa0a913a0de40a4ae40520bf4fbda77b8a5c6ff26892513aeb79eaac7ac"
            ),
        ),
        _source_member(
            "stage-value",
            "C27970",
            root_code="C89995",
            anchor_code="C89994",
            depth=1,
            group_id=(
                "363cefec5569b1888778f6d9d210df0acc474b68c5038322c61119fea994ea47"
            ),
        ),
        authority="AJCC",
        version="7",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c27787-ajcc-v6",
        "C27787",
        "AJCC v6 Stage III testicular non-seminomatous germ cell tumor",
        _source_member(
            "stage-type",
            "C90529",
            root_code="C27787",
            anchor_code="C140241",
            depth=2,
            group_id=(
                "0214f14693f059aae2d41b1b8426552f57dabe5cb2633ba455f21a52a5c4216f"
            ),
        ),
        _C27787_VALUE,
        authority="AJCC",
        version="6",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c27787-ajcc-v7",
        "C27787",
        "AJCC v7 Stage III testicular non-seminomatous germ cell tumor",
        _source_member(
            "stage-type",
            "C90530",
            root_code="C27787",
            anchor_code="C140241",
            depth=2,
            group_id=(
                "0214f14693f059aae2d41b1b8426552f57dabe5cb2633ba455f21a52a5c4216f"
            ),
        ),
        _C27787_VALUE,
        authority="AJCC",
        version="7",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
    _rule(
        "stage-c115118-ajcc-v7",
        "C115118",
        "AJCC v7 Stage IB esophageal cancer",
        _source_member(
            "stage-type",
            "C90530",
            root_code="C115118",
            anchor_code="C91221",
            depth=2,
            group_id=(
                "9456e4903fb8cdf39f626ddd74a95be4b269ee7e379156c29099aa8c671aacaa"
            ),
        ),
        _source_member(
            "stage-value",
            "C27976",
            root_code="C115118",
            anchor_code="C115118",
            depth=0,
            group_id=(
                "95549abf4674043e4470fd7002ab0fcdc5030b5786eb5080122a75670f5f4869"
            ),
        ),
        authority="AJCC",
        version="7",
        evidence_ids=(_AUDIT, _MCODE, _MCODE_TYPE, _AJCC),
    ),
)

_RULE_SOURCE_VALUE_GROUP = {
    "stage-c115057-ajcc-v6": "stage-ajcc-v6-and-v7",
    "stage-c115057-ajcc-v7": "stage-ajcc-v6-and-v7",
    "stage-c101539-ajcc-v7": "stage-ajcc-v7-dtc-under45",
    "stage-c132677-ajcc-v8": "stage-ajcc-v8",
    "stage-c181564-ajcc-v9": "stage-ajcc-v9",
    "stage-c186620-figo-2009": "stage-figo-2009",
    "stage-c162226-figo-2018": "stage-figo-2018",
    "stage-c206219-figo-2023": "stage-figo-2023",
    "stage-c6135-ajcc-v7": "stage-ajcc-v7",
    "stage-c35756-ajcc-v7": "stage-ajcc-v7",
    "stage-c35756-valg-extensive": "stage-sclc-extensive-vs-limited",
    "stage-c89995-ajcc-v7": "stage-ajcc-v7",
    "stage-c27787-ajcc-v6": "stage-ajcc-v6-and-v7",
    "stage-c27787-ajcc-v7": "stage-ajcc-v6-and-v7",
    "stage-c115118-ajcc-v7": "stage-ajcc-v7",
}

_CONTEXT_ONLY = {
    "subject_code": "C198031",
    "reason": (
        "C198023 identifies classification context but no stage value is asserted."
    ),
    "member": {
        "role": "classification-context",
        "axis": "op:StageSystem",
        "filler_code": "C198023",
        "source_facts": [
            {
                "ncit_release": _NCIT_RELEASE,
                "root_code": "C198031",
                "role_code": "R88",
                "filler_code": "C198023",
                "anchor_code": "C198031",
                "depth": 0,
                "source_group_id": (
                    "0d414f8ad31ecc05baa4617d99f8aa622c9c1a684f55f49120ffe79e78b594cf"
                ),
            }
        ],
    },
}


def _source_fact_dict(fact: SourceFactReference) -> dict[str, object]:
    return {
        "ncit_release": fact.ncit_release,
        "root_code": fact.root_code,
        "role_code": fact.role_code,
        "filler_code": fact.filler_code,
        "anchor_code": fact.anchor_code,
        "depth": fact.depth,
        "source_group_id": fact.source_group_id,
    }


def _member_dict(member: SemanticBundleMember) -> dict[str, object]:
    return {
        "role": member.role,
        "axis": member.axis,
        "filler_code": member.filler_code,
        "source_facts": [_source_fact_dict(fact) for fact in member.source_facts],
        "external_evidence_ids": list(member.evidence_ids),
    }


def _rule_dict(rule: SemanticBundleRule) -> dict[str, object]:
    bundle = SemanticBundle.from_rule(rule)
    return {
        "rule_id": rule.rule_id,
        "semantic_identity": bundle.identity,
        "subject_code": rule.subject_code,
        "kind": rule.kind,
        "name": rule.name,
        "source_value_group": _RULE_SOURCE_VALUE_GROUP[rule.rule_id],
        "qualifiers": dict(rule.qualifiers),
        "members": [_member_dict(member) for member in rule.members],
        "evidence_ids": list(rule.evidence_ids),
    }


def _source_value_groups() -> list[dict[str, object]]:
    grouped: defaultdict[tuple[str, str], list[SemanticBundleRule]] = defaultdict(list)
    for rule in STAGE_BUNDLE_RULES:
        grouped[(rule.subject_code, _RULE_SOURCE_VALUE_GROUP[rule.rule_id])].append(
            rule
        )
    result: list[dict[str, object]] = []
    for (subject_code, group_id), rules in grouped.items():
        members = {
            member.semantic_key: member for rule in rules for member in rule.members
        }
        result.append(
            {
                "subject_code": subject_code,
                "source_value_group": group_id,
                "members": [
                    _member_dict(members[key]) for key in sorted(members.keys())
                ],
                "semantic_rule_ids": sorted(rule.rule_id for rule in rules),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            str(item["subject_code"]),
            str(item["source_value_group"]),
        ),
    )


def _audit_fact_key(value: object) -> tuple[str, str, str, str, int, str]:
    if not isinstance(value, dict):
        raise ValueError("source audit facts must be objects")
    fields = (
        value.get("root_code"),
        value.get("role_code"),
        value.get("filler_code"),
        value.get("anchor_code"),
        value.get("depth"),
        value.get("group_id"),
    )
    if (
        not all(isinstance(item, str) for item in fields[:4])
        or not isinstance(fields[4], int)
        or not isinstance(fields[5], str)
    ):
        raise ValueError("source audit fact has an invalid shape")
    return cast("tuple[str, str, str, str, int, str]", fields)


def _reference_key(fact: SourceFactReference) -> tuple[str, str, str, str, int, str]:
    return (
        fact.root_code,
        fact.role_code,
        fact.filler_code,
        fact.anchor_code,
        fact.depth,
        fact.source_group_id,
    )


def validate_source_audit(raw_audit: object) -> None:
    """Require every claimed NCIt occurrence to exist in the bound source audit."""
    if (
        not isinstance(raw_audit, dict)
        or raw_audit.get("ncit_release") != _NCIT_RELEASE
    ):
        raise ValueError("source audit NCIt release does not match the registry")
    raw_facts = raw_audit.get("facts")
    if not isinstance(raw_facts, list):
        raise ValueError("source audit facts must be a list")
    audited = {_audit_fact_key(fact) for fact in raw_facts}
    referenced = {
        _reference_key(fact)
        for rule in STAGE_BUNDLE_RULES
        for member in rule.members
        for fact in member.source_facts
    }
    if missing := referenced - audited:
        raise ValueError(f"missing semantic-bundle source fact: {min(missing)!r}")


def _not_evaluable(reason: str) -> dict[str, str]:
    return {"status": "not-evaluable", "reason": reason}


def build_stage_bundle_report(
    engine_pairs_by_code: dict[str, set[Constituent]],
) -> dict[str, object]:
    """Report availability without inventing actual bundle associations."""
    rules_by_subject: defaultdict[str, list[SemanticBundleRule]] = defaultdict(list)
    for rule in STAGE_BUNDLE_RULES:
        rules_by_subject[rule.subject_code].append(rule)

    rule_results: list[dict[str, object]] = []
    satisfied = 0
    present_members = 0
    for subject_code, rules in rules_by_subject.items():
        pairs = engine_pairs_by_code.get(subject_code, set())
        generated = generate_semantic_bundles(subject_code, pairs, tuple(rules))
        complete_ids = {bundle.rule_id for bundle in generated.bundles}
        incomplete_by_id = {item.rule_id: item for item in generated.incomplete}
        satisfied += len(generated.bundles)
        for rule in rules:
            present = tuple(member for member in rule.members if member.pair in pairs)
            present_members += len(present)
            missing = (
                ()
                if rule.rule_id in complete_ids
                else incomplete_by_id[rule.rule_id].missing_members
            )
            rule_results.append(
                {
                    "rule_id": rule.rule_id,
                    "name": rule.name,
                    "semantic_identity": SemanticBundle.from_rule(rule).identity,
                    "status": "satisfied" if not missing else "incomplete",
                    "present_members": [_member_dict(member) for member in present],
                    "missing_members": [_member_dict(member) for member in missing],
                }
            )

    expected_bundles = len(STAGE_BUNDLE_RULES)
    expected_members = sum(len(rule.members) for rule in STAGE_BUNDLE_RULES)
    unavailable_reason = (
        "Engine evidence contains flat axis/filler pairs but no semantic bundle or "
        "within-bundle association identity; expected rules must not be projected back "
        "into actual output."
    )
    return {
        "schema_version": 1,
        "status": "REVIEW-CANDIDATE-NOT-ATTESTED",
        "scope": {
            "family": "cancer-stage-classification",
            "source_value_groups": len(_source_value_groups()),
            "semantic_bundles": expected_bundles,
            "excluded_context_only_subjects": ["C198031"],
        },
        "evidence_sources": [
            {
                "evidence_id": source.evidence_id,
                "title": source.title,
                "uri": source.uri,
                "access": source.access,
                "supports": source.supports,
            }
            for source in EVIDENCE_SOURCES
        ],
        "source_value_groups": _source_value_groups(),
        "semantic_bundle_rules": [_rule_dict(rule) for rule in STAGE_BUNDLE_RULES],
        "excluded_context_only_constructs": [_CONTEXT_ONLY],
        "engine_rule_satisfaction": {
            "interpretation": (
                "Recall-only diagnostic: all rule member pairs occur in flat engine "
                "output. It is not evidence that the engine associated those members."
            ),
            "bundles": {
                "expected": expected_bundles,
                "satisfied": satisfied,
                "incomplete": expected_bundles - satisfied,
                "recall": satisfied / expected_bundles,
            },
            "member_occurrences": {
                "expected": expected_members,
                "present": present_members,
                "missing": expected_members - present_members,
                "recall": present_members / expected_members,
            },
            "semantic_scores": {
                "exact_bundle": _not_evaluable(unavailable_reason),
                "contextual_member": _not_evaluable(unavailable_reason),
                "association": _not_evaluable(unavailable_reason),
            },
            "rules": rule_results,
        },
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_identity(path: Path, expected: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(
            f"{path.name} SHA-256 mismatch: expected {expected}, got {actual}"
        )


def _read_json(path: Path) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicates,
    )


def _constituent_pairs(code: str, raw_constituents: object) -> set[Constituent]:
    if not isinstance(raw_constituents, list):
        raise ValueError(f"{code} engine constituents must be a list")
    pairs: set[Constituent] = set()
    for constituent in raw_constituents:
        if not isinstance(constituent, dict):
            raise ValueError(f"{code} engine constituent has an invalid shape")
        axis, filler = constituent.get("axis"), constituent.get("filler")
        if not isinstance(axis, str) or not isinstance(filler, str):
            raise ValueError(f"{code} engine constituent has an invalid pair")
        pair = (axis, filler)
        if pair in pairs:
            raise ValueError(f"{code} engine constituent pairs must be unique")
        pairs.add(pair)
    return pairs


def _engine_pairs(raw_engine: object) -> dict[str, set[Constituent]]:
    if not isinstance(raw_engine, dict) or raw_engine.get("schema_version") != 1:
        raise ValueError("engine evidence schema version must be 1")
    if raw_engine.get("ncit_version") != _NCIT_RELEASE:
        raise ValueError("engine evidence NCIt release does not match the registry")
    concepts = raw_engine.get("concepts")
    if not isinstance(concepts, list):
        raise ValueError("engine evidence concepts must be a list")
    result: dict[str, set[Constituent]] = {}
    for concept in concepts:
        if not isinstance(concept, dict) or not isinstance(concept.get("code"), str):
            raise ValueError("engine evidence concept has an invalid shape")
        code = cast("str", concept["code"])
        if code in result:
            raise ValueError(f"duplicate engine concept: {code}")
        result[code] = _constituent_pairs(code, concept.get("constituents"))
    return result


def _payload_identity(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def generate_stage_bundle_artifact(
    workbook_path: Path,
    source_audit_path: Path,
    engine_evidence_path: Path,
) -> dict[str, object]:
    """Generate a hash-bound report from the v13 constituent and engine evidence."""
    _require_identity(workbook_path, _WORKBOOK_SHA256)
    _require_identity(source_audit_path, _SOURCE_AUDIT_SHA256)
    _require_identity(engine_evidence_path, _ENGINE_EVIDENCE_SHA256)
    raw_audit = _read_json(source_audit_path)
    raw_engine = _read_json(engine_evidence_path)
    validate_source_audit(raw_audit)
    report = build_stage_bundle_report(_engine_pairs(raw_engine))
    report["bindings"] = {
        "ncit_release": _NCIT_RELEASE,
        "constituent_workbook": {
            "file": workbook_path.name,
            "sha256": _WORKBOOK_SHA256,
            "attestation": "PENDING",
        },
        "source_audit": {
            "file": source_audit_path.name,
            "sha256": _SOURCE_AUDIT_SHA256,
        },
        "engine_evidence": {
            "file": engine_evidence_path.name,
            "sha256": _ENGINE_EVIDENCE_SHA256,
        },
    }
    report["artifact_identity"] = _payload_identity(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--source-audit", required=True, type=Path)
    parser.add_argument("--engine-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = generate_stage_bundle_artifact(
        args.workbook,
        args.source_audit,
        args.engine_evidence,
    )
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
