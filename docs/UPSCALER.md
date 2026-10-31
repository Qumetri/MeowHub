# Photo upscaler (profile `upscaler`)

Enlarges photos and pictures 2–4×
with the models of the **Upscayl** app, run headless through its CLI engine **upscayl-bin**
(`upscayl/upscayl-ncnn` build 20251207: Real-ESRGAN-family models on ncnn + Vulkan) on an NVIDIA GPU.

## Setup

1. NVIDIA GPU with the NVIDIA Container Toolkit; enable the GPU overlay in `.env`:
   `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml`.
2. Add `upscaler` to `COMPOSE_PROFILES` and run `./bootstrap.sh` — it randomises `UPSCALE_PATH`,
   creates `UPSCALE_DATA` and fetches the 7 models (`./upscaler/fetch-models.sh`, SHA-256 pinned).
3. `docker compose up -d`. The page is `https://<domain>/<UPSCALE_PATH>/` behind the bot admin login;
   members get it in the helper bot's Mini App once you switch the service `upscale` on (Bots page or
   `/services`).

| What | Where |
|---|---|
| Engine + API + owner page | `upscaler` (container `<stack>_upscaler`), loopback `:8099` |
| Code | `upscaler/app/server.py` (stdlib HTTP + Pillow), `upscaler/app/static/` (owner page, vanilla JS) |
| Binary | `upscayl-bin` from `upscayl/upscayl-ncnn` releases, downloaded and checksum-verified at image build |
| Models | `UPSCALE_DATA/models/*.bin/.param` |
| Jobs | `UPSCALE_DATA/work/<16 hex>/` (`meta.json`, previews, `out.<ext>`), learned speeds in `rates.json` |

## How it works

- **One job at a time** on the GPU, FIFO. Estimates: `3 s + in_MP × sec_per_MP(model) + out_MP × 0.12`,
  the per-model rate refined from finished jobs (EMA, jobs ≥ 0.25 MP). On an RTX 3060: a 0.9 MP photo ×4
  ≈ 10 s; model load ≈ 3 s whatever the size.
- **Upload normalisation (Pillow):** EXIF rotation applied, the ICC profile kept for the result, all other
  EXIF (GPS!) dropped; alpha kept (PNG out). `auto` format = same as input (JPEG→JPG q92, WebP→WebP, else PNG).
- **Guards:** `MAX_MB` 25 per file, `MAX_IN_MP` 24, `MAX_OUT_MP` 64 — the scale is lowered (4→3→2) to fit, and a
  picture that would exceed it even at 2× is refused (`already_large`).
- **Retention:** `KEEP_HOURS` 24, a janitor thread deletes older job dirs every 10 min. Restarting the
  container re-queues interrupted jobs.
- **CSRF:** POST/DELETE need header `X-Upscale: 1`.
- The models always run at ×4; `-s 2/3` lets upscayl-bin downscale the result, so time depends on the input
  size, not on the chosen scale.

## Vulkan in Docker (the non-obvious part)

The NVIDIA runtime with `NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics` mounts the driver's Vulkan
library but **not its loader manifest**, so the image ships `/usr/share/vulkan/icd.d/nvidia_icd.json`.
It must point at **`libEGL_nvidia.so.0`**: the host's manifest names `libGLX_nvidia.so.0`, which inside a
headless container fails with `Could not get 'vkCreateInstance'` (no X server) — upscayl-bin then prints
`vkCreateInstance failed -9` / `Invalid GPU Device`. After a driver upgrade, re-test with
`docker exec upscaler upscayl-bin -i … -o … -m /data/models -n upscayl-lite-4x`.

## Models

| id | Owner page | Member preset |
|---|---|---|
| upscayl-standard-4x | Стандарт | **photo** «Фото» |
| ultramix-balanced-4x | Больше деталей | **detail** «Больше деталей» |
| high-fidelity-4x | Бережно | **soft** «Портреты» |
| digital-art-4x | Рисунки и аниме | **art** «Рисунки и аниме» |
| remacri-4x, ultrasharp-4x, upscayl-lite-4x | under «Другие модели» | — |

Remacri / UltraSharp weights are CC BY-NC-SA (non-commercial) — fine for private use. To add a model: drop
its NCNN `.bin` + `.param` into `UPSCALE_DATA/models/`, add it to `MODELS` in `server.py`
(and to `NAMES` in `upscaler/web/src/app.ts`, then `npm run build` there — it emits `app/static/app.js`), rebuild. PyTorch models must be converted first (chaiNNer).

## Operating

```bash
docker compose up -d --build upscaler   # after editing upscaler/ (page files are in the image)
docker compose logs -f upscaler
curl -s localhost:8099/api/queue
```

Members: see [HELPER.md](HELPER.md#photo-upscaling) (service `upscale`, limits, signed links).
