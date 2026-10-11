"""Read-only comparison of an active NCIt store and one certified sibling."""

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from scripts.decompose import _source_snapshot

from ontolib.common.boundary_models import StrictFrozenBoundaryModel
from ontolib.decomposition.scope import descendant_codes, read_scope_hierarchy_edges
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.client import ncit_sparql_client
from ontolib.terminologies.ncit.owl_download import validate_ncit_owl_pair
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI
from ontolib.terminologies.ncit.release_delta import (
    RobotDiffConfig,
    _expression,
    parse_history,
    robot_diff,
    summarize_delta,
)
from ontolib.terminologies.ncit.sibling_store import (
    DockerQleverRuntime,
    validate_ncit_sibling_manifest,
)

_MONTHS_PER_YEAR = 12


def _robot_codes(expressions, retired=False):
    codes = set()
    for expression in expressions:
        kind, args = _expression(expression)
        while args and args[0] == "Annotation":
            args = args[2:]
        iri = _lifecycle_iri(kind, args, retired)
        if iri is not None:
            codes.add(iri[1:-1].removeprefix(NCIT_NS))
    return codes


def _lifecycle_iri(kind, args, retired):
    match kind, args, retired:
        case "Declaration", ["Class", [str(iri)]], False:
            return iri
        case "AnnotationAssertion", [predicate, str(iri), '"Retired_Concept"'], True:
            if predicate == f"<{NCIT_NS}P310>":
                return iri
    return None


def reconcile_history(axioms, events):
    result = {}
    for action, retired in (("create", False), ("retire", True)):
        recorded = {e.concept for e in events if e.action == action}
        observed = _robot_codes(axioms.added, retired) - _robot_codes(
            axioms.removed, retired
        )
        result[action] = {
            "history_count": sum(e.action == action for e in events),
            "robot_count": len(observed),
            "history_only": sorted(recorded - observed),
            "robot_only": sorted(observed - recorded),
        }
    return result


def _month(release: str) -> int:
    match = re.fullmatch(r"([0-9]{2})\.([0-9]{2})[a-z]", release)
    if not match or not 1 <= int(match[2]) <= _MONTHS_PER_YEAR:
        raise ValueError("history interval requires NCIt release identifiers")
    return int(match[1]) * _MONTHS_PER_YEAR + int(match[2])


def history_interval(before: str, after: str, releases: list[str]) -> tuple[str, ...]:
    start, end = _month(before), _month(after)
    if end <= start or not releases or releases[-1] != after:
        raise ValueError("history interval must end at the requested later release")
    if [_month(r) for r in releases] != list(range(start + 1, end + 1)):
        raise ValueError("history interval has a gap, overlap or wrong order")
    return tuple(releases)


def history_windows(releases, readmes):
    windows = []
    for release, readme in zip(releases, readmes, strict=True):
        expression = (
            r"Monthly_history_"
            + re.escape(release)
            + r" is a direct export of the concept history table from "
            r"(\w+ \d+, \d{4}) to (\w+ \d+, \d{4})\."
        )
        match = re.search(expression, readme)
        if not match or readme.splitlines()[0].strip() != release:
            raise ValueError("publisher history release/window mismatch")
        windows.append(match.groups())
    return windows


class ReleaseSource(StrictFrozenBoundaryModel):
    manifest: Path


class DeltaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RELEASE_DELTA_", env_file=".env", extra="ignore", strict=True
    )
    sources: dict[str, ReleaseSource]
    active_endpoint: str
    history_releases: list[str] = Field(min_length=1)
    robot_heap_gib: int = Field(gt=0)
    robot_max_element_depth: int = Field(ge=0)
    robot_timeout_seconds: int = Field(gt=0)


def _certified(source: ReleaseSource, release: str):
    manifest = validate_ncit_sibling_manifest(source.manifest)
    pair = validate_ncit_owl_pair(Path(manifest.pair_manifest_path))
    if (manifest.ontology_version, pair.ontology_version) != (release, release):
        raise ValueError("source release mismatch")
    if pair.manifest_identity != manifest.pair_manifest_identity:
        raise ValueError("source artifact pair differs from certified store")
    if pair.stated.artifact_identity != manifest.stated_artifact.artifact_identity:
        raise ValueError("stated artifact differs from certified store")
    if (
        Path(pair.stated.file_path).resolve()
        != Path(manifest.stated_artifact.path).resolve()
    ):
        raise ValueError("stated artifact path differs from certified store")
    return manifest, pair


