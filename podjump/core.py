"""Low-level helpers: podman CLI execution, state, ports, name sanitising."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

APP_LABEL = "podjump.app"
DEFAULT_NETWORK = "podjump-net"
DEFAULT_SSH_PORT = 2022
DEFAULT_BASE_IMAGE = "podjump/server-ubuntu:latest"


class PodmanError(RuntimeError):
    """A podman command failed or could not be run."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "", returncode: Optional[int] = None):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def podman_bin() -> str:
    return os.environ.get("PODJUMP_PODMAN_BIN", "podman")


def run(
    cmd: list[str],
    *,
    input: Optional[str] = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess:
    """Run a command (list form, no shell) and return the CompletedProcess."""
    try:
        return subprocess.run(
            cmd,
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=os.environ,
        )
    except FileNotFoundError as exc:
        raise PodmanError(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PodmanError(f"timed out after {timeout}s: {' '.join(cmd)}") from exc


def check(cmd: list[str], *, input: Optional[str] = None, timeout: float = 60) -> str:
    proc = run(cmd, input=input, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise PodmanError(
            f"{' '.join(cmd)} failed (rc={proc.returncode}): {detail}",
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
    return proc.stdout


def podman(*args: str, **kw: Any) -> str:
    """Run a podman subcommand, return stdout text."""
    return check([podman_bin(), *args], **kw)


def podman_ok(cmd: list[str]) -> bool:
    """Run a command, return True iff it exited 0 (used for exists-style checks)."""
    return run(cmd).returncode == 0


def podman_json(*args: str, **kw: Any):
    """Run a podman subcommand that prints JSON, return parsed object."""
    out = podman(*args, **kw).strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise PodmanError(f"could not parse JSON from {' '.join(args)}:\n{out}") from exc


def sanitize_name(name: str) -> str:
    n = re.sub(r"[^a-zA-Z0-9._-]", "-", (name or "").strip())
    n = n.strip("-._")
    if not n:
        raise ValueError("empty server name")
    if n.lower() in {"all", "none", "latest"}:
        raise ValueError(f"reserved server name: {name}")
    return n[:63]


def find_free_host_port(prefer: Optional[int] = None, lo: int = 2000, hi: int = 65000) -> int:
    """Find a free TCP port on 127.0.0.1, preferring `prefer` if it is free."""
    order: list[int] = []
    if prefer:
        order.append(int(prefer))
    order += list(range(lo, hi))
    seen: set[int] = set()
    for p in order:
        if p in seen:
            continue
        seen.add(p)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise PodmanError("no free host port found")


# --------------------------------------------------------------------------- #
# State (kept out of the repo, in $HOME so no secrets ever get committed)
# --------------------------------------------------------------------------- #
def state_dir() -> Path:
    d = Path(os.environ.get("PODJUMP_HOME", str(Path.home() / ".podjump")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file() -> Path:
    return state_dir() / "state.json"


def load_state() -> dict:
    p = state_file()
    if not p.exists():
        return {"servers": {}}
    try:
        data = json.loads(p.read_text())
        data.setdefault("servers", {})
        return data
    except Exception:
        return {"servers": {}}


def save_state(state: dict) -> None:
    p = state_file()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def require_podman() -> str:
    """Return the podman binary path or raise a helpful error."""
    import shutil

    binpath = podman_bin()
    if shutil.which(binpath) is None:
        raise PodmanError(
            f"podman binary '{binpath}' not found on PATH. "
            "Install podman (or set PODJUMP_PODMAN_BIN)."
        )
    return binpath


def is_linux() -> bool:
    return os.uname().sysname.lower() == "linux"
