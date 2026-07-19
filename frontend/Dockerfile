# syntax=docker/dockerfile:1
# Frontend image (SvelteKit via adapter-node). Build context is the frontend/ dir:
#   docker build -t ontoprism-web ./frontend
# At runtime the app proxies /api to the backend; set ORIGIN for correct URLs.

FROM node:23-slim@sha256:86191b94d2a163be41f3dc7fe5e5fcaca8ba2f1be7275d98a06343483c17414a AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build && npm prune --omit=dev

FROM node:23-slim@sha256:86191b94d2a163be41f3dc7fe5e5fcaca8ba2f1be7275d98a06343483c17414a
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/build ./build
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
USER node
EXPOSE 3000
CMD ["node", "build"]
