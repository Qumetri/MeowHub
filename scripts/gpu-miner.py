#!/usr/bin/env python3
"""GPU miner switch + guard (docs/MINER.md). Runs as root: gpu-miner.service -> /usr/local/sbin/gpu-miner.

The owner (or a helper-bot admin) flips mining on and off from Telegram; this daemon does the work, because the
bot's container cannot (and must not) touch systemd or the GPU:

  helper/data/miner-want.json    written by the bot:  {"on": true, "by": <uid>, "ts": ...}
  helper/data/miner-status.json  written here every few seconds; the bot shows it

The RTX 3060 is shared. `llm_server` keeps its model in VRAM all the time, so "another process is on the card" can
no longer mean "busy" (it did in the first guard, October 2026, and the miner then never ran). Rules now:

  * a process from RESIDENT (llama-server) counts as busy only while it really computes (its GPU share >= BUSY_UTIL);
  * any other process on the card (Immich ML, NVENC, the upscaler, book2audio, ...) counts as busy while it exists;
  * busy            -> stop the miner at once, reset its overclock;
  * idle RESUME_AFTER seconds and the switch is on -> start the miner again;
  * the miner needs FREE_MB of free VRAM to start; without it the state is "no_vram" and nothing is started.

SRBMiner keeps its overclock after exit (memory clock locked at 810 MHz would cripple the LLM), so every stop path
ends with reset_oc(); prl-miner.service also runs `gpu-miner --reset` as ExecStopPost.

    gpu-miner            the daemon
    gpu-miner --status   what is on the GPU and what the guard would do
    gpu-miner --reset    put clocks / power limit back to stock
"""
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import time

DATA = os.environ.get('MINER_DATA', '/opt/meowstack/helper/data')     # the installer sets MINER_DATA in the unit
WANT, STATUS = os.path.join(DATA, 'miner-want.json'), os.path.join(DATA, 'miner-status.json')
MINER_UNIT = 'prl-miner.service'
MINER_LOG = '/opt/prl-miner/miner.log'
MINER_PROC = 'SRBMiner-MULTI'
POLL = float(os.environ.get('GUARD_POLL', 2))
RESUME_AFTER = float(os.environ.get('GUARD_RESUME_AFTER', 120))
BUSY_UTIL = int(os.environ.get('GUARD_BUSY_UTIL', 8))          # % of the GPU a resident process must use to count
FREE_MB = int(os.environ.get('GUARD_FREE_MB', 600))            # free VRAM the miner needs to start
RESIDENT = {'llama-server', *filter(None, os.environ.get('GUARD_RESIDENT', '').split(','))}
IGNORE = {'Xorg', 'gnome-shell', 'nautilus', 'Xwayland', MINER_PROC}
OWNER = (1000, 1000)                                           # status file owner: the bot's uid inside its container
GPU_INDEX = 0
_nvml = None


class ProcInfo(ctypes.Structure):          # nvmlProcessInfo_v2_t
    _fields_ = [('pid', ctypes.c_uint), ('usedGpuMemory', ctypes.c_ulonglong),
                ('gpuInstanceId', ctypes.c_uint), ('computeInstanceId', ctypes.c_uint)]


class ProcUtil(ctypes.Structure):          # nvmlProcessUtilizationSample_t
    _fields_ = [('pid', ctypes.c_uint), ('timeStamp', ctypes.c_ulonglong), ('smUtil', ctypes.c_uint),
                ('memUtil', ctypes.c_uint), ('encUtil', ctypes.c_uint), ('decUtil', ctypes.c_uint)]


class Memory(ctypes.Structure):
    _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong), ('used', ctypes.c_ulonglong)]


def nvml():
    global _nvml
    if _nvml is None:
        lib = ctypes.CDLL('libnvidia-ml.so.1')
        rc = lib.nvmlInit_v2()
        if rc:
            raise RuntimeError(f'nvmlInit failed: {rc}')
        _nvml = lib
    return _nvml


