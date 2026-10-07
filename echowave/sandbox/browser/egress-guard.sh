#!/bin/sh
# Host-side egress guard for Decibyl's browser boxes. Run once as root on the
# Docker host (and from the host's boot scripts), after the sandbox service
# has created the browser network (it does so on start; see ../server.py).
#
# The box's own proxy (netguard.py) already refuses private addresses for
# every request the browser makes. This is the second wall, for anything in
# the box that is not the browser: from the browser subnet, nothing private,
# nothing link-local (the cloud metadata endpoint), no other container.
set -eu
SUBNET="${SANDBOX_BROWSER_SUBNET:-172.30.240.0/24}"
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 127.0.0.0/8 0.0.0.0/8 224.0.0.0/4; do
  iptables -C DOCKER-USER -s "$SUBNET" -d "$net" -j DROP 2>/dev/null \
    || iptables -I DOCKER-USER -s "$SUBNET" -d "$net" -j DROP
done
echo "browser egress guard in place for $SUBNET"
