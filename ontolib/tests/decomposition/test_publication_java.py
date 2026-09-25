"""Local real-RIOT contracts; no database, container, or network is used."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from rdflib import RDF, Graph, Literal, Namespace

from ontolib.core.data_build_tools import ToolIdentityError
from ontolib.decomposition.publication import (
    PublicationMarker,
    PublicationValidationError,
    _convert_publication_ntriples,
    _publish_started_artifact,
)

# The integration lane installs pinned Jena and Java. This contract itself uses
# only local processes and temporary files; it never provisions a store.
pytestmark = pytest.mark.integration


async def test_file_only_publication_rejects_invalid_turtle_before_completion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bad.ttl"
    destination = tmp_path / "published.ttl"
    source.write_text('<urn:s> <urn:p> [ <urn:q> "broken" .\n')
    provenance = AsyncMock()
    marker = PublicationMarker(
        run_id="run",
        source_identity="a" * 64,
        representation_identity="b" * 64,
        built_at=datetime.now(UTC),
    )
    with pytest.raises(PublicationValidationError):
        await _publish_started_artifact(
            marker=marker,
            artifact=source,
            destination=destination,
            metrics={},
            load_to_store=False,
            predecessor=None,
            client=AsyncMock(),
            provenance=provenance,
        )
    assert not destination.exists()
    provenance.finish_run.assert_not_awaited()


@pytest.fixture
def java_home() -> Path:
    java = os.environ.get("JAVA") or (
        str(Path(os.environ["JAVA_HOME"]) / "bin/java")
        if os.environ.get("JAVA_HOME")
        else shutil.which("java")
    )
    assert java, "Install a JDK >=21 and export JAVA_HOME or put java on PATH"
    result = subprocess.run(  # noqa: S603 - local Java prerequisite probe
        [java, "-XshowSettings:properties", "-version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    home = next(
        line.split("=", 1)[1].strip()
        for line in result.stderr.splitlines()
        if "java.home =" in line
    )
    return Path(home)


@pytest.fixture
def launcher_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep launcher utilities, but exclude Java and the macOS /usr/bin stub."""
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("dirname", "which", "uname"):
        executable = shutil.which(name)
        assert executable, f"RIOT launcher needs {name}"
        (tools / name).symlink_to(executable)
    for name in ("JAVA", "JAVA_HOME", "JENA_HOME", "CLASSPATH", "JVM_ARGS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", str(tools))
    return tools


@pytest.mark.parametrize("discovery", ["PATH", "JAVA_HOME"])
async def test_real_riot_preserves_rdf_with_java_discovery(
    java_home: Path,
    launcher_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    discovery: str,
) -> None:
    if discovery == "PATH":
        monkeypatch.setenv("PATH", f"{java_home / 'bin'}{os.pathsep}{launcher_path}")
    else:
        monkeypatch.setenv("JAVA_HOME", str(java_home))
    source, output = tmp_path / "input.ttl", tmp_path / "output.nt"
    source.write_text(
        "@prefix ex: <https://example.org/> . "
        'ex:s ex:list (ex:a ex:b); ex:label "café"@fr .',
        encoding="utf-8",
    )
    await _convert_publication_ntriples(source, output)
    graph = Graph().parse(output, format="nt")
    ex = Namespace("https://example.org/")
    assert len(graph) == 6
    assert (ex.s, ex.label, Literal("café", lang="fr")) in graph
    head = graph.value(ex.s, ex.list)
    assert head is not None
    assert list(graph.items(head)) == [ex.a, ex.b]
    assert len(list(graph.triples((None, RDF.rest, RDF.nil)))) == 1


async def test_real_riot_missing_java_fails_before_conversion(
    launcher_path: Path,
    tmp_path: Path,
) -> None:
    source, output = tmp_path / "input.ttl", tmp_path / "output.nt"
    source.write_text('<urn:test:s> <urn:test:p> "value" .')
    with pytest.raises(ToolIdentityError, match="Cannot find a Java JDK") as error:
        await _convert_publication_ntriples(source, output)
    assert "JAVA_HOME" in str(error.value)
    assert "ONTOPRISM_JENA_DIR" in str(error.value)
    assert not output.exists()
