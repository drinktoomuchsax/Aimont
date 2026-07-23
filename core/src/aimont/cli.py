"""CLI: daemon, status, watch, test commands."""

from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

app = typer.Typer(name="aimont", help="Human-in-the-loop state broadcast for Claude Code.")


def _version_callback(value: bool) -> None:
    if value:
        from aimont import __version__

        typer.echo(f"aimont {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the aimont version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Aimont CLI."""


STATE_ICONS = {
    "off": "  ",
    "idle": "💤",
    "working": "🔵",
    "tool_active": "⚙️ ",
    "awaiting_input": "🟡",
    "awaiting_permission": "🟣",
    "notification": "🔔",
    "error": "🔴",
}


def _state_name(value) -> str:
    """Render a state that may arrive as an int (enum value) or a name."""
    if isinstance(value, int):
        from aimont.models import AimontState

        return AimontState(value).name.lower()
    return str(value)


def format_watch_frame(frame: dict) -> str:
    """Format a frame for the `aimont watch` CLI. Handles session, aggregate,
    and presence frame types (presence arrives in mode=all)."""
    from datetime import datetime

    ts = frame.get("timestamp", "")
    if ts:
        try:
            ts = datetime.fromisoformat(ts).strftime("%H:%M:%S")
        except ValueError:
            pass

    ftype = frame.get("type")
    if ftype == "presence":
        # Host online/offline announcement — no session_id/state, so render it
        # distinctly instead of printing a session line with empty fields.
        host = frame.get("host") or {}
        host_label = host.get("display_name") or host.get("host_id") or "?"
        status = frame.get("status", "?")
        dot = "🟢" if status == "online" else "⚫"
        return f"  {ts}  {dot} host {host_label}: {status}"
    if ftype == "aggregate":
        state = _state_name(frame.get("state", ""))
        icon = STATE_ICONS.get(state, "  ")
        sessions = frame.get("active_sessions", 0)
        breakdown = frame.get("breakdown", {})
        return f"  {ts}  {icon} {state}  ({sessions} sessions: {breakdown})"
    # session frame (default)
    state = _state_name(frame.get("state", ""))
    icon = STATE_ICONS.get(state, "  ")
    sid = frame.get("session_id", "?")
    kind = frame.get("agent_kind", "claude")
    prev = _state_name(frame.get("previous", ""))
    return f"  {ts}  {icon} [{kind}:{sid}] {prev} → {state}"


@app.command()
def daemon(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8765, help="Bind port"),
    config: Path | None = typer.Option(None, help="Config file path"),
    log_level: str = typer.Option(
        "info",
        "--log-level",
        help="Log verbosity: critical, error, warning, info, debug, trace.",
        envvar="AIMONT_LOG_LEVEL",
    ),
):
    """Start the Aimont daemon."""
    import os

    if config:
        os.environ["AIMONT_CONFIG"] = str(config)

    level = log_level.lower()
    valid = {"critical", "error", "warning", "info", "debug", "trace"}
    if level not in valid:
        typer.echo(
            f"Invalid --log-level {log_level!r}. Choose from: {', '.join(sorted(valid))}.",
            err=True,
        )
        raise typer.Exit(2)

    uvicorn.run(
        "aimont.server:api",
        host=host,
        port=port,
        log_level=level,
    )


@app.command()
def status(
    port: int = typer.Option(8765, help="Daemon port"),
):
    """Show current daemon state."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/state", timeout=2.0)
        data = r.json()
        typer.echo(f"State: {data['state']}")
        typer.echo(f"Sessions: {data['active_sessions']}")
        if data.get("breakdown"):
            typer.echo(f"Breakdown: {data['breakdown']}")
    except httpx.ConnectError:
        typer.echo("Daemon is not running.", err=True)
        raise typer.Exit(1)


@app.command()
def watch(
    mode: str = typer.Option("aggregate", help="Subscription mode: aggregate, all, session"),
    session_id: str | None = typer.Option(
        None, "--session", "-s", help="Session ID (for mode=session)"
    ),
    port: int = typer.Option(8765, help="Daemon port"),
):
    """Watch state changes in real-time."""
    import asyncio
    import json

    import websockets

    # Fail fast on a bad --mode rather than connecting only to have the daemon
    # close us with 1008. Mirrors the server-side validation.
    if mode not in ("aggregate", "all", "session"):
        typer.echo(
            f"Invalid --mode {mode!r}. Choose from: aggregate, all, session.",
            err=True,
        )
        raise typer.Exit(2)
    if mode == "session" and not session_id:
        typer.echo("mode=session requires --session <id>.", err=True)
        raise typer.Exit(2)

    async def _watch():
        url = f"ws://127.0.0.1:{port}/ws?mode={mode}"
        if session_id:
            url += f"&session={session_id}"

        typer.echo(f"Connecting to {url} ...")
        try:
            async with websockets.connect(url) as ws:
                typer.echo("Connected. Watching state changes (Ctrl+C to stop):\n")
                async for message in ws:
                    frame = json.loads(message)
                    typer.echo(format_watch_frame(frame))
        except ConnectionRefusedError:
            typer.echo("Daemon is not running.", err=True)
            raise typer.Exit(1)
        except websockets.exceptions.ConnectionClosedError as e:
            # The daemon closed the socket abnormally (e.g. rejected the
            # subscription). Report the reason instead of a bare traceback.
            reason = (e.reason or "connection closed by daemon").strip()
            typer.echo(f"Connection closed: {reason}", err=True)
            raise typer.Exit(1)
        except KeyboardInterrupt:
            pass

    asyncio.run(_watch())


@app.command()
def sessions(
    port: int = typer.Option(8765, help="Daemon port"),
):
    """List all active sessions."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/sessions", timeout=2.0)
        data = r.json()
        if not data["sessions"]:
            typer.echo("No active sessions.")
        else:
            for sid, info in data["sessions"].items():
                if isinstance(info, dict):
                    state = info.get("state", "?")
                    kind = info.get("agent_kind", "claude")
                    typer.echo(f"  [{kind}] {sid}: {state}")
                else:
                    typer.echo(f"  {sid}: {info}")
    except httpx.ConnectError:
        typer.echo("Daemon is not running.", err=True)
        raise typer.Exit(1)


