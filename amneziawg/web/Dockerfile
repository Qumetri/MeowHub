# Borrow the `awg` CLI from the pinned server image so the UI can never drift
# from the daemon's protocol version. Keep this ARG in step with AWG_IMAGE.
ARG AWG_IMAGE=amneziavpn/amneziawg-go:3.1.20260828
FROM ${AWG_IMAGE} AS awgtools

FROM python:3.12-alpine
RUN apk add --no-cache libqrencode-tools || apk add --no-cache libqrencode
COPY --from=awgtools /usr/bin/awg /usr/bin/awg
EXPOSE 8085
# server.py is bind-mounted at /app/server.py, so edits need only a restart.
CMD ["python3", "-u", "/app/server.py"]
