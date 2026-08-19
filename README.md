# podjump (qwen-podmania)

A **podman control plane** for building a multi-OS **jump-server lab**. podjump
lets you spin up multiple "servers" — one podman container each, defaulting to
the latest Ubuntu with an SSH daemon for root — on a shared podman network.
Pick one to be the jump/bastion: generate a root key pair on it, push its public
key to the others, and hop around.

Two interfaces, no external API surface:

- **Web UI** — a stylish, localhost-only single page (`podjump web`)
- **CLI** — full parity (`podjump ...`)

It talks to containers **through the `podman` binary only** (no podman socket or
REST API dependency), so the same code works on **macOS** (podman machine /
applehv) and **Linux**.

## How it works

- Each server = one podman container on the shared `podjump-net` network.
  Every container gets its own IP, so `sshd` on `:22` never collides and
  containers reach each other by name.
- The default image `podjump/server-ubuntu:latest` is **built once** from
  `ubuntu:latest` with `openssh-server` baked in (root, key-auth, `sshd -D` as
  the entrypoint). Override with any image via `--image` (it just needs to run
  an SSH daemon for root).
- The host publishes a free `127.0.0.1` port to each container's `:22`, so from
  your Mac/Linux host you connect with `ssh root@127.0.0.1 -p <port>`.
- **Jump flow:** `keygen <jump>` creates `/root/.ssh/id_ed25519` inside the jump
  container; `pushkey <jump> <targets...>` appends the jump's public key to each
  target's `/root/.ssh/authorized_keys` and verifies a passwordless hop.

## Install

Requires Python 3.10+ and `podman` on PATH (on macOS: `podman machine start`).

```bash
cd podjump
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

Build the reusable server image once:

```bash
podjump build
```

## Web UI

```bash
podjump web --open            # http://127.0.0.1:8080
PODJUMP_TOKEN=secret podjump web   # require ?token=secret
```

The UI: create/rename/configure servers (env, volumes, CPU, memory, port),
start/stop, open a live **terminal** (PTY over websocket) or **logs**, run
`keygen`, and run the **jump-server key push** with per-target verification.

## CLI

```bash
podjump doctor                       # sanity-check podman + env
podjump build                        # build the Ubuntu+sshd base image
podjump create web-1                 # create + start (auto port)
podjump create db-1 --env FOO=bar --volume /tmp/data:/data -m 512m --port 2030
podjump servers                      # list
podjump start db-1 / podjump stop db-1
podjump logs web-1 -n 100
podjump connect web-1                # print the ssh command

# jump-server flow
podjump keygen jump-1                # generate a root key pair, print pubkey
podjump pushkey jump-1 web-1 db-1    # push pubkey to targets + verify the hop

podjump rm web-1 -y
```

## Deployment

- **Source** (recommended): `pip install -e .` on any macOS/Linux box with
  podman.
- **Single binary** (optional): `pip install -e .[package]` then
  `pyinstaller --onefile --name podjump --collect-all podjump podjump/cli.py`
  to produce a self-contained executable per OS.

## Security notes

- The web server binds to `127.0.0.1` by default; there is no public REST API.
  Pass `--host` to bind wider (you'll be warned), and set `PODJUMP_TOKEN` to add
  a shared token.
- Containers run root with SSH key auth only (no password). Treat the lab as
  untrusted.
- State (ports, generated public keys) is stored in `~/.podjump/state.json` —
  outside the repo, so no secrets are committed.

## Layout

```
podjump/
  pyproject.toml
  image/                # Dockerfile + entrypoint.sh -> the Ubuntu+sshd server image
  podjump/
    core.py             # podman CLI runner, state, ports, name sanitising
    podman_driver.py    # low-level podman operations
    server.py           # high-level lifecycle + jump-server orchestration
    cli.py              # Typer CLI
    web/
      routes.py         # FastAPI app (localhost-only) + PTY websocket terminal
      static/index.html # the web UI
```
