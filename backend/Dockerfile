# syntax=docker/dockerfile:1
# Backend image (FastAPI + uvicorn). Build context is the repo root:
#   docker build -f backend/Dockerfile -t ontoprism-api .
# The runtime deps live in the root pyproject; ontolib + backend are local packages.

FROM python:3.13-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir pdm
COPY pyproject.toml pdm.lock ./
# Export only the root project's production dependencies from the lockfile.
RUN pdm export --prod --no-hashes -o /tmp/requirements.txt

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY --from=builder /tmp/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
# Local packages installed without deps (their deps came from requirements above).
COPY ontolib ./ontolib
COPY backend ./backend
RUN pip install --no-deps ./ontolib ./backend
# Non-root runtime user; /app/refresh is the writable target for refresh downloads.
RUN mkdir -p /app/refresh && useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8011
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8011"]