def handle():
    h = ctypes.c_void_p()
    rc = nvml().nvmlDeviceGetHandleByIndex_v2(GPU_INDEX, ctypes.byref(h))
    if rc:
        raise RuntimeError(f'nvmlDeviceGetHandleByIndex failed: {rc}')
    return h


def gpu_pids():
    lib, h, pids = nvml(), handle(), set()
    for fn in ('nvmlDeviceGetComputeRunningProcesses_v3', 'nvmlDeviceGetGraphicsRunningProcesses_v3'):
        n = ctypes.c_uint(64)
        buf = (ProcInfo * 64)()
        rc = getattr(lib, fn)(h, ctypes.byref(n), buf)
        if rc:
            raise RuntimeError(f'{fn} failed: {rc}')
        pids.update(buf[i].pid for i in range(n.value))
    return pids


def proc_util(since_us):
    """{pid: highest GPU share (sm/enc/dec, %) in the samples newer than `since_us`}; {} when there are none."""
    lib, h = nvml(), handle()
    n = ctypes.c_uint(128)
    buf = (ProcUtil * 128)()
    rc = lib.nvmlDeviceGetProcessUtilization(h, buf, ctypes.byref(n), ctypes.c_ulonglong(since_us))
    if rc:                                  # NVML_ERROR_NOT_FOUND (6) = no samples in the window
        return {}
    out = {}
    for i in range(n.value):
        s = buf[i]
        out[s.pid] = max(out.get(s.pid, 0), s.smUtil, s.encUtil, s.decUtil)
    return out


def proc_name(pid):
    try:
        with open(f'/proc/{pid}/comm') as f:
            return f.read().strip()
    except OSError:
        return f'pid{pid}'


def free_mb():
    m = Memory()
    return m.free // 2**20 if nvml().nvmlDeviceGetMemoryInfo(handle(), ctypes.byref(m)) == 0 else 0


def sensors():
    lib, h = nvml(), handle()
    t, p = ctypes.c_uint(), ctypes.c_uint()
    return (t.value if lib.nvmlDeviceGetTemperature(h, 0, ctypes.byref(t)) == 0 else None,
            round(p.value / 1000) if lib.nvmlDeviceGetPowerUsage(h, ctypes.byref(p)) == 0 else None)


def busy(window_s=6):
    """Names of the processes that need the GPU right now."""
    util = proc_util(int((time.time() - window_s) * 1e6))
    out = set()
    for pid in gpu_pids():
        name = proc_name(pid)
        if name in IGNORE:
            continue
        if name in RESIDENT:
            if util.get(pid, 0) >= BUSY_UTIL:
                out.add(name)
        else:
            out.add(name)
    return sorted(out)


def reset_oc():
    lib, h, out = nvml(), handle(), []
    for name, call in (
        ('mem lock', lambda: lib.nvmlDeviceResetMemoryLockedClocks(h)),
        ('core lock', lambda: lib.nvmlDeviceResetGpuLockedClocks(h)),
        ('core offset', lambda: lib.nvmlDeviceSetGpcClkVfOffset(h, ctypes.c_int(0))),
        ('mem offset', lambda: lib.nvmlDeviceSetMemClkVfOffset(h, ctypes.c_int(0))),
    ):
        rc = call()
        out.append(f'{name}={"ok" if rc == 0 else rc}')
    default = ctypes.c_uint()
    if lib.nvmlDeviceGetPowerManagementDefaultLimit(h, ctypes.byref(default)) == 0:
        rc = lib.nvmlDeviceSetPowerManagementLimit(h, default)
        out.append(f'power={default.value // 1000}W:{"ok" if rc == 0 else rc}')
    return ' '.join(out)


def systemctl(*args):
    return subprocess.run(['systemctl', *args], capture_output=True, text=True).returncode


