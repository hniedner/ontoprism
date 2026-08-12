from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from ontolib.core.data_build_tools import JENA_INSTALL_DIR_ENV
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.activation import (
    ActivationHealthError,
    ActivationJournal,
    ActivationRolledBackError,
    DockerComposeNcitService,
    QleverServiceContract,
    prepare_activation_journal,
    read_activation_journal,
    run_journaled_activation,
    stage_active_store_for_rollback,
    transition_activation_journal,
)
from ontolib.terminologies.ncit.sibling_store import (
    CANDIDATE_MANIFEST_FILENAME,
    QLEVER_IMAGE,
    QLEVER_INDEX_VERSION,
    CandidateValidationPolicy,
    DockerQleverRuntime,
    NcitSiblingStoreManifest,
    build_initial_ncit_store,
    build_ncit_sibling_store,
    observe_ncit_candidate,
    run_docker,
    validate_ncit_sibling_manifest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractContextManager

_ONTOLOGY_IRI = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"
_VERSION = "26.activation-test"
_INFERRED = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{_ONTOLOGY_IRI}">
    <owl:versionInfo>{_VERSION}</owl:versionInfo>
  </owl:Ontology>
  <owl:Class rdf:about="{NCIT_NS}C6135"/>
</rdf:RDF>
""".encode()
_STATED = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#">
  <owl:Ontology rdf:about="{_ONTOLOGY_IRI}">
    <owl:versionInfo>{_VERSION}</owl:versionInfo>
  </owl:Ontology>
  <owl:Class rdf:about="{NCIT_NS}C14806">
    <owl:deprecated rdf:datatype="http://www.w3.org/2001/XMLSchema#boolean">true</owl:deprecated>
  </owl:Class>
  <owl:Class rdf:about="{NCIT_NS}C6135">
    <owl:equivalentClass>
      <owl:Class>
        <owl:intersectionOf rdf:parseType="Collection">
          <owl:Class rdf:about="{NCIT_NS}C1"/>
          <owl:Restriction>
            <owl:onProperty rdf:resource="{NCIT_NS}R88"/>
            <owl:someValuesFrom rdf:resource="{NCIT_NS}C27970"/>
          </owl:Restriction>
        </owl:intersectionOf>
      </owl:Class>
    </owl:equivalentClass>
  </owl:Class>
</rdf:RDF>
""".encode()
_POLICY = CandidateValidationPolicy(
    min_default_triples=1,
    max_default_triples=100,
    min_stated_triples=1,
    max_stated_triples=100,
    min_restrictions=1,
    max_restrictions=5,
)


def _identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_pair(root: Path) -> Path:
    pair_dir = root / "pair"
    pair_dir.mkdir()
    records: dict[str, dict[str, object]] = {}
    for variant, payload in (("inferred", _INFERRED), ("stated", _STATED)):
        archive = pair_dir / f"{variant}.zip"
        artifact = pair_dir / f"{variant}.owl"
        archive.write_bytes(f"{variant}-archive".encode())
        artifact.write_bytes(payload)
        record: dict[str, object] = {
            "variant": variant,
            "source_url": f"https://example.test/{variant}.zip",
            "archive_path": str(archive.resolve()),
            "file_path": str(artifact.resolve()),
            "archive_size_bytes": archive.stat().st_size,
            "size_bytes": artifact.stat().st_size,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "owl_sha256": hashlib.sha256(payload).hexdigest(),
            "ontology_version": _VERSION,
            "ontology_iri": _ONTOLOGY_IRI,
        }
        record["artifact_identity"] = _identity(
            {
                "variant": variant,
                "source_url": record["source_url"],
                "archive_sha256": record["archive_sha256"],
                "owl_sha256": record["owl_sha256"],
                "ontology_version": _VERSION,
                "ontology_iri": _ONTOLOGY_IRI,
            }
        )
        records[variant] = record
    manifest = {
        "schema_version": 1,
        "manifest_identity": _identity(
            {
                "schema_version": 1,
                "stated": records["stated"]["artifact_identity"],
                "inferred": records["inferred"]["artifact_identity"],
                "ontology_version": _VERSION,
                "ontology_iri": _ONTOLOGY_IRI,
            }
        ),
        "ontology_version": _VERSION,
        "ontology_iri": _ONTOLOGY_IRI,
        **records,
    }
    path = pair_dir / "ncit-artifact-pair.json"
    path.write_text(json.dumps(manifest))
    return path


def _write_compose_file(
    root: Path,
    active: Path,
    contract: QleverServiceContract,
) -> None:
    (root / "compose.yaml").write_text(
        f"""services:
  {contract.service_name}:
    image: {contract.image}
    container_name: {contract.container_name}
    # The sibling index is built as the host owner. The server must use the same
    # uid/gid or image uid 999 exits against this bind mount on Linux CI.
    user: "{os.getuid()}:{os.getgid()}"
    working_dir: /data
    entrypoint: [\"/bin/sh\", \"-c\"]
    command:
      - >-
        exec qlever-server -i ncit -p 7001 --no-access-check
        -j 1 -m 1G -c 128M -e 128M -s 10s
    ports: [\"127.0.0.1::7001\"]
    volumes: [\"{active.resolve()}:/data\"]
    healthcheck:
      test:
        - CMD-SHELL
        - >-
          curl -fsS -G --data-urlencode 'query=ASK {{}}'
          -H 'Accept: application/sparql-results+json'
          http://127.0.0.1:7001/ >/dev/null
      interval: 1s
      timeout: 1s
      retries: 30
"""
    )


def _endpoint(contract: QleverServiceContract) -> str:
    port = run_docker("port", contract.container_name, "7001/tcp").stdout.strip()
    return f"http://{port}"


def _jena_install_dir() -> Path:
    """Resolve Jena from the documented env var, falling back to the repo default.

    `docs/DATA_SETUP.md` installs Jena under `.tools/jena-6.1.0` and exports
    `ONTOPRISM_JENA_DIR`; CI installs it under the runner temp dir and exports the same
    variable. Hardcoding the repo-relative default made this test pass locally and fail
    in CI with "cannot read pinned artifact .../apache-jena-6.1.0.tar.gz".
    """
    configured = os.environ.get(JENA_INSTALL_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / ".tools/jena-6.1.0"


async def _build_activation_pair(
    root: Path,
    connection_scope: Callable[[str], AbstractContextManager[None]],
) -> tuple[Path, NcitSiblingStoreManifest, NcitSiblingStoreManifest]:
    pair_path = _write_pair(root)
    runtime = DockerQleverRuntime(
        connection_scope=connection_scope,
        jena_install_dir=_jena_install_dir(),
    )
    active_path = root / "qlever-ncit"
    active = await build_initial_ncit_store(
        pair_path,
        active_store_path=active_path,
        runtime=runtime,
        owner="b" * 32,
        policy=_POLICY,
    )
    candidate = await build_ncit_sibling_store(
        pair_path,
        active_store_path=active_path,
        runtime=runtime,
        owner="a" * 32,
        policy=_POLICY,
    )
    return active_path, active, candidate


@asynccontextmanager
async def _publication_pause() -> AsyncIterator[None]:
    yield


async def _no_projection(_journal: ActivationJournal) -> None:
    return None


@pytest.mark.integration
@pytest.mark.mutating_integration
async def test_real_disposable_qlever_activation_rollback_and_interrupted_recovery(
    qlever_sibling_store_root: Path,
    integration_connection_scope: Callable[[str], AbstractContextManager[None]],
) -> None:
    for scenario in ("success", "rollback", "interrupted"):
        root = qlever_sibling_store_root / scenario
        root.mkdir()
        active_path, old, candidate = await _build_activation_pair(
            root,
            integration_connection_scope,
        )
        nonce = uuid4().hex[:12]
        contract = QleverServiceContract(
            service_name=f"qlever-ncit-{nonce}",
            container_name=f"ontoprism-qlever-ncit-{nonce}",
            image=QLEVER_IMAGE,
            image_id="sha256:" + QLEVER_IMAGE.rsplit("@sha256:", 1)[1],
            index_version=QLEVER_INDEX_VERSION,
            index_basename="ncit",
        )
        _write_compose_file(root, active_path, contract)
        service = DockerComposeNcitService(
            project_directory=root,
            contract=contract,
            readiness_attempts=300,
            readiness_interval=0.1,
        )
        run_docker(
            "compose",
            "--project-directory",
            str(root),
            "up",
            "-d",
            "--wait",
            contract.service_name,
        )
        try:
            journal_path, journal = prepare_activation_journal(
                Path(candidate.candidate_path) / CANDIDATE_MANIFEST_FILENAME,
                expected_active_path=active_path,
                minimum_free_bytes=0,
                expected_policy=_POLICY,
            )

            async def health(
                current: ActivationJournal,
                service_contract: QleverServiceContract = contract,
            ) -> None:
                endpoint = _endpoint(service_contract)
                with integration_connection_scope(endpoint):
                    observed = await observe_ncit_candidate(endpoint)
                installed = validate_ncit_sibling_manifest(
                    Path(current.active_path) / CANDIDATE_MANIFEST_FILENAME,
                    expected_policy=_POLICY,
                )
                assert observed == installed.observation

            if scenario == "rollback":

                async def reject_after_real_health(current: ActivationJournal) -> None:
                    await health(current)
                    raise ActivationHealthError("forced disposable rejection")

                with pytest.raises(
                    ActivationRolledBackError,
                    match="forced disposable",
                ):
                    await run_journaled_activation(
                        journal_path,
                        service=service,
                        pause_publication=_publication_pause,
                        reconcile_projection=_no_projection,
                        validate_health=reject_after_real_health,
                        validate_rollback_health=health,
                    )
                assert read_activation_journal(journal_path).phase == "rolled-back"
                assert (
                    validate_ncit_sibling_manifest(
                        active_path / CANDIDATE_MANIFEST_FILENAME,
                        expected_policy=_POLICY,
                    ).owner
                    == old.owner
                )
                continue

            if scenario == "interrupted":
                service.stop(active_path)
                journal = transition_activation_journal(
                    journal_path, journal, "publication-paused"
                )
                journal = transition_activation_journal(
                    journal_path, journal, "service-stopped"
                )
                stage_active_store_for_rollback(journal_path, journal)
                journal_path.write_text(journal.model_dump_json(indent=2) + "\n")

            result = await run_journaled_activation(
                journal_path,
                service=service,
                pause_publication=_publication_pause,
                reconcile_projection=_no_projection,
                validate_health=health,
                validate_rollback_health=health,
            )
            assert result.phase == "complete"
            assert (
                validate_ncit_sibling_manifest(
                    active_path / CANDIDATE_MANIFEST_FILENAME,
                    expected_policy=_POLICY,
                ).owner
                == candidate.owner
            )
        finally:
            run_docker(
                "compose",
                "--project-directory",
                str(root),
                "down",
                "--remove-orphans",
                check=False,
            )