async def _inventory(endpoint: str):
    async with ncit_sparql_client(endpoint) as client:
        edges = await read_scope_hierarchy_edges(client)
        rows = await client.select_once(
            f"SELECT DISTINCT ?code WHERE {{ GRAPH <{STATED_GRAPH_IRI}> {{ "
            "?code a <http://www.w3.org/2002/07/owl#Class> . "
            f'FILTER(STRSTARTS(STR(?code), "{NCIT_NS}C")) }} }}',
            required_variables={"code"},
        )
    codes = {r["code"].removeprefix(NCIT_NS) for r in rows}
    if not codes or any(re.fullmatch(r"C[0-9]+", c) is None for c in codes):
        raise ValueError("invalid named class inventory")
    return codes, set(descendant_codes("C3262", edges))


async def _history(releases: tuple[str, ...]):
    events = []
    provenance = []
    seen = set()
    async with httpx.AsyncClient(timeout=60) as client:
        for release in releases:
            base = "https://evs.nci.nih.gov/ftp1/NCI_Thesaurus"
            readme = await client.get(
                f"{base}/archive/{release}_Release/ReadMe_history.txt"
            )
            readme.raise_for_status()
            window = history_windows([release], [readme.text])[0]
            response = await client.get(
                f"{base}/archive/{release}_Release/monthly_history_{release}.txt"
            )
            response.raise_for_status()
            batch = parse_history(response.text)
            lifecycle = {
                (e.concept, e.action, e.reference)
                for e in batch
                if e.action != "modify"
            }
            if seen & lifecycle:
                raise ValueError("repeated lifecycle event across history files")
            seen.update(lifecycle)
            provenance.append(
                {
                    "release": release,
                    "url": str(response.url),
                    "declared_window": window,
                }
            )
            events.extend(batch)
    return tuple(events), provenance


async def _compare(
    before: str,
    after: str,
    settings: DeltaSettings,
    manifests: tuple,
    endpoint: str,
    releases: tuple[str, ...],
):
    sources = (settings.sources[before], settings.sources[after])
    endpoints = (settings.active_endpoint, endpoint)
    snapshots = [
        await _source_snapshot(s.manifest, url)
        for s, url in zip(sources, endpoints, strict=True)
    ]
    old, new = await _inventory(endpoints[0]), await _inventory(endpoints[1])
    events, provenance = await _history(releases)
    profile = RobotDiffConfig(
        heap_gib=settings.robot_heap_gib,
        max_element_depth=settings.robot_max_element_depth,
        timeout_seconds=settings.robot_timeout_seconds,
    )
    axioms = await asyncio.to_thread(
        robot_diff,
        Path(manifests[0][1].stated.file_path),
        Path(manifests[1][1].stated.file_path),
        profile,
    )
    for index, release in enumerate((before, after)):
        if _certified(sources[index], release) != manifests[index]:
            raise ValueError("source artifacts changed during comparison")
        if (
            await _source_snapshot(sources[index].manifest, endpoints[index])
            != snapshots[index]
        ):
            raise ValueError("source store changed during comparison")
    result = summarize_delta(
        before, after, old[0], new[0], old[1], new[1], axioms, events
    )
    result["history_sources"] = provenance
    result["history_reconciliation"] = reconcile_history(axioms, events)
    return result


async def release_delta(before: str, after: str, settings: DeltaSettings) -> dict:
    releases = history_interval(before, after, settings.history_releases)
    if before not in settings.sources or after not in settings.sources:
        raise ValueError("two configured source manifests are required")
    manifests = (
        _certified(settings.sources[before], before),
        _certified(settings.sources[after], after),
    )
    sibling = manifests[1][0]

    async def compare(endpoint):
        return await _compare(before, after, settings, manifests, endpoint, releases)

    return await DockerQleverRuntime().observe(
        Path(sibling.candidate_path), sibling.owner, compare
    )


def main(
    before: Annotated[str, typer.Option("--from")],
    after: Annotated[str, typer.Option("--to")],
):
    """Print a delta only after both stores and artifacts pass the postchecks."""
    typer.echo(
        json.dumps(asyncio.run(release_delta(before, after, DeltaSettings())), indent=2)
    )


if __name__ == "__main__":
    typer.run(main)
