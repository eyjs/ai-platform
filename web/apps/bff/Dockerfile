# syntax=docker/dockerfile:1.6
FROM node:20-alpine AS base
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate
WORKDIR /app

# ---------- deps ----------
FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY apps/bff/package.json ./apps/bff/
COPY packages/design-system/package.json ./packages/design-system/
RUN pnpm install --frozen-lockfile

# ---------- build ----------
FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/bff/node_modules ./apps/bff/node_modules
COPY . .
RUN pnpm --filter @aip/bff build

# ---------- runtime ----------
FROM node:20-alpine AS runtime
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate
WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/package.json /app/pnpm-lock.yaml /app/pnpm-workspace.yaml ./
COPY --from=build /app/apps/bff/package.json ./apps/bff/
COPY --from=build /app/apps/bff/dist ./apps/bff/dist
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/apps/bff/node_modules ./apps/bff/node_modules

EXPOSE 3001
WORKDIR /app/apps/bff
CMD ["node", "dist/main"]
