# Gateway: nginx reverse proxy for /ogc/, /tiles/, /api/, /viewer/ and the application API routes.
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
COPY nginx.conf /etc/nginx/nginx.conf
COPY default.conf /etc/nginx/conf.d/default.conf
COPY read-access.public.conf read-access.protected.conf /etc/nginx/cgis/
# Default (public) snippet baked in, so `nginx -t` works even without the entrypoint hook.
COPY read-access.public.conf /etc/nginx/cgis/read-access.conf
COPY --chmod=0755 40-cgis-read-access.sh /docker-entrypoint.d/40-cgis-read-access.sh
EXPOSE 8080
