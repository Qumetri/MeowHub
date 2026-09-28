# Caddy with the layer4 module, so port 443 can be SNI-demultiplexed between
# the web stack and a TURN-over-TLS relay for censored networks.
FROM caddy:2-builder AS build
RUN xcaddy build \
    --with github.com/mholt/caddy-l4

FROM caddy:2
COPY --from=build /usr/bin/caddy /usr/bin/caddy
