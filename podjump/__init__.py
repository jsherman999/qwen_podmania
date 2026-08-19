"""qwen-podmania (podjump): a podman control plane for multi-OS jump-server labs.

Run multiple "server" containers (default: latest Ubuntu) that each expose an
SSH daemon for root. One container can act as the jump/bastion: generate a
root key pair on it, push its public key to the others, and hop around.

Interfaces: a localhost-only web UI (``podjump web``) and a CLI (``podjump``).
There is no external API surface.
"""

__version__ = "0.1.0"

APP_LABEL = "podjump.app"
DEFAULT_NETWORK = "podjump-net"
DEFAULT_SSH_PORT = 2022
DEFAULT_BASE_IMAGE = "podjump/server-ubuntu:latest"