def miner_active():
    return systemctl('is-active', '--quiet', MINER_UNIT) == 0


def log(msg):
    print(msg, flush=True)


def stop_miner(reason):
    systemctl('stop', MINER_UNIT)           # its ExecStopPost resets the overclock too
    log(f'miner stopped: {reason}; reset: {reset_oc()}')


def want():
    """The bot's switch. Anything unreadable means off: the file is written by a container."""
    try:
        with open(WANT) as f:
            d = json.load(f)
        return d.get('on') is True
    except (OSError, ValueError, AttributeError):
        return False


HASH = re.compile(r'Total: ([\d.]+) ([kMGT]?H/s).*?A:(\d+) R:(\d+)')


def miner_stats():
    """Last 'Total:' line of the miner's log: hashrate and accepted / rejected shares."""
    try:
        with open(MINER_LOG, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            tail = f.read().decode(errors='replace')
    except OSError:
        return {}
    m = None
    for m in HASH.finditer(tail):
        pass
    return {'hashrate': f'{m.group(1)} {m.group(2)}', 'accepted': int(m.group(3)), 'rejected': int(m.group(4))} if m else {}


def write_status(st):
    st['ts'] = int(time.time())
    tmp = STATUS + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump(st, f)
        os.chown(tmp, *OWNER)
        os.replace(tmp, STATUS)
    except OSError as e:
        log(f'status write failed: {e}')


def run():
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    last_busy = time.monotonic() - RESUME_AFTER + 10        # a short settle time after start
    since, state_was = time.time(), None
    log(f'guard up: poll {POLL}s, resume after {RESUME_AFTER:.0f}s idle, resident {sorted(RESIDENT)}, busy at {BUSY_UTIL}%')
    while not stopping:
        on = want()
        try:
            others = busy()
            free = free_mb()
            temp, power = sensors()
        except Exception as e:              # NVML hiccup (driver reload, GPU gone): fail safe
            others, free, temp, power = [f'nvml-error:{e}'], 0, None, None
        now, active = time.monotonic(), miner_active()
        if others:
            last_busy = now
        if not on:
            state, reason = 'off', ''
            if active:
                stop_miner('switched off')
        elif others:
            state, reason = 'paused', ', '.join(others)
            if active:
                stop_miner(reason)
        elif active:
            state, reason = 'mining', ''
        elif now - last_busy < RESUME_AFTER:
            state, reason = 'waiting', str(int(RESUME_AFTER - (now - last_busy)))
        elif free < FREE_MB:
            state, reason = 'no_vram', str(free)
        else:
            log('GPU idle, starting miner')
            rc = systemctl('start', MINER_UNIT)
            state, reason = ('mining', '') if rc == 0 else ('error', f'systemctl start: {rc}')
        if state != state_was:
            since, state_was = time.time(), state
            log(f'state: {state} {reason}')
        st = {'on': on, 'state': state, 'reason': reason, 'since': int(since), 'temp': temp, 'power': power, 'free_mb': free}
        if state == 'mining':
            st.update(miner_stats())
        write_status(st)
        time.sleep(POLL)
    if miner_active():
        stop_miner('guard stopping')
    write_status({'on': want(), 'state': 'guard_down', 'reason': '', 'since': int(time.time())})
    log('guard down')


def status():
    util = proc_util(int((time.time() - 6) * 1e6))
    for p in sorted(gpu_pids()):
        n = proc_name(p)
        kind = 'ignored' if n in IGNORE else 'resident' if n in RESIDENT else 'foreign'
        log(f'{p:>8}  {n}  ({kind}, gpu {util.get(p, 0)}%)')
    log(f'switch: {want()}, miner active: {miner_active()}, busy: {busy()}, free VRAM: {free_mb()} MiB, sensors: {sensors()}')


if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    if arg == '--reset':
        log(f'reset: {reset_oc()}')
    elif arg == '--status':
        status()
    else:
        run()
