# Image upscaler: upscayl-bin (the engine of the Upscayl app — Real-ESRGAN-family models on
# ncnn + Vulkan) behind a small stdlib HTTP API. Needs an NVIDIA GPU (docker-compose.gpu.yml);
# models are fetched into the data dir by ./upscaler/fetch-models.sh (bootstrap.sh runs it).
FROM debian:bookworm-slim
ARG UPSCAYL_BIN_VERSION=20251207-174704
ARG UPSCAYL_BIN_SHA256=a9fab3c770b62f2b7a35d8d6d61eb4e8b3aef79128b665c919d080b85a2292f2
RUN apt-get update && apt-get install -y --no-install-recommends \
        libvulkan1 libgomp1 python3 python3-pil ca-certificates curl unzip \
    && curl -fsSL -o /tmp/u.zip "https://github.com/upscayl/upscayl-ncnn/releases/download/${UPSCAYL_BIN_VERSION}/upscayl-bin-${UPSCAYL_BIN_VERSION}-linux.zip" \
    && echo "${UPSCAYL_BIN_SHA256}  /tmp/u.zip" | sha256sum -c - \
    && unzip -j /tmp/u.zip '*/upscayl-bin' -d /usr/local/bin && chmod 755 /usr/local/bin/upscayl-bin \
    && rm /tmp/u.zip && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
# The NVIDIA runtime mounts the driver's Vulkan library (needs the `graphics` capability) but not
# its loader manifest. Headless, only the EGL entry point works (libGLX_nvidia fails: no X server).
COPY vendor/nvidia_icd.json /usr/share/vulkan/icd.d/nvidia_icd.json
COPY app/ /app/
WORKDIR /app
USER 1000:1000
CMD ["python3", "-u", "/app/server.py"]
