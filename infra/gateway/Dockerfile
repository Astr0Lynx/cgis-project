# Gateway image: nginx reverse proxy for /ogc/, /tiles/, /api/, /viewer/ and the application API routes.
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
COPY nginx.conf /etc/nginx/nginx.conf
COPY default.conf /etc/nginx/conf.d/default.conf
COPY 40-cgis-gateway.sh /docker-entrypoint.d/40-cgis-gateway.sh
# Defaults (Docker DNS, public read routes) so `nginx -t` works without the start-up script.
RUN chmod 0755 /docker-entrypoint.d/40-cgis-gateway.sh && mkdir -p /etc/nginx/cgis \
    && echo "resolver 127.0.0.11 valid=10s ipv6=off;" > /etc/nginx/cgis/resolver.conf \
    && : > /etc/nginx/cgis/read-access.conf \
    && chown -R nginx /etc/nginx/cgis
EXPOSE 8080
