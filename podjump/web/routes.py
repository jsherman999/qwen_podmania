"""Localhost-only FastAPI app for the podjump web UI.

There is no public API surface: docs/OpenAPI are disabled, the server binds to
127.0.0.1 by default, and an optional shared token (PODJUMP_TOKEN) can be
required. Every endpoint is a thin wrapper over podjump.server.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import struct
import subprocess
import termios
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import core, podman_driver as drv, server as srv

STATIC = Path(__file__).parent / "static"


def _expected_token() -> Optional[str]:
    return os.environ.get("PODJUMP_TOKEN") or None


def _auth(provided: Optional[str]) -> None:
    expected = _expected_token()
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="invalid or missing token")


class ServerCreate(BaseModel):
    name: str
    image: Optional[str] = None
    env: Optional[dict] = None
    volumes: Optional[list[str]] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    ssh_host_port: Optional[int] = None
    start: bool = True


class PushReq(BaseModel):
    jump: str
    targets: list[str]


def _wrap(fn, *a, **k):
    try:
        return fn(*a, **k)
    except (core.PodmanError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def create_app() -> FastAPI:
    app = FastAPI(title="podjump", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health(tok: Optional[str] = Query(None)):
        _auth(tok)
        try:
            core.require_podman()
            podman_ok = True
            err = ""
        except core.PodmanError as exc:
            podman_ok = False
            err = str(exc)
        return {"ok": podman_ok, "error": err, "default_image": core.DEFAULT_BASE_IMAGE, "network": core.DEFAULT_NETWORK}

    @app.get("/api/image")
    def api_image_status(tok: Optional[str] = Query(None)):
        _auth(tok)
        return {
            "default_image": core.DEFAULT_BASE_IMAGE,
            "exists": drv.image_exists(core.DEFAULT_BASE_IMAGE),
        }

    @app.post("/api/image/build")
    def api_image_build(tok: Optional[str] = Query(None)):
        _auth(tok)
        return {"image": _wrap(drv.build_base_image)}

    @app.get("/api/servers")
    def api_list(tok: Optional[str] = Query(None)):
        _auth(tok)
        return {"servers": _wrap(srv.list_servers)}

    @app.post("/api/servers")
    def api_create(req: ServerCreate, tok: Optional[str] = Query(None)):
        _auth(tok)
        cfg = srv.ServerConfig(
            name=req.name,
            image=req.image or core.DEFAULT_BASE_IMAGE,
            env=req.env or {},
            volumes=req.volumes or [],
            cpu=req.cpu,
            memory=req.memory,
            ssh_host_port=req.ssh_host_port,
        )
        return _wrap(srv.create, cfg, start=req.start)

    @app.post("/api/servers/{name}/start")
    def api_start(name: str, tok: Optional[str] = Query(None)):
        _auth(tok)
        return _wrap(srv.start, name)

    @app.post("/api/servers/{name}/stop")
    def api_stop(name: str, tok: Optional[str] = Query(None)):
        _auth(tok)
        return _wrap(srv.stop, name)

    @app.delete("/api/servers/{name}")
    def api_remove(name: str, tok: Optional[str] = Query(None)):
        _auth(tok)
        return {"message": _wrap(srv.remove, name, force=True)}

    @app.post("/api/servers/{name}/keygen")
    def api_keygen(name: str, tok: Optional[str] = Query(None)):
        _auth(tok)
        return {"name": name, "public_key": _wrap(srv.keygen, name)}

    @app.post("/api/servers/push")
    def api_push(req: PushReq, tok: Optional[str] = Query(None)):
        _auth(tok)
        return _wrap(srv.push, req.jump, req.targets)

    @app.get("/api/servers/{name}/connect")
    def api_connect(name: str, tok: Optional[str] = Query(None)):
        _auth(tok)
        return _wrap(srv.connect, name)

    @app.get("/api/servers/{name}/logs")
    def api_logs(name: str, lines: int = Query(200), tok: Optional[str] = Query(None)):
        _auth(tok)
        return {"name": name, "logs": _wrap(srv.logs, name, lines)}

    # ------------------------------------------------------------------ #
    # Interactive terminal (PTY over websocket)
    # ------------------------------------------------------------------ #
    @app.websocket("/ws/terminal/{name}")
    async def ws_terminal(websocket: WebSocket, name: str):
        expected = _expected_token()
        if expected and websocket.query_params.get("token") != expected:
            await websocket.close(code=1008)
            return
        from .. import podman_driver as drv

        try:
            name = core.sanitize_name(name)
            if not drv.container_exists(name):
                raise core.PodmanError(f"server '{name}' does not exist")
            if drv.container_state(name) != "running":
                raise core.PodmanError(f"server '{name}' is not running")
        except (core.PodmanError, ValueError) as exc:
            await websocket.close(code=1008, reason=str(exc))
            return

        await websocket.accept()
        master, slave = pty.openpty()
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        try:
            proc = subprocess.Popen(
                [core.podman_bin(), "exec", "-it", name, "bash"],
                stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, preexec_fn=os.setsid,
            )
        except Exception as exc:  # noqa: BLE001
            os.close(master)
            await websocket.close(code=1011, reason=str(exc))
            return
        os.close(slave)
        loop = asyncio.get_running_loop()

        def proc_to_client() -> None:
            try:
                while True:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    asyncio.run_coroutine_threadsafe(websocket.send_bytes(data), loop)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=proc_to_client, daemon=True).start()

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    os.write(master, raw.encode())
                    continue
                t = obj.get("t")
                if t == "in":
                    os.write(master, str(obj.get("d", "")).encode())
                elif t == "resize":
                    rows = int(obj.get("rows") or 24)
                    cols = int(obj.get("cols") or 80)
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except (WebSocketDisconnect, Exception):  # noqa: BLE001
            pass
        finally:
            try:
                os.close(master)
            except OSError:
                pass
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    return app


def run(host: str = "127.0.0.1", port: int = 9090, open_browser: bool = False) -> None:
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}/"
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
