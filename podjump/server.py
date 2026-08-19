"""High-level server lifecycle + jump-server orchestration.

Both the web UI and the CLI call into this module so their behaviour stays
identical. A "server" is one podman container running an SSH daemon for root
on a shared podman network; any server can become the jump/bastion by pushing
its public key to the others.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from . import core, podman_driver as drv


@dataclass
class ServerConfig:
    name: str
    image: str = core.DEFAULT_BASE_IMAGE
    env: dict = field(default_factory=dict)
    volumes: list = field(default_factory=list)
    cpu: Optional[str] = None
    memory: Optional[str] = None
    ssh_host_port: Optional[int] = None


def list_servers() -> list[dict]:
    out = []
    for s in drv.list_servers():
        s = dict(s)
        s["ip"] = drv.container_ip(s["name"]) if s.get("state") == "running" else None
        out.append(s)
    return out


def get(name: str) -> dict:
    name = core.sanitize_name(name)
    if not drv.container_exists(name):
        raise core.PodmanError(f"server '{name}' does not exist")
    s = next((x for x in drv.list_servers() if x["name"] == name), None)
    if s is None:  # exists but not labelled; build a minimal record
        s = {"name": name, "image": None, "state": None, "status": None, "ssh_host_port": None, "created": None}
        s["state"] = drv.container_state(name)
    s["ip"] = drv.container_ip(name) if s.get("state") == "running" else None
    return s


def _ensure_image(image: str) -> None:
    if drv.image_exists(image):
        return
    if image == core.DEFAULT_BASE_IMAGE:
        drv.build_base_image()
    else:
        drv.pull(image)


def create(cfg: ServerConfig, *, start: bool = True) -> dict:
    name = core.sanitize_name(cfg.name)
    if drv.container_exists(name):
        raise core.PodmanError(f"server '{name}' already exists")

    image = cfg.image or core.DEFAULT_BASE_IMAGE
    _ensure_image(image)
    port = int(cfg.ssh_host_port or core.find_free_host_port())

    drv.create_container(
        name,
        image,
        network=core.DEFAULT_NETWORK,
        ssh_host_port=port,
        env=cfg.env,
        volumes=cfg.volumes,
        cpu=cfg.cpu,
        memory=cfg.memory,
    )

    state = core.load_state()
    state["servers"][name] = {
        "image": image,
        "ssh_host_port": port,
        "env": cfg.env,
        "volumes": cfg.volumes,
        "cpu": cfg.cpu,
        "memory": cfg.memory,
        "created": time.time(),
    }
    core.save_state(state)

    if start:
        drv.start(name)
    return get(name)


def start(name: str) -> dict:
    name = core.sanitize_name(name)
    _require(name)
    drv.start(name)
    return get(name)


def stop(name: str) -> dict:
    name = core.sanitize_name(name)
    _require(name)
    drv.stop(name)
    return get(name)


def remove(name: str, *, force: bool = True) -> str:
    name = core.sanitize_name(name)
    _require(name)
    drv.remove(name, force=force)
    state = core.load_state()
    state["servers"].pop(name, None)
    core.save_state(state)
    return f"removed {name}"


def _require(name: str) -> None:
    if not drv.container_exists(name):
        raise core.PodmanError(f"server '{name}' does not exist")


def keygen(name: str) -> str:
    """Generate (or reuse) a root key pair on the server; return the public key."""
    name = core.sanitize_name(name)
    _require(name)
    _running(name)
    pub = drv.keygen(name)
    state = core.load_state()
    state["servers"].setdefault(name, {})["pubkey"] = pub
    core.save_state(state)
    return pub


def push(jump: str, targets: list[str]) -> dict:
    """Make `jump` a bastion: ensure a key pair, push its pubkey to each target."""
    jump = core.sanitize_name(jump)
    targets = [core.sanitize_name(t) for t in targets]
    if not targets:
        raise ValueError("no targets given")
    _require(jump)
    _running(jump)

    pub = drv.keygen(jump)
    results = []
    for t in targets:
        entry = {"target": t, "key_pushed": False, "verified": False, "detail": ""}
        try:
            _require(t)
            _running(t)
            drv.push_key_to(t, pub)
            entry["key_pushed"] = True
            ok, detail = drv.verify_ssh(jump, drv.container_ip(t) or "")
            entry["verified"] = ok
            entry["detail"] = detail or ("verified" if ok else "connect failed")
        except core.PodmanError as exc:
            entry["detail"] = str(exc)
        results.append(entry)

    state = core.load_state()
    state["servers"].setdefault(jump, {})["pubkey"] = pub
    core.save_state(state)
    return {"jump": jump, "public_key": pub, "results": results}


def connect(name: str) -> dict:
    s = get(name)
    port = s.get("ssh_host_port")
    return {
        "name": s["name"],
        "ip": s.get("ip"),
        "ssh_host_port": port,
        "ssh_command": f"ssh root@127.0.0.1 -p {port}" if port else None,
        "state": s.get("state"),
    }


def logs(name: str, lines: int = 200) -> str:
    name = core.sanitize_name(name)
    _require(name)
    return drv.logs(name, lines)


def _running(name: str) -> None:
    if drv.container_state(name) != "running":
        raise core.PodmanError(f"server '{name}' is not running (state: {drv.container_state(name)})")
