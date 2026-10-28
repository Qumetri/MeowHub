#!/usr/bin/env bash
# Downloads the upscaler's models (the ones bundled with the Upscayl app) into <data>/models and
# checks each against a pinned SHA-256. Idempotent: files already present and correct are skipped.
#   ./upscaler/fetch-models.sh /srv/media/upscale
# Remacri and UltraSharp are CC BY-NC-SA 4.0 (non-commercial use only).
set -euo pipefail
dest=${1:?usage: fetch-models.sh <upscale data dir>}/models
base=https://raw.githubusercontent.com/upscayl/upscayl/main/resources/models
mkdir -p "$dest"
while read -r sum name; do
  f=$dest/$name
  if [[ -f $f ]] && echo "$sum  $f" | sha256sum -c --status -; then continue; fi
  curl -fsSL -o "$f.part" "$base/$name"
  echo "$sum  $f.part" | sha256sum -c --status - || { rm -f "$f.part"; echo "checksum mismatch: $name" >&2; exit 1; }
  mv "$f.part" "$f"
  echo "  fetched $name"
done <<'SUMS'
713ee713b0353afaa27976f0563a64a5043bd70b9bd8936c2e26e25ebcdbcddf upscayl-standard-4x.bin
35330ececcea33b6c397a72548e788d5d53becee4734c50b7fada36e89f10a86 upscayl-standard-4x.param
171cae5968485d366b4fb575d232f98d117d94b766b15f22849cfccde40d2050 ultramix-balanced-4x.bin
859ecba5b3592ecf3e76c93bed65e9f627b5236dd696aae5a84ecf8c93ab65ce ultramix-balanced-4x.param
8a135402b4f39286121b76abb47601a6b7b7e8d4f3e999a5aaa45ed277824fb4 high-fidelity-4x.bin
4576ed5c2fc5fa250d3c3d585ef02248f26abdfc1867088078f501fe71e5d61e high-fidelity-4x.param
a43be595c0d743314c30b50fe7ef188be0c61cc55c46ce81adb79ba4b3c3fb7a remacri-4x.bin
859ecba5b3592ecf3e76c93bed65e9f627b5236dd696aae5a84ecf8c93ab65ce remacri-4x.param
fb3e279d40d4cddb44db4e684d59e68d0aa39852c8cc14dc3f23ccc7e6eee9c1 ultrasharp-4x.bin
0136ca83686809a8f17f7111f11b951e8db93610e24b7f4137c9ffe4dbc4a806 ultrasharp-4x.param
85ee266b632a765a725425ba6a5620c088c8aa2939a03063b2d83b3462724cc1 upscayl-lite-4x.bin
22174924330297357434ad21ed0af7f4b820008d2a502b492754d130d4142714 upscayl-lite-4x.param
fe01c269cfd10cdef8e018ab66ebe750cf79c7af4d1f9c16c737e1295229bacc digital-art-4x.bin
2b8fb6e0ae4d2d85704ca08c119a2f5ea40add4f2ecd512eb7f4cd44b6127ed4 digital-art-4x.param
SUMS
