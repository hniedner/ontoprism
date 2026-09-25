"""Shared production-flags disposable QLever for publication acceptance tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ontolib.decomposition.publication import PublicationMarker
from test_support.integration_resources import IntegrationResourceOwner, run_docker

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager
    from pathlib import Path


class _ProductionFlagsOwner(IntegrationResourceOwner):
    def qlever_run_command(self, data_dir: Path) -> list[str]:
        command = super().qlever_run_command(data_dir)
        previous = "-m 4G -c 512M -e 256M"
        if previous not in command[-1]:
            raise RuntimeError("disposable QLever resource flags changed")
        command[-1] = command[-1].replace(previous, "-m 8G -c 1G -e 512M")
        return command


@contextmanager
def publication_qlever(
    provision: Callable[
        [IntegrationResourceOwner], AbstractContextManager[tuple[str, str]]
    ],
) -> Iterator[str]:
    """Provision once per test, retaining logs before owned-resource cleanup."""
    with provision(_ProductionFlagsOwner(nonce=uuid4().hex)) as (url, container_id):
        try:
            yield url
        finally:
            logs = run_docker("logs", "--tail", "60", container_id, check=False)
            print(logs.stdout, logs.stderr)


def publication_marker() -> PublicationMarker:
    return PublicationMarker(
        run_id=f"publication-{uuid4().hex}",
        source_identity="a" * 64,
        representation_identity="b" * 64,
        built_at=datetime.now(UTC),
    )
