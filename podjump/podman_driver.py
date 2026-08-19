"""Thin, focused wrapper around the podman CLI for the operations we need.

Everything is driven through the `podman` binary over subprocess — no podman
socket or REST API dependency, so the same code works on macOS (podman
machine) and Linux.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import core


def image_exists(image: str) -> bool:
    return core.podman_ok([core.podman_bin(), "image", "exists", image])


def pull(image: str) -> str:
    core.podman("pull", image, timeout=600)
    return image


def build_base_image() -> str:
    """Build the reusable Ubuntu+sshd server image from ./image."""
    ctx = Path(__file__).resolve().parent.parent / "image"
    if not (ctx / "Dockerfile").exists():
        raise core.PodmanError(f"no Dockerfile at {ctx}")
    core.podman("build", "-t", core.DEFAULT_BASE_IMAGE, str(ctx), timeout=900)
    return core.DEFAULT_BASE_IMAGE


def ensure_network(name: str = core.DEFAULT_NETWORK) -> str:
    if not core.podman_ok([core.podman_bin(), "network", "exists", name]):
        core.podman("network", "create", name)
    return name


def container_exists(name: str) -> bool:
    out = core.podman("ps", "-aq", "--filter", f"name=^{name}$").strip()
    return bool(out)


def create_container(
    name: str,
    image: str,
    *,
    network: str,
    ssh_host_port: int,
    env: Optional[dict] = None,
    volumes: Optional[list[str]] = None,
    cpu: Optional[str] = None,
    memory: Optional[str] = None,
) -> None:
    ensure_network(network)
    args = [
        "container", "create",
        "--name", name,
        "--label", f"{core.APP_LABEL}=1",
        "--label", f"{core.APP_LABEL}.ssh_port={ssh_host_port}",
        "--network", network,
        "-p", f"127.0.0.1:{ssh_host_port}:22",
        "--hostname", name,
    ]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    for v in volumes or []:
        args += ["-v", v]
    if cpu:
        args += ["--cpus", str(cpu)]
    if memory:
        args += ["--memory", str(memory)]
    # The image ENTRYPOINT runs sshd; we do not override the command.
    args += [image]
    core.podman(*args, timeout=300)


def start(name: str) -> None:
    core.podman("start", name)


def stop(name: str) -> None:
    core.podman("stop", name, "-t", "10")


def remove(name: str, *, force: bool = True) -> None:
    args = ["rm"]
    if force:
        args.append("-f")
    args.append(name)
    core.podman(*args)


def container_state(name: str) -> str:
    out = core.podman(
        "inspect", "--format", "{{.State.Status}}", name
    ).strip()
    return out or "unknown"


def container_ip(name: str, network: str = core.DEFAULT_NETWORK) -> Optional[str]:
    tpl = '{{(index .NetworkSettings.Networks "%s").IPAddress}}' % network
    try:
        out = core.podman("inspect", "--format", tpl, name).strip()
    except core.PodmanError:
        return None
    return out or None


def list_servers() -> list[dict]:
    out = core.podman(
        "ps", "-a", "--filter", f"label={core.APP_LABEL}", "--format", "{{json .}}"
    )
    servers: list[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = _parse_json_line(line)
        except Exception:
            continue
        labels = c.get("Labels", {}) or {}
        names = c.get("Names", []) or []
        name = (names[0] if names else c.get("Name", "")).lstrip("/")
        if not name:
            continue
        servers.append(
            {
                "name": name,
                "image": c.get("Image"),
                "state": c.get("State"),
                "status": c.get("Status"),
                "ssh_host_port": _parse_port(labels.get(f"{core.APP_LABEL}.ssh_port")),
                "created": c.get("Created"),
            }
        )
    servers.sort(key=lambda s: s.get("created") or 0, reverse=True)
    return servers


def _parse_json_line(line: str):
    import json

    return json.loads(line)


def _parse_port(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def logs(name: str, lines: int = 200) -> str:
    return core.podman("logs", "--tail", str(max(1, lines)), name, timeout=60)


# --- in-container exec ------------------------------------------------------ #
def exec_in(name: str, command: list[str], *, pty: bool = False, env: Optional[dict] = None) -> "subprocess.CompletedProcess":
    args = ["exec"]
    if pty:
        args.append("-it")
    else:
        args.append("-i")
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    args += [name, *command]
    return core.run([core.podman_bin(), *args], timeout=300)


# --- SSH jump-server primitives -------------------------------------------- #
def keygen(name: str) -> str:
    """Generate a root SSH key pair inside the container; return the public key."""
    script = (
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
        "if [ ! -f /root/.ssh/id_ed25519 ]; then "
        "ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_ed25519 -C podjump; "
        "fi && test -f /root/.ssh/id_ed25519"
    )
    res = exec_in(name, ["bash", "-lc", script])
    if res.returncode != 0:
        raise core.PodmanError(f"keygen failed on {name}: {res.stderr.strip() or res.stdout.strip()}")
    pub = exec_in(name, ["cat", "/root/.ssh/id_ed25519.pub"]).stdout.strip()
    if not pub:
        raise core.PodmanError(f"no public key found on {name}")
    return pub


def push_key_to(name: str, pubkey: str) -> None:
    """Append a public key to root's authorized_keys in the container (idempotent)."""
    script = (
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
        "touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && "
        'grep -qxF "$PUBKEY" /root/.ssh/authorized_keys || printf "%s\\n" "$PUBKEY" >> /root/.ssh/authorized_keys'
    )
    res = exec_in(name, ["bash", "-lc", script], env={"PUBKEY": pubkey})
    if res.returncode != 0:
        raise core.PodmanError(f"push key to {name} failed: {res.stderr.strip() or res.stdout.strip()}")


def verify_ssh(from_name: str, target_ip: str) -> tuple[bool, str]:
    """From `from_name`, try a passwordless SSH to root@target_ip:22."""
    script = (
        "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        "-o BatchMode=yes -o ConnectTimeout=6 root@%(ip)s 'echo PODJUMP_OK' 2>/dev/null"
    ) % {"ip": target_ip}
    res = exec_in(from_name, ["bash", "-lc", script])
    ok = "PODJUMP_OK" in res.stdout
    detail = (res.stdout or res.stderr).strip()
    return ok, detail
