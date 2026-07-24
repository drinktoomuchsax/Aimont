"""CLI: daemon, status, watch, test commands."""

from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

app = typer.Typer(name="aimont", help="Human-in-the-loop state broadcast for Claude Code.")


def _exit_daemon_unreachable(exc: Exception) -> None:
    """Map an httpx/decode error from a daemon HTTP call to a clean message + exit.

    ConnectError means nothing is listening; a timeout means the daemon is up
    but not responding; an HTTP status error means it answered with an error
    code; a JSON decode error (ValueError) means whatever answered on that port
    isn't the aimont daemon. Either way we exit 1 rather than dumping a
    traceback.
    """
    import httpx

    if isinstance(exc, httpx.ConnectError):
        typer.echo("Daemon is not running.", err=True)
    elif isinstance(exc, httpx.TimeoutException):
        typer.echo("Daemon did not respond in time (is it hung?).", err=True)
    elif isinstance(exc, httpx.HTTPStatusError):
        typer.echo(f"Daemon returned an error status: {exc.response.status_code}.", err=True)
    elif isinstance(exc, ValueError):
        # json.JSONDecodeError subclasses ValueError. A non-JSON body means
        # something other than the daemon is listening on this port.
        typer.echo("Unexpected response from daemon (not JSON — wrong port?).", err=True)
    else:
        typer.echo(f"Could not reach daemon: {exc}", err=True)
    raise typer.Exit(1)


def _validate_port(value: int) -> int:
    """Reject out-of-range TCP ports before they reach uvicorn/httpx.

    A port outside 1..65535 (e.g. 0, 99999, negative) otherwise surfaces as an
    opaque uvicorn OverflowError/OSError or a malformed httpx URL. Fail with a
    clean exit-2, matching how --log-level / --mode reject bad values.
    """
    if not 1 <= value <= 65535:
        raise typer.BadParameter(f"port must be between 1 and 65535 (got {value})")
    return value


def _validate_poll(value: float) -> float:
    """Reject a non-positive poll interval before it reaches time.sleep.

    A negative value makes time.sleep raise, and 0 turns the probe loop into a
    busy-spin. Fail with a clean exit-2, matching how --port is validated.
    """
    if value <= 0:
        raise typer.BadParameter(f"poll must be greater than 0 (got {value})")
    return value


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
    port: int = typer.Option(8765, help="Bind port", callback=_validate_port),
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
        # Validate the config up front. Otherwise it isn't read until much
        # later, inside uvicorn's ASGI lifespan hook (server.load_config), where
        # a missing path or a malformed/invalid file surfaces as a full uvicorn
        # lifespan traceback + "Application startup failed" instead of the clean
        # exit-2 every other bad-argument path here produces (--port,
        # --log-level). A typo'd path or a YAML indentation slip is a common
        # user mistake, so fail fast with a one-line message.
        from aimont.config import ConfigError, load_config

        try:
            load_config(config)
        except (FileNotFoundError, ConfigError) as exc:
            typer.echo(f"Invalid --config {str(config)!r}: {exc}", err=True)
            raise typer.Exit(2) from exc

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
        # Use the modern websockets implementation; the default auto-selects
        # the deprecated websockets.legacy path.
        ws="websockets-sansio",
    )


@app.command()
def status(
    port: int = typer.Option(8765, help="Daemon port", callback=_validate_port),
):
    """Show current daemon state."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/state", timeout=2.0)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            # A foreign service on this port can answer with valid JSON of the
            # wrong shape (a list/scalar); data.get(...) would then raise an
            # uncaught AttributeError. Treat it as the "wrong port?" case the
            # non-JSON branch already handles.
            raise ValueError("response body is not a JSON object")
        typer.echo(f"State: {data.get('state', '?')}")
        typer.echo(f"Sessions: {data.get('active_sessions', '?')}")
        if data.get("breakdown"):
            typer.echo(f"Breakdown: {data['breakdown']}")
    except (httpx.HTTPError, ValueError) as e:
        _exit_daemon_unreachable(e)


@app.command()
def watch(
    mode: str = typer.Option("aggregate", help="Subscription mode: aggregate, all, session"),
    session_id: str | None = typer.Option(
        None, "--session", "-s", help="Session ID (for mode=session)"
    ),
    port: int = typer.Option(8765, help="Daemon port", callback=_validate_port),
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
                    try:
                        frame = json.loads(message)
                    except json.JSONDecodeError:
                        # A well-behaved daemon only sends JSON frames. A
                        # non-JSON text frame means something that isn't the
                        # aimont daemon completed the WS handshake on this port
                        # — mirror the sibling commands' clean "wrong port?"
                        # exit instead of dumping a JSONDecodeError traceback.
                        typer.echo(
                            "Unexpected response from daemon (not JSON — wrong port?).",
                            err=True,
                        )
                        raise typer.Exit(1)
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
        except websockets.exceptions.InvalidHandshake:
            # Something is listening but it isn't a WebSocket daemon — a plain
            # HTTP server or a different service on this port answers the
            # upgrade with a non-WS response (InvalidMessage/InvalidStatus).
            # Mirror the sibling commands' clean "wrong port?" exit instead of
            # dumping a websockets traceback.
            typer.echo("Unexpected response from daemon (wrong port?).", err=True)
            raise typer.Exit(1)
        except OSError as e:
            # Network-level failure (host unreachable, DNS, reset) that isn't a
            # plain connection-refused.
            typer.echo(f"Could not reach daemon: {e}", err=True)
            raise typer.Exit(1)
        except KeyboardInterrupt:
            pass

    asyncio.run(_watch())


@app.command()
def sessions(
    port: int = typer.Option(8765, help="Daemon port", callback=_validate_port),
):
    """List all active sessions."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/sessions", timeout=2.0)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            # Foreign JSON server on this port: data.get(...) would raise an
            # uncaught AttributeError. Route it through the "wrong port?" path.
            raise ValueError("response body is not a JSON object")
        sessions_map = data.get("sessions")
        if not sessions_map:
            typer.echo("No active sessions.")
        elif not isinstance(sessions_map, dict):
            # A well-formed daemon returns a mapping; a wrong-shape body (e.g.
            # {"sessions": [...]}) would crash .items(). Treat as wrong port.
            raise ValueError("'sessions' is not a JSON object")
        else:
            for sid, info in sessions_map.items():
                if isinstance(info, dict):
                    state = info.get("state", "?")
                    kind = info.get("agent_kind", "claude")
                    typer.echo(f"  [{kind}] {sid}: {state}")
                else:
                    typer.echo(f"  {sid}: {info}")
    except (httpx.HTTPError, ValueError) as e:
        _exit_daemon_unreachable(e)


@app.command(name="codex-probe")
def codex_probe(
    port: int = typer.Option(8765, help="Daemon port", callback=_validate_port),
    poll: float = typer.Option(2.0, help="Poll interval (seconds)", callback=_validate_poll),
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

    import os

    # The token is a credential. Create the file with 0600 from the start via
    # os.open — writing then chmod'ing leaves a window where the secret exists
    # world-readable under the process umask, which a local attacker can read.
    TOKEN_FILE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(TOKEN_FILE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    # O_CREAT honors the mode only when the file is new; force 0600 in case an
    # existing (--force overwrite) file had looser perms.
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
    port: int = typer.Option(8765, help="Daemon port", callback=_validate_port),
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
        r.raise_for_status()
        typer.echo(r.json())
    except (httpx.HTTPError, ValueError) as e:
        _exit_daemon_unreachable(e)
