FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:4714e0b1b2577eaa1a6131d07c958b67f0eb68e6d0521e90c6e5287db8cf0bc5
COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/railway/nginx.conf.template /etc/sentinel/nginx.conf.template
COPY --chmod=755 infra/railway/start-frontend.sh /usr/local/bin/start-sentinel
USER 101
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/start-sentinel"]
