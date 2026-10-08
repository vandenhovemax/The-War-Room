# The War Room — frontend image (Next.js 15, port 3033).
# The /api/* rewrite target MUST be set at BUILD time: Next.js bakes rewrites
# into the build manifest, so a runtime env var is too late (learned 2026-08-08
# — runtime-only var proxied to localhost inside the container, ECONNREFUSED).
FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG BACKEND_INTERNAL_URL=http://localhost:8055
ENV BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL
RUN npm run build

EXPOSE 3033
CMD ["npx", "next", "start", "-p", "3033"]
