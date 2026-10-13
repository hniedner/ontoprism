"""Compare stated NCIt with a SubClassOf-only decomposition projection."""

import re
from functools import partial
from itertools import combinations
from pathlib import Path
from time import perf_counter

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from ontolib.core.robot import RobotConfig, run_robot
from ontolib.repositories.xref.validation import (
    ReasonerUnavailableError,
    _refuse_if_killed,
)
from ontolib.terminologies.namespaces import NCIT_NS

OP = "https://w3id.org/ontoprism/vocab#"


def _projection(assertions):
    graph = Graph()
    for row in assertions:
        subject = URIRef(NCIT_NS + row["concept_code"])
        filler = URIRef(
            (OP if row["filler_code"].startswith("MINT-") else NCIT_NS)
            + row["filler_code"]
        )
        roles = row["source_roles"] or [row["axis"]]
        for role in roles:
            prop = URIRef(OP + role[3:] if role.startswith("op:") else NCIT_NS + role)
            restriction = BNode()
            for triple in (
                (subject, RDF.type, OWL.Class),
                (filler, RDF.type, OWL.Class),
                (prop, RDF.type, OWL.ObjectProperty),
                (subject, RDFS.subClassOf, restriction),
                (restriction, RDF.type, OWL.Restriction),
                (restriction, OWL.onProperty, prop),
                (restriction, OWL.someValuesFrom, filler),
            ):
                graph.add(triple)
    return graph


def _classify(path, output, robot):
    result = robot(
        [
            "reason",
            "--reasoner",
            "ELK",
            "-v",
            "--input",
            str(path),
            "--remove-redundant-subclass-axioms",
            "false",
            "--include-indirect",
            "true",
            "--equivalent-classes-allowed",
            "all",
            "--axiom-generators",
            "SubClass EquivalentClass",
            "--create-new-ontology",
            "true",
            "--exclude-owl-thing",
            "true",
            "--output",
            str(output),
        ]
    )
    _refuse_if_killed(result, "reason")
    diagnostic = result.stdout + result.stderr
    output.with_suffix(".log").write_text(diagnostic)
    if result.returncode:
        unsat = sorted(
            set(re.findall(r"ReasonerHelper -\s+unsatisfiable: (\S+)", diagnostic))
        )
        count = re.search(
            r"ReasonerHelper - There are (\d+) unsatisfiable classes in the ontology\.",
            diagnostic,
        )
        if count and int(count[1]) == len(unsat) and unsat:
            return {
                "consistency": "consistent",
                "unsatisfiable": [c.removeprefix(NCIT_NS) for c in unsat],
            }, None
        raise ReasonerUnavailableError(diagnostic)
    graph = Graph().parse(output)
    return {"consistency": "consistent", "unsatisfiable": []}, _subsumptions(graph)


def _named_pairs(graph, predicate):
    return [
        (a, b)
        for a, b in graph.subject_objects(predicate)
        if isinstance(a, URIRef) and isinstance(b, URIRef)
    ]


def _subsumptions(graph):
    named = Graph()
    for child, parent in _named_pairs(graph, RDFS.subClassOf):
        named.add((child, RDFS.subClassOf, parent))
    for left, right in _named_pairs(graph, OWL.equivalentClass):
        named.add((left, RDFS.subClassOf, right))
        named.add((right, RDFS.subClassOf, left))
    return {
        (str(child), str(parent))
        for child in set(named.subjects())
        for parent in named.transitive_objects(child, RDFS.subClassOf)
        if child != parent
    }


def _codes(pairs):
    return sorted([a.removeprefix(NCIT_NS), b.removeprefix(NCIT_NS)] for a, b in pairs)


