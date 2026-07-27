# syntax=docker/dockerfile:1
# Frontend image (SvelteKit via adapter-node). Build context is the frontend/ dir:
#   docker build -t ontoprism-web ./frontend
# At runtime the app proxies /api to the backend; set ORIGIN for correct URLs.

FROM node:26-slim@sha256:715e55e4b84e4bb0ff48e49b398a848f08e55daed8eb6a0ea1839ae53bc57583 AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build && npm prune --omit=dev

FROM node:26-slim@sha256:715e55e4b84e4bb0ff48e49b398a848f08e55daed8eb6a0ea1839ae53bc57583
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/build ./build
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
USER node
EXPOSE 3000
CMD ["node", "build"]
