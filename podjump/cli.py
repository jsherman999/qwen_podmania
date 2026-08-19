"""podjump CLI — full parity with the web UI, driven through podjump.server."""
from __future__ import annotations

from typing import List, Optional

import typer

from . import core, podman_driver as drv, server as srv
from .web import routes as webroutes

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="podjump — run multiple Ubuntu SSH servers in a podman jump-server lab.",
)


def _fail(e: Exception) -> None:
    typer.secho(str(e), fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Show the version."""
    from . import __version__

    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check podman is available and report the environment."""
    try:
        core.require_podman()
        v = core.podman("version", "--format", "{{.Version}}").strip()
        typer.secho(f"podman {v} — ok", fg=typer.colors.GREEN)
    except core.PodmanError as exc:
        _fail(exc)
    if core.is_linux():
        typer.echo("platform: linux (native podman)")
    else:
        typer.echo("platform: non-linux (expects a `podman machine` VM)")
    typer.echo(f"default image : {core.DEFAULT_BASE_IMAGE}")
    typer.echo(f"image present : {drv.image_exists(core.DEFAULT_BASE_IMAGE)}")
    typer.echo(f"network       : {core.DEFAULT_NETWORK}")


@app.command()
def build() -> None:
    """Build the reusable Ubuntu+sshd base image."""
    try:
        image = drv.build_base_image()
        typer.secho(f"built {image}", fg=typer.colors.GREEN)
    except core.PodmanError as exc:
        _fail(exc)


@app.command()
def servers() -> None:
    """List servers."""
    try:
        rows = srv.list_servers()
    except core.PodmanError as exc:
        _fail(exc)
    if not rows:
        typer.echo("no servers yet — create one with: podjump create <name>")
        return
    header = f"{'NAME':<18} {'STATE':<10} {'IP':<15} {'SSH PORT':<9} IMAGE"
    typer.echo(header)
    typer.echo("-" * len(header))
    for s in rows:
        typer.echo(
            f"{s['name']:<18} {str(s.get('state')):<10} {str(s.get('ip') or '—'):<15} "
            f"{str(s.get('ssh_host_port') or '—'):<9} {s.get('image') or '?'}"
        )


@app.command()
def create(
    name: str = typer.Argument(..., help="server name"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="image (default: built Ubuntu+sshd)"),
    env: List[str] = typer.Option([], "--env", "-e", help="KEY=value (repeatable)"),
    volume: List[str] = typer.Option([], "--volume", "-v", help="host:container (repeatable)"),
    cpu: Optional[str] = typer.Option(None, "--cpu"),
    memory: Optional[str] = typer.Option(None, "--memory", "-m"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="host port for SSH (default: auto)"),
    no_start: bool = typer.Option(False, "--no-start"),
) -> None:
    """Create (and by default start) a new server."""
    envd = {}
    for kv in env:
        if "=" not in kv:
            _fail(f"invalid --env '{kv}' (want KEY=value)")
        k, v = kv.split("=", 1)
        envd[k] = v
    cfg = srv.ServerConfig(
        name=name,
        image=image or core.DEFAULT_BASE_IMAGE,
        env=envd,
        volumes=volume,
        cpu=cpu,
        memory=memory,
        ssh_host_port=port,
    )
    try:
        s = srv.create(cfg, start=not no_start)
    except (core.PodmanError, ValueError) as exc:
        _fail(exc)
    typer.secho(f"created {s['name']} ({s.get('state')})", fg=typer.colors.GREEN)
    _print_connect(s)


def _print_connect(s: dict) -> None:
    port = s.get("ssh_host_port")
    if port:
        typer.secho(f"  ssh  : ssh root@127.0.0.1 -p {port}", fg=typer.colors.CYAN)
    if s.get("ip"):
        typer.echo(f"  ip   : {s['ip']}")


@app.command()
def start(name: str = typer.Argument(...)) -> None:
    """Start a server."""
    try:
        s = srv.start(name)
    except core.PodmanError as exc:
        _fail(exc)
    typer.secho(f"started {s['name']}", fg=typer.colors.GREEN)


@app.command()
def stop(name: str = typer.Argument(...)) -> None:
    """Stop a server."""
    try:
        s = srv.stop(name)
    except core.PodmanError as exc:
        _fail(exc)
    typer.secho(f"stopped {s['name']}", fg=typer.colors.GREEN)


@app.command("rm")
def remove(name: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Remove a server (container)."""
    if not yes and not typer.confirm(f"Remove server '{name}'?"):
        raise typer.Exit()
    try:
        msg = srv.remove(name, force=True)
    except core.PodmanError as exc:
        _fail(exc)
    typer.secho(msg, fg=typer.colors.GREEN)


@app.command()
def keygen(name: str = typer.Argument(...)) -> None:
    """Generate a root SSH key pair on the server and print its public key."""
    try:
        pub = srv.keygen(name)
    except core.PodmanError as exc:
        _fail(exc)
    typer.secho(f"{name} public key:\n", fg=typer.colors.CYAN)
    typer.echo(pub)


@app.command()
def pushkey(
    jump: str = typer.Argument(..., help="jump/bastion server (gets the key pair)"),
    target: List[str] = typer.Argument(..., help="one or more target servers"),
) -> None:
    """Make `jump` the bastion: generate a key pair and push its pubkey to each target."""
    try:
        r = srv.push(jump, target)
    except (core.PodmanError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"jump : {r['jump']}")
    typer.echo(f"pub  : {r['public_key']}")
    for x in r["results"]:
        status = "ok " if x["verified"] else "!! "
        typer.secho(f"  {status}{x['target']}: pushed={x['key_pushed']} verified={x['verified']}"
                    f"{'  (' + x['detail'] + ')' if x['detail'] and not x['verified'] else ''}",
                    fg=typer.colors.GREEN if x["verified"] else typer.colors.YELLOW)


@app.command()
def connect(name: str = typer.Argument(...)) -> None:
    """Show how to connect to a server from this host."""
    try:
        c = srv.connect(name)
    except core.PodmanError as exc:
        _fail(exc)
    if not c.get("ssh_command"):
        _fail("server has no published SSH port or is not running")
    typer.secho(c["ssh_command"], fg=typer.colors.CYAN)
    typer.echo(f"  (ip: {c.get('ip') or '—'}, state: {c.get('state')})")


@app.command()
def logs(name: str = typer.Argument(...), lines: int = typer.Option(200, "--lines", "-n")) -> None:
    """Tail a server's logs."""
    try:
        out = srv.logs(name, lines)
    except core.PodmanError as exc:
        _fail(exc)
    typer.echo(out.rstrip() or "(no logs)")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="bind address (keep localhost for no public surface)"),
    port: int = typer.Option(9090, "--port", "-p"),
    open_browser: bool = typer.Option(False, "--open"),
) -> None:
    """Run the local web UI."""
    if host not in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
        typer.secho(f"warning: binding to {host} exposes the UI beyond localhost", fg=typer.colors.YELLOW)
    try:
        webroutes.run(host=host, port=port, open_browser=open_browser)
    except core.PodmanError as exc:
        _fail(exc)


if __name__ == "__main__":
    app()
