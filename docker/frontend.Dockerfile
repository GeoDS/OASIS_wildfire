# FireScope frontend: a production Next.js build.
# Build context is the repository root; nothing under frontend/ is Docker-specific.
FROM node:24.4.0-bookworm-slim

# The version that installed the checked-in lockfile.
RUN npm install -g pnpm@11.19.0

WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend ./

# The browser calls the API directly, so its address is fixed when the bundle is built.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

ENV NODE_ENV=production
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000"]
