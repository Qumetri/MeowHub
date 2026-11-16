#!/bin/bash
# Installs the Kryptex Pearl miner + its guard (docs/MINER.md). Run as:  sudo scripts/gpu-miner-install.sh
#   - SRBMiner-MULTI 3.8.2 from the author's GitHub release, checked against the release's sha256
#   - /usr/local/sbin/gpu-miner (copy of scripts/gpu-miner.py; reinstall after editing), units gpu-miner + prl-miner
# Nothing mines until somebody turns the switch on in the helper bot (/miner). Remove with: --remove
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VER=3.8.2; SUM=3635531f022bc023b5111792be4e91cb8d940f8fb9e5d133bbc5f9f15e2df0f5
if [ "${1:-}" = "--remove" ]; then
  systemctl disable --now gpu-miner.service 2>/dev/null || true
  systemctl stop prl-miner.service 2>/dev/null || true
  /usr/local/sbin/gpu-miner --reset 2>/dev/null || true
  rm -f /etc/systemd/system/gpu-miner.service /etc/systemd/system/prl-miner.service /usr/local/sbin/gpu-miner
  rm -rf /opt/prl-miner "$HERE/../helper/data/miner-status.json"
  systemctl daemon-reload; echo removed; exit 0
fi
if [ ! -x /opt/prl-miner/srbminer/SRBMiner-MULTI ]; then
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  curl -fsSL -o "$T/srb.tar.gz" "https://github.com/doktor83/SRBMiner-Multi/releases/download/$VER/SRBMiner-Multi-${VER//./-}-Linux.tar.gz"
  echo "$SUM  $T/srb.tar.gz" | sha256sum -c -
  mkdir -p /opt/prl-miner; tar xzf "$T/srb.tar.gz" -C /opt/prl-miner
  mv "/opt/prl-miner/SRBMiner-Multi-${VER//./-}" /opt/prl-miner/srbminer
  chown -R root:root /opt/prl-miner; chmod 755 /opt/prl-miner/srbminer /opt/prl-miner/srbminer/SRBMiner-MULTI
fi
install -m 755 -o root -g root "$HERE/gpu-miner.py" /usr/local/sbin/gpu-miner
grep -q YOUR_KRYPTEX_WALLET "$HERE/prl-miner.service" && { echo "Set your wallet in scripts/prl-miner.service first"; exit 1; }
install -m 644 "$HERE/prl-miner.service" /etc/systemd/system/
sed "s#__HELPER_DATA__#$(cd "$HERE/../helper/data" && pwd)#" "$HERE/gpu-miner.service" > /etc/systemd/system/gpu-miner.service
systemctl daemon-reload
systemctl enable --now gpu-miner.service
sleep 3; /usr/local/sbin/gpu-miner --status
echo "Installed. The switch is in the helper bot: /miner"
