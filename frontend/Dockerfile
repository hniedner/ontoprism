# syntax=docker/dockerfile:1
# Frontend image (SvelteKit via adapter-node). Build context is the frontend/ dir:
#   docker build -t ontoprism-web ./frontend
# At runtime the app proxies /api to the backend; set ORIGIN for correct URLs.

FROM node:22-slim@sha256:53ada149d435c38b14476cb57e4a7da73c15595aba79bd6971b547ceb6d018bf AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build && npm prune --omit=dev

FROM node:22-slim@sha256:53ada149d435c38b14476cb57e4a7da73c15595aba79bd6971b547ceb6d018bf
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/build ./build
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
USER node
EXPOSE 3000
CMD ["node", "build"]
