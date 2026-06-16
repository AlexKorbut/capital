# syntax=docker/dockerfile:1
# KAPITAL frontend — build the Vite SPA, then serve the static bundle via nginx.
FROM node:22-alpine AS build

WORKDIR /app

# Install deps with a clean, reproducible lockfile install.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# In prod the SPA talks to the API on the same origin (reverse proxy routes
# /api -> backend), so the default relative base "/api/v1" is correct. Override
# with VITE_API_BASE_URL at build time only if the API lives elsewhere.
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
# Sentry DSN + release are baked in at build time (Vite inlines VITE_* envs).
ARG VITE_SENTRY_DSN
ENV VITE_SENTRY_DSN=${VITE_SENTRY_DSN}
ARG VITE_GIT_SHA
ENV VITE_GIT_SHA=${VITE_GIT_SHA}
# Optional cookieless analytics (Plausible); empty disables it.
ARG VITE_PLAUSIBLE_DOMAIN
ENV VITE_PLAUSIBLE_DOMAIN=${VITE_PLAUSIBLE_DOMAIN}
RUN npm run build

# --- Runtime: static files on nginx ---------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
