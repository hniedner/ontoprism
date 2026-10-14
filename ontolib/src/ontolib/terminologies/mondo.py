"""Transient corroboration against Mondo's published disease hierarchy."""

import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from defusedxml.ElementTree import iterparse

from ontolib.core.data_build_tools import (
    DataBuildToolIdentity,
    PinnedArtifact,
    _download_https,
    _verify_artifact,
)
from ontolib.decomposition.evidence_models import EvidenceItem

VERSION = "http://purl.obolibrary.org/obo/mondo/releases/2026-09-01/mondo-base.owl"
BASIS = "Mondo published release hierarchy (publisher-classified)"
ARTIFACT = PinnedArtifact(
    identity=DataBuildToolIdentity(
        name="mondo-base",
        source="https://github.com/monarch-initiative/mondo/releases/download/"
        "v2026-09-01/mondo-base.owl",
        version=VERSION,
        digest="sha256:daffd575c0540bced1cdd4cccea2a50957395b803e09c72d6636462d7735a1cc",
    ),
    filename="mondo-base.owl",
)
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
OWL = "{http://www.w3.org/2002/07/owl#}"
RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
OBO = "{http://www.geneontology.org/formats/oboInOwl#}"
MONDO = "http://purl.obolibrary.org/obo/MONDO_"
type Bucket = Literal["supported", "unmapped", "multi-mapped", "disagreement"]
BUCKETS = ("supported", "unmapped", "multi-mapped", "disagreement")
_RDF_CHILD_DEPTH = 2


def published_mondo() -> MondoHierarchy:
    """Download, verify, read, and discard the pinned source without a cache."""
    with TemporaryDirectory(prefix="mondo-") as directory:
        path = Path(directory) / ARTIFACT.filename
        _download_https(ARTIFACT.identity.source, path)
        _verify_artifact(path, ARTIFACT)
        return MondoHierarchy.read(path)


def _elements(path):
    depth = 0
    for event, element in iterparse(path, events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        if depth == _RDF_CHILD_DEPTH:
            yield element
            element.clear()
        depth -= 1


def _resource(element, predicate):
    child = element.find(predicate)
    return None if child is None else child.get(RDF + "resource")


def _qualified(element):
    if _resource(element, OWL + "annotatedProperty") != OBO[1:-1] + "hasDbXref":
        return None
    if "MONDO:equivalentTo" not in [e.text for e in element.findall(OBO + "source")]:
        return None
    return (
        _resource(element, OWL + "annotatedSource"),
        element.findtext(OWL + "annotatedTarget"),
    )


def _class_record(element):
    return (
        element.findtext(OWL + "deprecated") in {"true", "1"},
        {e.text or "" for e in element.findall(OBO + "hasDbXref")},
        {e.get(RDF + "resource") for e in element.findall(RDFS + "subClassOf")},
    )


def _element_kind(element):
    if element.tag == RDF + "Description":
        return (_resource(element, RDF + "type") or "").replace(OWL[1:-1], OWL)
    return element.tag


def _source_rows(path):
    classes, qualified, versions = {}, set(), []
    for element in _elements(path):
        kind = _element_kind(element)
        if kind == OWL + "Ontology":
            versions.append(_resource(element, OWL + "versionIRI"))
            if element.find(OWL + "imports") is not None:
                raise ValueError("Mondo base must not import another hierarchy")
        if kind == OWL + "Axiom":
            qualified.add(_qualified(element))
        subject = element.get(RDF + "about", "")
        if kind == OWL + "Class" and subject.startswith(MONDO):
            classes[subject] = _class_record(element)
    return classes, qualified, versions


def _index_xrefs(subject, xrefs, qualified, mappings, exclusions):
    valid = {x for x in xrefs if re.fullmatch(r"NCIT:C[0-9]+", x)}
    exclusions["malformed"] += len(xrefs - valid)
    for xref in valid:
        if (subject, xref) in qualified:
            mappings[xref.removeprefix("NCIT:")].add(subject)
        else:
            exclusions["non-equivalence"] += 1
    return valid


def _item(child, genus, path):
    links = "; ".join(
        f'{node} oboInOwl:hasDbXref "NCIT:{code}" '
        '[oboInOwl:source "MONDO:equivalentTo"]'
        for node, code in ((path[0], child), (path[-1], genus))
    )
    edges = "; ".join(f"{a} rdfs:subClassOf {b}" for a, b in pairwise(path))
    return EvidenceItem(
        tier=1,
        kind="curated-source",
        source="Mondo",
        source_release=VERSION,
        locator=path[0],
        statement=f"{BASIS}; supports child-to-genus subsumption, not nearest-frontier "
        f"selection or mapping identity promotion. {links}; {edges}",
    )


@dataclass(frozen=True)
class MondoHierarchy:
    parents: dict[str, set[str]]
    mappings: dict[str, set[str]]
    reverse: dict[str, set[str]]
    exclusions: dict[str, int]

    @classmethod
    def read(cls, path):
        """Read explicit named edges and axiom-qualified xrefs, never definitions."""
        classes, qualified, versions = _source_rows(path)
        if versions != [VERSION] or not classes:
            raise ValueError(
                "Mondo release version or class inventory is missing/wrong"
            )
        return cls._index(classes, qualified)

    @classmethod
    def _index(cls, classes, qualified):
        active = {s for s, (deprecated, _, _) in classes.items() if not deprecated}
        parents, mappings, reverse, exclusions = {}, defaultdict(set), {}, Counter()
        for subject, (deprecated, xrefs, edges) in classes.items():
            ncit = {x for x in xrefs if x.startswith("NCIT:")}
            if deprecated:
                exclusions["deprecated"] += len(ncit)
                continue
            parents[subject] = edges & active
            reverse[subject] = _index_xrefs(
                subject, ncit, qualified, mappings, exclusions
            )
        return cls(parents, dict(mappings), reverse, dict(exclusions))

    def _path(self, child, genus):
        queue: deque[tuple[str, ...]] = deque([(child,)])
        visited = {child}
        while queue:
            path = queue.popleft()
            for parent in sorted(self.parents[path[-1]]):
                if parent in visited:
                    continue
                following = (*path, parent)
                if parent == genus:
                    return following
                visited.add(parent)
                queue.append(following)
        return ()

    def _target(self, code):
        targets = self.mappings.get(code, set())
        if not targets:
            return "unmapped", None
        if len(targets) != 1:
            return "multi-mapped", None
        target = next(iter(targets))
        if len(self.reverse[target]) != 1:
            return "multi-mapped", None
        return "unique", target

    def evidence(
        self, child: str, genus: str
    ) -> tuple[Bucket, tuple[EvidenceItem, ...]]:
        left, start = self._target(child)
        right, end = self._target(genus)
        if "unmapped" in (left, right):
            return "unmapped", ()
        if "multi-mapped" in (left, right):
            return "multi-mapped", ()
        path = self._path(start, end)
        if not path:
            return "disagreement", ()
        return "supported", (_item(child, genus, path),)
