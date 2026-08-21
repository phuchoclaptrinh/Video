# ==============================================================
# Frontend (Next.js 15) — multi-stage
#   target development : next dev, code mount từ host
#   target production  : dựa trên output "standalone" -> image nhỏ (~150MB)
#
# LƯU Ý: biến NEXT_PUBLIC_* được nhúng vào bundle lúc BUILD, không phải runtime.
# Đổi API URL => phải build lại image.
# ==============================================================

FROM node:20-alpine AS base
RUN apk add --no-cache libc6-compat
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# ---------------------------------------------------------- deps
FROM base AS deps
COPY package.json package-lock.json* ./
RUN npm ci

# --------------------------------------------------- development
FROM base AS development
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NODE_ENV=development
EXPOSE 3000
CMD ["npm", "run", "dev"]

# --------------------------------------------------------- build
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_APP_NAME
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \
    NODE_ENV=production

RUN npm run build

# ---------------------------------------------------- production
FROM base AS production
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
# standalone đã gói sẵn node_modules cần thiết
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://localhost:3000/ >/dev/null || exit 1

CMD ["node", "server.js"]