@app.command(name="codex-probe")
def codex_probe(
    port: int = typer.Option(8765, help="Daemon port"),
    poll: float = typer.Option(2.0, help="Poll interval (seconds)"),
    busy_cpu: float = typer.Option(10.0, help="CPU%% threshold that counts as 'working'"),
    idle_after: float = typer.Option(6.0, help="Seconds of quiet CPU before emitting Stop"),
):
    """Watch for Codex CLI processes and report their state to the daemon."""
    from aimont.codex_probe import CodexProbe

    probe = CodexProbe(
        daemon_url=f"http://127.0.0.1:{port}/events",
        poll_sec=poll,
        busy_threshold=busy_cpu,
        idle_after_sec=idle_after,
    )
    typer.echo(
        f"Codex probe running (poll={poll}s, busy_cpu>={busy_cpu}%, idle_after={idle_after}s). Ctrl+C to stop."
    )
    try:
        probe.run_forever()
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def join(
    token: str = typer.Argument(help="Encoded AimontToken string"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing token without prompting"
    ),
):
    """Join an upstream dashboard using a token issued by IT.

    The token encodes the upstream URL and bearer secret; no separate
    configuration is required. After this command succeeds, restart the
    daemon to activate the push transport.
    """
    from aimont.auth import TokenDecodeError, decode_token
    from aimont.config import TOKEN_FILE_PATH

    try:
        bundle = decode_token(token)
    except TokenDecodeError as e:
        typer.echo(f"Invalid token: {e}", err=True)
        raise typer.Exit(1)

    if TOKEN_FILE_PATH.exists() and not force:
        typer.echo(
            f"A token already exists at {TOKEN_FILE_PATH}. "
            "Pass --force to overwrite, or run `aimont leave` first.",
            err=True,
        )
        raise typer.Exit(1)

    TOKEN_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE_PATH.write_text(token + "\n", encoding="utf-8")
    # 0600 — token is a credential; don't let other local users read it.
    TOKEN_FILE_PATH.chmod(0o600)

    typer.echo(f"✓ Joined {bundle.issuer or bundle.upstream_url}")
    typer.echo(f"  upstream: {bundle.upstream_url}")
    if bundle.display_name_hint:
        typer.echo(f"  display_name_hint: {bundle.display_name_hint}")
    typer.echo(f"  saved to: {TOKEN_FILE_PATH}")
    typer.echo("Restart the daemon for the push transport to activate.")


@app.command()
def leave(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove the joined token, disabling push mode on next daemon start."""
    from aimont.config import TOKEN_FILE_PATH

    if not TOKEN_FILE_PATH.exists():
        typer.echo("No token to remove.")
        return

    if not yes:
        confirm = typer.confirm(f"Delete {TOKEN_FILE_PATH}?", default=False)
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(1)

    TOKEN_FILE_PATH.unlink()
    typer.echo(f"✓ Removed {TOKEN_FILE_PATH}")
    typer.echo("Restart the daemon to stop pushing to the upstream.")


@app.command()
def issue(
    upstream: str = typer.Option(
        ..., "--upstream", help="Upstream URL, e.g. wss://aimont.company.com/ingest"
    ),
    secret: str = typer.Option(..., "--secret", help="Bearer secret the upstream expects"),
    display_name_hint: str | None = typer.Option(
        None, "--display-name", help="Optional hint shown on dashboards"
    ),
    issuer: str | None = typer.Option(
        None, "--issuer", help="Optional human-readable issuer label"
    ),
):
    """Issue an encoded AimontToken (IT/admin use).

    Prints the token to stdout. Distribute the resulting string to
    employees, who can run `aimont join <token>` to connect.
    """
    from aimont.auth import AimontToken, encode_token

    bundle = AimontToken(
        upstream_url=upstream,
        auth_secret=secret,
        display_name_hint=display_name_hint,
        issuer=issuer,
    )
    typer.echo(encode_token(bundle))


@app.command()
def test(
    state: str = typer.Argument(help="State to trigger (e.g. awaiting_input, error)"),
    session_id: str = typer.Option("test-session", "--session", "-s", help="Session ID"),
    port: int = typer.Option(8765, help="Daemon port"),
):
    """Send a synthetic event to test a state transition."""
    import httpx

    state_to_event = {
        "off": "SessionEnd",
        "idle": "SessionStart",
        "working": "UserPromptSubmit",
        "tool_active": "PreToolUse",
        "awaiting_input": "Stop",
        "awaiting_permission": "PermissionRequest",
        "notification": "Notification",
        "error": "StopFailure",
    }

    event = state_to_event.get(state.lower())
    if not event:
        typer.echo(f"Unknown state: {state}. Options: {', '.join(state_to_event.keys())}", err=True)
        raise typer.Exit(1)

    try:
        r = httpx.post(
            f"http://127.0.0.1:{port}/events",
            json={"event": event, "session_id": session_id},
            timeout=2.0,
        )
        typer.echo(r.json())
    except httpx.ConnectError:
        typer.echo("Daemon is not running.", err=True)
        raise typer.Exit(1)