def _merge(stated, assertions, workspace, robot):
    projection = workspace / "projection.ttl"
    _projection(assertions).serialize(projection, format="turtle")
    enhanced = workspace / "enhanced.owl"
    result = robot(
        [
            "merge",
            "--input",
            str(stated),
            "--input",
            str(projection),
            "--output",
            str(enhanced),
        ]
    )
    _refuse_if_killed(result, "merge")
    if result.returncode:
        raise ReasonerUnavailableError(result.stdout + result.stderr)
    return enhanced


def _attribute(stated, assertions, new_unsat, workspace, robot):
    affected = []
    for index, row in enumerate(assertions):
        if row["concept_code"] not in new_unsat:
            continue
        trial = workspace / f"assertion-{index}"
        trial.mkdir()
        merged = _merge(stated, [row], trial, robot)
        result, _ = _classify(merged, trial / "classified.owl", robot)
        if row["concept_code"] in result["unsatisfiable"]:
            affected.append(row)
    return affected


def check_projection(stated, assertions, workspace, config: RobotConfig):
    started = perf_counter()
    robot = partial(run_robot, config=config)
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=False)
    enhanced = _merge(stated, assertions, workspace, robot)
    merged = perf_counter()
    baseline, old = _classify(stated, workspace / "baseline.ttl", robot)
    classified_baseline = perf_counter()
    augmented, new = _classify(enhanced, workspace / "classified.ttl", robot)
    classified_enhanced = perf_counter()
    new_unsat = sorted(set(augmented["unsatisfiable"]) - set(baseline["unsatisfiable"]))
    affected = _attribute(stated, assertions, new_unsat, workspace, robot)
    attributed = perf_counter()
    profiles = {
        "baseline": _profile(stated, robot),
        "enhanced": _profile(enhanced, robot),
    }
    for name, value in (("baseline", baseline), ("enhanced", augmented)):
        if not profiles[name]["in_profile"]:
            value["consistency"] = "not-proved-outside-EL"
    return {
        "baseline": baseline,
        "enhanced": augmented,
        "new_subsumptions": _codes(new - old)
        if new is not None and old is not None
        else None,
        "lost_subsumptions": _codes(old - new)
        if new is not None and old is not None
        else None,
        "new_unsatisfiable": new_unsat,
        "contradictory_assertions": affected,
        "el_profile": profiles,
        "phase_seconds": {
            "merge": merged - started,
            "baseline_classification_and_read": classified_baseline - merged,
            "enhanced_classification_and_read": classified_enhanced
            - classified_baseline,
            "attribution": attributed - classified_enhanced,
            "profiles_and_comparison": perf_counter() - attributed,
        },
    }


def _profile(path, robot):
    result = robot(["validate-profile", "--profile", "EL", "--input", str(path)])
    _refuse_if_killed(result, "profile")
    diagnostic = result.stdout + result.stderr
    if result.returncode and not re.search(
        r"violation|not in profile", diagnostic, re.I
    ):
        raise ReasonerUnavailableError(diagnostic)
    return {"in_profile": result.returncode == 0, "diagnostic": diagnostic}


def _disjoint_pairs(pairs, left, right):
    return [
        (a, b)
        for a, b in pairs
        if (a in left and b in right) or (b in left and a in right)
    ]


def disjoint_fillers(assertions, hierarchy):
    """Inspection only: disjoint existential fillers need not share a witness."""
    groups = {}
    for row in assertions:
        groups.setdefault((row["concept_code"], row["axis"]), set()).add(
            row["filler_code"]
        )
    findings = []
    pairs = {(p.left, p.right) for p in hierarchy.disjoint_pairs}
    for (code, axis), fillers in sorted(groups.items()):
        for left, right in combinations(sorted(fillers), 2):
            ancestors = hierarchy.ancestor_paths(left), hierarchy.ancestor_paths(right)
            evidence = _disjoint_pairs(pairs, *ancestors)
            if evidence:
                findings.append(
                    {
                        "concept_code": code,
                        "axis": axis,
                        "fillers": [left, right],
                        "disjoint_pairs": sorted(evidence),
                    }
                )
    return findings
