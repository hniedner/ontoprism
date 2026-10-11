"""The command refuses incomplete intervals and changing certified inputs."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from scripts import release_delta as command
from scripts.release_delta import history_interval, history_windows

from ontolib.terminologies.ncit.release_delta import AxiomDelta, parse_history


def test_history_reconciliation_reports_unmatched_creates_without_refusing():
    ns = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
    delta = AxiomDelta(
        removed=(),
        added=(
            f"Declaration(Class(<{ns}C2>))",
            f'AnnotationAssertion(<{ns}P310> <{ns}C1> "Retired_Concept")',
        ),
    )
    events = parse_history(
        "C2||create|28-Sep-26|null|\nC3||create|28-Sep-26|null|\nC1||retire|28-Sep-26|null|"
    )
    result = command.reconcile_history(delta, events)
    assert result["create"]["history_count"] == 2
    assert result["create"]["robot_count"] == 1
    assert result["create"]["history_only"] == ["C3"]
    assert result["retire"]["history_count"] == result["retire"]["robot_count"] == 1
    assert result["retire"]["history_only"] == []


def settings(tmp_path):
    return command.DeltaSettings(
        sources={
            v: command.ReleaseSource(manifest=tmp_path / v)
            for v in ("26.07d", "26.09d")
        },
        active_endpoint="http://localhost:7888",
        history_releases=["26.08e", "26.09d"],
        robot_heap_gib=1,
        robot_max_element_depth=100,
        robot_timeout_seconds=60,
    )


@pytest.mark.parametrize("damage", ["version", "pair", "artifact", "path"])
def test_certified_artifacts_must_belong_to_requested_store(
    tmp_path, monkeypatch, damage
):
    source = command.ReleaseSource(manifest=tmp_path / "manifest")
    manifest = SimpleNamespace(
        ontology_version="26.09d",
        pair_manifest_path="pair",
        pair_manifest_identity="pair",
        stated_artifact=SimpleNamespace(artifact_identity="stated", path="stated.owl"),
    )
    pair = SimpleNamespace(
        ontology_version="26.09d",
        manifest_identity="pair",
        stated=SimpleNamespace(artifact_identity="stated", file_path="stated.owl"),
    )
    if damage == "version":
        pair.ontology_version = "26.08e"
    elif damage == "pair":
        pair.manifest_identity = "other"
    elif damage == "artifact":
        pair.stated.artifact_identity = "other"
    else:
        pair.stated.file_path = "other.owl"
    monkeypatch.setattr(
        command, "validate_ncit_sibling_manifest", lambda path: manifest
    )
    monkeypatch.setattr(command, "validate_ncit_owl_pair", lambda path: pair)
    with pytest.raises(ValueError, match=r"mismatch|differs"):
        command._certified(source, "26.09d")


@pytest.mark.parametrize(
    "drift", ["none", "old-artifact", "new-artifact", "old-store", "new-store"]
)
async def test_comparison_checks_both_sources_before_returning_report(
    tmp_path, monkeypatch, drift
):
    cfg = settings(tmp_path)
    manifests = tuple(
        (
            SimpleNamespace(candidate_path=str(tmp_path / "candidate"), owner="owner"),
            SimpleNamespace(stated=SimpleNamespace(file_path=str(tmp_path / v))),
        )
        for v in ("old", "new")
    )

    def certified(source, release):
        index = int(release == "26.09d")
        return (
            ("changed",)
            if drift == ("new-artifact" if index else "old-artifact")
            else manifests[index]
        )

    monkeypatch.setattr(command, "_certified", certified)
    monkeypatch.setattr(
        command,
        "_inventory",
        AsyncMock(side_effect=[({"C1"}, {"C1"}), ({"C1", "C2"}, {"C1", "C2"})]),
    )
    monkeypatch.setattr(command, "_history", AsyncMock(return_value=((), [])))
    monkeypatch.setattr(
        command, "robot_diff", lambda *args: AxiomDelta(removed=(), added=())
    )
    monkeypatch.setattr(
        command,
        "_source_snapshot",
        AsyncMock(
            side_effect=[
                "old",
                "new",
                "changed" if drift == "old-store" else "old",
                "changed" if drift == "new-store" else "new",
            ]
        ),
    )
    if drift != "none":
        with pytest.raises(ValueError, match="changed during comparison"):
            await command._compare(
                "26.07d",
                "26.09d",
                cfg,
                manifests,
                "http://localhost:9999",
                ("26.08e", "26.09d"),
            )
    else:
        result = await command._compare(
            "26.07d",
            "26.09d",
            cfg,
            manifests,
            "http://localhost:9999",
            ("26.08e", "26.09d"),
        )
        assert result["neoplasm"]["added"] == ["C2"]
        assert result["axiom_counts"] == {"removed": 0, "added": 0}


def test_missing_configuration_fails_without_starting_comparison(monkeypatch):
    monkeypatch.delenv("RELEASE_DELTA_SOURCES", raising=False)
    with pytest.raises(ValueError, match="sources"):
        command.DeltaSettings(_env_file=None)


def test_publisher_declared_windows_are_provenance_not_interval_evidence():
    august = (
        "26.08e\nMonthly_history_26.08e is a direct export of the concept "
        "history table from Jul 27, 2026 to Aug 31, 2026."
    )
    september = (
        "26.09d\nMonthly_history_26.09d is a direct export of the concept "
        "history table from Aug 30, 2026 to Sept 28, 2026."
    )
    windows = history_windows(["26.08e", "26.09d"], [august, september])
    assert windows == [
        ("Jul 27, 2026", "Aug 31, 2026"),
        ("Aug 30, 2026", "Sept 28, 2026"),
    ]


@pytest.mark.parametrize("action", ["create", "merge", "split", "retire", "modify"])
async def test_history_chain_rejects_repeated_lifecycle_but_allows_modify(
    monkeypatch, action
):
    def respond(request):
        release = "26.08e" if "26.08e_Release" in request.url.path else "26.09d"
        assert request.url.path.startswith(
            f"/ftp1/NCI_Thesaurus/archive/{release}_Release/"
        )
        if request.url.path.endswith("ReadMe_history.txt"):
            body = (
                f"{release}\nMonthly_history_{release} is a direct export "
                "of the concept "
                "history table from Aug 30, 2026 to Sept 28, 2026."
            )
        else:
            assert request.url.path.endswith(f"monthly_history_{release}.txt")
            body = f"C1||{action}|31-Aug-26|C2|"
        return httpx.Response(200, text=body)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        command.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond)),
    )
    if action == "modify":
        events, provenance = await command._history(("26.08e", "26.09d"))
        assert len(events) == 2
        assert [p["release"] for p in provenance] == ["26.08e", "26.09d"]
        assert provenance[0]["declared_window"] == ("Aug 30, 2026", "Sept 28, 2026")
    else:
        with pytest.raises(ValueError, match="repeated lifecycle event"):
            await command._history(("26.08e", "26.09d"))


def test_history_interval_requires_every_month_once_and_exact_target_release():
    assert history_interval("26.07d", "26.09d", ["26.08e", "26.09d"]) == (
        "26.08e",
        "26.09d",
    )
    for releases in (
        ["26.09d"],
        ["26.08e", "26.08e", "26.09d"],
        ["26.08e", "26.09c"],
        ["26.07d", "26.08e", "26.09d"],
        ["26.09d", "26.08e"],
    ):
        with pytest.raises(ValueError, match="history interval"):
            history_interval("26.07d", "26.09d", releases)
    with pytest.raises(ValueError, match="history interval"):
        history_interval("26.09d", "26.07d", [])


@pytest.mark.parametrize("damage", ["missing", "uncertified"])
def test_missing_or_uncertified_manifest_fails_closed(tmp_path, damage):
    path = tmp_path / "candidate.json"
    if damage == "uncertified":
        path.write_text("{}")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        command._certified(command.ReleaseSource(manifest=path), "26.09d")


@pytest.mark.parametrize("fails", [False, True])
async def test_command_uses_exact_sibling_lifecycle_and_propagates_failure(
    tmp_path, monkeypatch, fails
):
    cfg = settings(tmp_path)
    target = tmp_path / "exact-candidate"
    manifest = SimpleNamespace(candidate_path=str(target), owner="exact-owner")
    monkeypatch.setattr(command, "_certified", lambda *args: (manifest, None))
    live = False

    class Runtime:
        async def observe(self, path, owner, observer):
            nonlocal live
            assert path == target
            assert owner == "exact-owner"
            live = True
            try:
                return await observer("http://localhost:54321")
            finally:
                live = False

    async def compare(*args):
        assert live
        assert args[-2] == "http://localhost:54321"
        if fails:
            raise ValueError("candidate source changed")
        return {"axiom_counts": {"removed": 1, "added": 2}}

    monkeypatch.setattr(command, "DockerQleverRuntime", Runtime)
    monkeypatch.setattr(command, "_compare", compare)
    if fails:
        with pytest.raises(ValueError, match="candidate source changed"):
            await command.release_delta("26.07d", "26.09d", cfg)
    else:
        assert (await command.release_delta("26.07d", "26.09d", cfg))["axiom_counts"][
            "added"
        ] == 2
    assert not live


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"code": "foreign:C1"}],
        [{"code": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#Cat"}],
    ],
)
async def test_bad_named_inventory_is_not_an_empty_delta(monkeypatch, rows):
    @asynccontextmanager
    async def client(endpoint):
        yield SimpleNamespace(select_once=AsyncMock(return_value=rows))

    monkeypatch.setattr(command, "ncit_sparql_client", client)
    monkeypatch.setattr(
        command, "read_scope_hierarchy_edges", AsyncMock(return_value=[])
    )
    with pytest.raises(ValueError, match="invalid named class inventory"):
        await command._inventory("http://localhost")
