# syntax=docker/dockerfile:1
# Backend image (FastAPI + uvicorn). Build context is the repo root:
#   docker build --build-arg ONTOPRISM_VERSION=<SCM-version> -f backend/Dockerfile -t ontoprism-api .
# The runtime deps live in the root pyproject; ontolib + backend are local packages.

FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS builder
WORKDIR /app
RUN pip install --no-cache-dir pdm==2.28.0
COPY pyproject.toml pdm.lock ./
# Export only the root project's production dependencies from the lockfile.
RUN pdm export --prod --no-hashes -o /tmp/requirements.txt

FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
WORKDIR /app
ARG ONTOPRISM_VERSION=0+unknown
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY --from=builder /tmp/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
# Local packages installed without deps (their deps came from requirements above).
COPY ontolib ./ontolib
COPY backend ./backend
RUN PDM_BUILD_SCM_VERSION="$ONTOPRISM_VERSION" pip install --no-deps ./ontolib ./backend && \
    python -c "from importlib.metadata import version; import sys; assert all(version(name) == sys.argv[1] for name in ('ontolib', 'ontoprism-backend'))" "$ONTOPRISM_VERSION"
# Non-root runtime user; /app/refresh is the writable target for refresh downloads.
RUN mkdir -p /app/refresh && useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8011
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8011"]
