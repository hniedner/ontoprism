"""Docker environment selectors shared by validation entry points."""

from __future__ import annotations

DOCKER_SELECTOR_VARIABLES = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "PODMAN_COMPOSE_PROVIDER",
    "CONTAINER_HOST",
)
