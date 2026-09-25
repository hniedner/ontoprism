"""Resume an old-publication failure using disposable PostgreSQL and QLever."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
import typer
from scripts import decompose
from test_support.publication_qlever import publication_qlever
from typer.testing import CliRunner

from backend.config import get_settings
from backend.db import dispose_engine, make_engine, make_sessionmaker
from ontolib.core.exceptions import StorageError
from ontolib.decomposition import run as pipeline
from ontolib.decomposition.collapse_policy import NO_COLLAPSE_VETO_POLICY
from ontolib.decomposition.normalized_group_policy import (
    load_packaged_normalized_group_policy,
)
from ontolib.decomposition.provenance import ProvenanceStore
from ontolib.decomposition.provenance_models import NcitSourceSnapshot
from ontolib.decomposition.publication import read_publication_marker
from ontolib.terminologies.ncit.client import ncit_sparql_client

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.integration,
    pytest.mark.mutating_integration,
    pytest.mark.usefixtures("isolated_postgres_settings"),
]

_OLD_HEAD = "28f3da0193cfc54512e0d0036ee9bfb3eb504333"


@pytest.fixture
def old_publication(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    git = shutil.which("git")
    assert git is not None
    source = subprocess.run(  # noqa: S603 - fixed local historical commit
        [
            git,
            "show",
            f"{_OLD_HEAD}:ontolib/src/ontolib/decomposition/publication.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    old = ModuleType("old_publication_443")
    monkeypatch.setitem(sys.modules, old.__name__, old)
    exec(compile(source, "old_publication_443.py", "exec"), old.__dict__)  # noqa: S102 - trusted repository revision for old-to-new resume demo
    return old


@pytest.mark.full_build
async def test_old_publication_failure_resumes_without_redecomposition(
    tmp_path: Path,
    qlever_resource_provisioner,
    monkeypatch: pytest.MonkeyPatch,
    old_publication: ModuleType,
) -> None:
    policy = load_packaged_normalized_group_policy().model_copy(update={"rows": ()})
    monkeypatch.setattr(
        pipeline, "enumerate_in_scope_codes", AsyncMock(return_value=["C9305"])
    )
    engine = make_engine(get_settings().database_url)
    store = ProvenanceStore(make_sessionmaker(engine))
    output = tmp_path / "resumed.ttl"
    run_ids = []
    current_publish = pipeline.publish_artifact
    try:
        with publication_qlever(qlever_resource_provisioner) as url:
            completed = await _resume_contract(
                url,
                policy,
                monkeypatch,
                old_publication,
                store,
                output,
                run_ids,
                current_publish,
            )
            assert completed.publication_state == "published"
    finally:
        await dispose_engine(engine)


async def _resume_contract(
    url,
    policy,
    monkeypatch,
    old_publication,
    store,
    output,
    run_ids,
    current_publish,
):
    async with ncit_sparql_client(url) as client:
        version = await client.version()
        snapshot = NcitSourceSnapshot(
            source_identity=policy.source_identity, ontology_version=version
        )
        common = {
            "get_source_snapshot": AsyncMock(return_value=snapshot),
            "collapse_policy": NO_COLLAPSE_VETO_POLICY,
            "normalized_group_policy": policy,
            "progress": lambda event: run_ids.append(event.run_id),
        }
        with monkeypatch.context() as old_phase:
            old_phase.setattr(
                pipeline, "publish_artifact", old_publication.publish_artifact
            )
            old_phase.setattr(
                client,
                "load",
                AsyncMock(side_effect=StorageError("injected old load failure")),
            )
            with pytest.raises(pipeline.RunPublicationError):
                await pipeline.run_pipeline(
                    pipeline.RunConfig(
                        branch="neoplasm", out=output, load_to_store=True
                    ),
                    client,
                    store,
                    **common,
                )
        run_id = run_ids[-1]
        failed = await store.get_run(run_id)
        assert failed.status == "running"
        assert failed.publication_state == "failed"
        assert await store.pending_codes(run_id) == []
        monkeypatch.setattr(pipeline, "publish_artifact", current_publish)
        monkeypatch.setattr(
            pipeline,
            "_decompose_one",
            AsyncMock(side_effect=AssertionError("re-decomposed")),
        )
        loop = asyncio.get_running_loop()

        async def cli_run(**options):
            operation = pipeline.run_pipeline(
                pipeline.RunConfig(
                    branch=options["branch"],
                    out=options["out"],
                    load_to_store=options["load"],
                    resume_from=options["resume"],
                ),
                client,
                store,
                **common,
            )
            return await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(operation, loop)
            )

        # Only service/fixture injection is replaced: the actual CLI parses
        # --resume and the real admission/publication path handles it.
        monkeypatch.setattr(decompose, "_run", cli_run)
        app = typer.Typer()
        app.command()(decompose.main)
        result = await asyncio.to_thread(
            CliRunner().invoke,
            app,
            [
                "--source-manifest",
                "disposable.json",
                "--branch",
                "neoplasm",
                "--out",
                str(output),
                "--load",
                "--resume",
                run_id,
            ],
        )
        assert result.exit_code == 0, (result.output, result.exception)
        completed = await store.get_run(run_id)
        assert completed.status == "complete"
        assert completed.publication_state == "published"
        assert (await read_publication_marker(client)).run_id == run_id
        assert output.is_file()
        return completed
