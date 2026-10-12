"""Stated Uberon paths used as non-identity anatomical corroboration."""

import re
from collections import defaultdict, deque
from dataclasses import dataclass

from ontolib.core.exceptions import StorageError
from ontolib.decomposition.evidence_models import EvidenceItem
from ontolib.repositories.xref.candidate_ingest import (
    SparqlSelectClient,
    fetch_uberon_xrefs,
)
from ontolib.repositories.xref.promotion import (
    build_disjoint_query,
    build_upstream_edges_query,
    build_upstream_partof_query,
)

OBO = "http://purl.obolibrary.org/obo/"
ORGAN = OBO + "UBERON_0000062"
REGION = OBO + "UBERON_0000475"
TISSUE = OBO + "UBERON_0000479"
PART = OBO + "UBERON_0000064"
SYSTEM = OBO + "UBERON_0000467"


def _bindings(row, *keys):
    if any(not row.get(key) for key in keys):
        raise StorageError("Incomplete Uberon corroboration source row")
    return tuple(str(row[key]) for key in keys)


async def _mappings(client, codes):
    mappings = defaultdict(set)
    for row in await fetch_uberon_xrefs(client):
        iri, xref = row["upstream"], row["xref"]
        code = xref.removeprefix("NCIT:")
        if code not in codes or not iri.startswith(OBO + "UBERON_"):
            continue
        if not re.fullmatch(r"UBERON_[0-9]+", iri.removeprefix(OBO)):
            raise StorageError("Malformed Uberon xref target")
        mappings[code].add(iri)
    return dict(mappings)


async def _edges(client, seeds, builder):
    edges = defaultdict(set)
    for start in range(0, len(seeds), 200):
        for row in await client.select(builder(seeds[start : start + 200])):
            child, parent = _bindings(row, "child", "parent")
            edges[child].add(parent)
    return dict(edges)


@dataclass(frozen=True)
class UberonCorroboration:
    release: str
    mappings: dict[str, set[str]]
    parents: dict[str, set[str]]
    parts: dict[str, set[str]]
    disjoint: set[tuple[str, str]]

    @classmethod
    async def read(cls, client: SparqlSelectClient, codes: set[str]):
        versions = await client.select(
            "SELECT ?version WHERE { <http://purl.obolibrary.org/obo/uberon.owl> "
            "<http://www.w3.org/2002/07/owl#versionIRI> ?version }"
        )
        if len(versions) != 1:
            raise StorageError("Uberon requires exactly one release version")
        (release,) = _bindings(versions[0], "version")
        mappings = await _mappings(client, codes)
        seeds = sorted(
            {
                iri.removeprefix(OBO).replace("_", ":", 1)
                for targets in mappings.values()
                for iri in targets
            }
        )
        parents = await _edges(client, seeds, build_upstream_edges_query)
        parts = await _edges(client, seeds, build_upstream_partof_query)
        rows = await client.select(build_disjoint_query(base_iri=OBO))
        disjoint = set()
        for row in rows:
            left, right = _bindings(row, "left", "right")
            disjoint.add((left, right))
        return cls(release, mappings, parents, parts, disjoint)

    def paths(self, seed):
        """One deterministic stated subclass path to each reachable named class."""
        paths: dict[str, tuple[str, ...]] = {seed: ()}
        queue = deque([seed])
        while queue:
            child = queue.popleft()
            for parent in sorted(self.parents.get(child, ())):
                if parent not in paths:
                    paths[parent] = (*paths[child], f"{child} rdfs:subClassOf {parent}")
                    queue.append(parent)
        return paths

    def evidence(self, code, axis, peers=()) -> tuple[str, tuple[EvidenceItem, ...]]:
        targets = self.mappings.get(code, set())
        if len(targets) != 1:
            return ("no mapping" if not targets else "mapped but unresolved"), ()
        target = next(iter(targets))
        paths = self.paths(target)
        items, conflict = [], False

        def record(stance, purpose, statements, links):
            citations = "; ".join(
                f'{iri} oboInOwl:hasDbXref "NCIT:{ncit}" (non-identity)'
                for ncit, iri in links
            )
            items.append(
                EvidenceItem(
                    tier=1,
                    kind="curated-source",
                    source="Uberon",
                    source_release=self.release,
                    locator=f"{target} / {purpose}",
                    statement=f"{stance} {purpose} only, "
                    "not the whole disease-site assertion: "
                    f"{citations}; " + "; ".join(statements),
                )
            )

        conflict = self._category(axis, paths, code, target, record)
        if axis != "op:NormalTissueOrigin":
            self._part_of(code, target, paths, peers, record)
        verdict = "supported" if items else "mapped but unresolved"
        return ("contradicted" if conflict else verdict), tuple(items)

    def _part_of(self, code, target, paths, peers, record):
        for peer in sorted(set(peers) - {code}):
            mapped = self.mappings.get(peer, set())
            if len(mapped) != 1:
                continue
            whole = next(iter(mapped))
            for ancestor, path in sorted(paths.items()):
                if whole in self.parts.get(ancestor, ()):
                    record(
                        "supports",
                        f"part-of relation to {peer}",
                        (
                            *path,
                            f"{ancestor} rdfs:subClassOf [owl:onProperty "
                            f"{OBO}BFO_0000050; owl:someValuesFrom {whole}]",
                        ),
                        ((code, target), (peer, whole)),
                    )
                    break

    def _category(self, axis, paths, code, target, record):
        expected = {
            "op:PrimarySite": (ORGAN,),
            "op:AssociatedRegion": (REGION, TISSUE),
        }.get(axis, ())
        if ORGAN in paths and set(paths).intersection((REGION, TISSUE, PART, SYSTEM)):
            return False
        for root in sorted(set(expected).intersection(paths)):
            record("supports", "D20 anatomy category", paths[root], ((code, target),))
        exclusions = [self._exclusion(paths, root) for root in expected]
        if not exclusions or not all(exclusions):
            return False
        # An explicit disjoint ancestor must exclude every permitted category.
        statements = sum(exclusions, ())
        record("contradicts", "D20 anatomy category", statements, ((code, target),))
        return True

    def _exclusion(self, paths, root):
        for ancestor in sorted(paths):
            if (ancestor, root) in self.disjoint:
                return (*paths[ancestor], f"{ancestor} owl:disjointWith {root}")
            if (root, ancestor) in self.disjoint:
                return (*paths[ancestor], f"{root} owl:disjointWith {ancestor}")
        return ()
