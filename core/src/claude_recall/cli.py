"""CLI: daemon, status, test commands."""

from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

app = typer.Typer(name="claude-recall", help="Human-in-the-loop state broadcast for Claude Code.")


@app.command()
def daemon(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8765, help="Bind port"),
    config: Path | None = typer.Option(None, help="Config file path"),
):
    """Start the Claude Recall daemon."""
    if config:
        import os
        os.environ["CLAUDE_RECALL_CONFIG"] = str(config)

    uvicorn.run(
        "claude_recall.server:api",
        host=host,
        port=port,
        log_level="info",
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
            for sid, state in data["sessions"].items():
                typer.echo(f"  {sid}: {state}")
    except httpx.ConnectError:
        typer.echo("Daemon is not running.", err=True)
        raise typer.Exit(1)


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
