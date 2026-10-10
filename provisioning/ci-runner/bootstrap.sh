#!/usr/bin/env bash
# Operator-only provisioning inside a disposable Ubuntu 24.04 CI VM.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Root required for guest provisioning' >&2; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]]
account=hapsic-ci
runner_root=/opt/hapsic-ci-runner
if ! id "$account" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/hapsic-ci --shell /usr/sbin/nologin "$account"
fi
[[ $(id -Gn "$account") == "$account" ]] || { echo 'Unexpected runner groups' >&2; exit 1; }
install -d -m 0700 -o "$account" -g "$account" "$runner_root" /var/lib/hapsic-ci
install -d -m 0755 -o "$account" -g "$account" /opt/hapsic-ci-toolcache
apt-get update -qq
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y --no-install-recommends \
  ca-certificates curl git jq unzip zstd build-essential cmake ccache pkg-config \
  libicu74 libssl3t64 libdrm-dev libcap-dev libsystemd-dev libcurl4-openssl-dev libssl-dev zlib1g-dev
if [[ -e $runner_root/config.sh ]]; then
  echo 'Existing runner preserved; no registration or service change performed'
  exit 0
fi
archive=$(mktemp -d /tmp/hapsic-ci-bootstrap.XXXXXXXX)
chmod 0755 "$archive"
curl --fail --location --proto '=https' --tlsv1.2 \
  https://github.com/actions/runner/releases/download/v2.337.0/actions-runner-linux-x64-2.337.0.tar.gz \
  --output "$archive/runner.tar.gz"
echo '70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613  '"$archive/runner.tar.gz" | sha256sum --check
chmod 0644 "$archive/runner.tar.gz"
runuser -u "$account" -- tar xzf "$archive/runner.tar.gz" -C "$runner_root" --no-same-owner
echo "Verified archive retained at $archive/runner.tar.gz"
echo 'Register with a short-lived token; install hardening before starting.'
