# syntax=docker/dockerfile:1
# Frontend image (SvelteKit via adapter-node). Build context is the repository root so
# the build can consume the shared repository manifest:
#   docker build -f frontend/Dockerfile -t ontoprism-web .
# Inject ONTOPRISM_FASTAPI_ORIGIN for the private BFF target. Set ORIGIN explicitly, or
# trust forwarded origin headers only when an upstream proxy overwrites them.

FROM node:24-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY repository-manifest.json /repository-manifest.json
RUN npm run build && npm prune --omit=dev

FROM node:24-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/build ./build
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
USER node
EXPOSE 3000
CMD ["node", "build"]
