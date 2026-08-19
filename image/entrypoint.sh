#!/usr/bin/env bash
# podjump server entrypoint: make sshd run for root and stay in the foreground.
set -euo pipefail

mkdir -p /run/sshd /root/.ssh
chmod 700 /root/.ssh

# Optional: allow a password as a bootstrap (a real key pair is the intended
# path; set ROOT_SSH_PASSWORD env on the container to enable it).
if [ -n "${ROOT_SSH_PASSWORD:-}" ]; then
    echo "root:${ROOT_SSH_PASSWORD}" | chpasswd
fi

# Optional: seed authorized_keys so you can log in before the jump flow.
if [ -n "${ROOT_SSH_AUTHORIZED_KEYS:-}" ]; then
    mkdir -p /root/.ssh
    printf '%s\n' "${ROOT_SSH_AUTHORIZED_KEYS}" > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi

echo "[podjump] starting sshd on :22 as root (key auth)"
exec /usr/sbin/sshd -D -e
