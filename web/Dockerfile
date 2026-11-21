# The LegalMind web front end: a static Next.js export served by nginx (unprivileged, port 8080).
# Per-environment settings come from the container's environment (see docker/40-config.sh).

FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /web/out /usr/share/nginx/html
COPY docker/default.conf.template /etc/nginx/templates/default.conf.template
COPY docker/40-config.sh /docker-entrypoint.d/40-config.sh
ENV WEB_API_URL="" WEB_CLIENT_ID="" WEB_TENANT_ID="" WEB_API_SCOPE=""
EXPOSE 8080
