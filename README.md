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
- **Master toggle (macOS):** containers run inside a podman *machine* (a small
  VM). Bring it up or down with `podjump up` / `podjump down`, the power toggle
  in the web header, or let the bundled launchd agent start it automatically at
  login. Stopping the machine stops all containers but leaves the web UI up.

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
podjump web --open                      # binds 0.0.0.0:9090 — reachable from your LAN
PODJUMP_TOKEN=secret podjump web --open # require ?token=secret (recommended for LAN)
podjump web --host 127.0.0.1            # this machine only
```

The UI: create/rename/configure servers (env, volumes, CPU, memory, port),
start/stop, open a live **terminal** (PTY over websocket) or **logs**, run
`keygen`, and run the **jump-server key push** with per-target verification.

## CLI

```bash
podjump doctor                       # sanity-check podman + env
podjump up                           # master ON  — bring the podman machine (VM) up
podjump down                         # master OFF — stop the podman machine (VM)
podjump machine                      # show machine (VM) state
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

## Run as a service (macOS)

Two bundled launchd agents keep the lab alive across reboots. The labels and
paths are for this machine (`/Users/jay/podjump`); edit them if you install
elsewhere:

- `packaging/launchd/com.jay.podjump.plist` — the web UI. `KeepAlive` restarts
  it if it ever dies.
- `packaging/launchd/com.jay.podjump-machine.plist` — runs `podjump up` at login
  so the podman machine (VM) is up after a reboot. It's a one-shot (no
  `KeepAlive`) and is idempotent, so it's a no-op when the machine is already up.

```bash
cp packaging/launchd/com.jay.podjump*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jay.podjump.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jay.podjump-machine.plist
```

Manage:

```bash
launchctl kickstart -k gui/$(id -u)/com.jay.podjump    # restart the web UI
launchctl bootout    gui/$(id -u)/com.jay.podjump      # stop the web UI
tail -f /Users/jay/podjump/logs/podjump-web.log        # watch logs
```

## Security notes

- The web server binds to `0.0.0.0` by default, so it is reachable from other
  machines on your LAN (there is still no public REST API — only the endpoints
  the UI calls). Use `--host 127.0.0.1` to restrict it to this machine. Because
  the UI can create/destroy containers and exec as root, set `PODJUMP_TOKEN` and
  pass `?token=…` when exposing it to the LAN.
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
      routes.py         # FastAPI app (LAN by default) + PTY websocket terminal
      static/index.html # the web UI
```
