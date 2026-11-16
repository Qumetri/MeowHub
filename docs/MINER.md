# GPU miner with a Telegram switch (optional, host)

A GPU that serves a language model, photo upscaling and video transcoding is idle most of the day. This lets the
owner and the helper bot's admins turn a miner on and off from Telegram, while a guard on the host makes sure the
miner never competes with real work. Example setup: Pearl (PRL) on the Kryptex pool with SRBMiner-MULTI on an
RTX 3060. Nothing is installed by `bootstrap.sh`; it is a host service you add yourself.

| | |
|---|---|
| Switch | helper bot: «⚙️ Control» → «⛏ Miner», or `/miner` (owner and `HELPER_ADMIN_IDS`); the owner is told when an admin flips it |
| Bot side | `helper/app/miner.py`: writes `helper/data/miner-want.json`, reads `helper/data/miner-status.json`. The container gets no new rights |
| Host side | `scripts/gpu-miner.py` → `/usr/local/sbin/gpu-miner`, unit `gpu-miner.service` (root). It alone starts and stops `prl-miner.service` |
| Miner | `/opt/prl-miner/srbminer` (SRBMiner-MULTI 3.8.2, sha256 checked by the installer); wallet, pool and overclock in `scripts/prl-miner.service` |

## Install

1. Put your wallet id and a worker name into `scripts/prl-miner.service` (the installer refuses the placeholder),
   and retune the overclock flags for your card or delete them.
2. `sudo scripts/gpu-miner-install.sh`. Remove everything with `sudo scripts/gpu-miner-install.sh --remove`.
3. Open «⚙️ Control» → «⛏ Miner» in the helper bot. Until the guard runs, the screen says "not installed".

After editing `scripts/gpu-miner.py`, run the installer again (the daemon is a root-owned copy).

## The guard

The card is shared, and a local LLM server usually keeps its model in video memory all the time. Treating any other process on the card as "busy" would mean the miner never starts, so the rules are:

- `llama-server` (list `GUARD_RESIDENT`) counts as busy only while it computes: its GPU share in the last 6 s is at
  least `GUARD_BUSY_UTIL` (8 %).
- Any other process on the card (Immich ML and NVENC, the upscaler, book2audio) counts as busy while it exists.
- Busy → the miner stops within `GUARD_POLL` (2 s) and the overclock is reset. Idle for `GUARD_RESUME_AFTER` (120 s)
  with the switch on → it starts again.
- The miner starts only with `GUARD_FREE_MB` (600) MB of free video memory.

States in the bot: off, mining (hashrate, accepted / rejected shares), paused (who holds the card), waiting, no free
memory, guard not answering.

## Limits

- The miner needs free video memory next to whatever is already loaded. With a 12 GB card and a model that holds
  11 GB it will not start; the bot then shows "no free memory". Nothing stops other services to make room.
- The first seconds of an LLM answer while mining are slow, until the guard stops the miner (poll: 2 s).
- SRBMiner keeps its overclock after exit. Every stop path resets it (`gpu-miner --reset`, also `ExecStopPost`).
- NVIDIA only (the guard talks to NVML through `libnvidia-ml.so.1`).
