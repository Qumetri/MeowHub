#!/bin/bash
# Bring awg0 up, then idle until signalled so the container's lifecycle == the tunnel's.
set -e
IFACE=awg0
down() { awg-quick down "$IFACE" >/dev/null 2>&1 || true; exit 0; }
trap down TERM INT
awg-quick down "$IFACE" >/dev/null 2>&1 || true
# /run/amneziawg is a named volume shared with the web UI, so a socket can survive a
# hard kill. Only we ever create it, so an orphan here is always ours and always stale.
rm -f "/run/amneziawg/$IFACE.sock"
awg-quick up "$IFACE"
echo "[entrypoint] $IFACE up on udp/$(awg show $IFACE listen-port); $(awg show $IFACE peers | wc -l) peer(s)"
# amneziawg-go daemonises, so hold the container open and watch the interface
while ip link show "$IFACE" >/dev/null 2>&1; do sleep 15 & wait $!; done
echo "[entrypoint] $IFACE disappeared, exiting so Docker restarts us" >&2
exit 1
