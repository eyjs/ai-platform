# syntax=docker/dockerfile:1.6
FROM node:20-alpine AS base
WORKDIR /app

# ---------- deps ----------
FROM base AS deps
COPY package.json package-lock.json* ./
COPY apps/bff/package.json ./apps/bff/
COPY packages/design-system/package.json ./packages/design-system/
RUN npm install --workspaces --include-workspace-root

# ---------- build ----------
FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build --workspace @aip/bff

# ---------- runtime ----------
FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/package.json /app/package-lock.json* ./
COPY --from=build /app/apps/bff/package.json ./apps/bff/
COPY --from=build /app/apps/bff/dist ./apps/bff/dist
COPY --from=build /app/node_modules ./node_modules

EXPOSE 3001
WORKDIR /app/apps/bff
CMD ["node", "dist/main"]
