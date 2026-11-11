"""``jarvis wake`` — resident JARVIS operator with double-clap + voice."""

from __future__ import annotations

import sys

import click


@click.command()
@click.option(
    "--ui/--no-ui",
    "use_ui",
    default=False,
    help="Open the small desktop panel instead of running in this terminal.",
)
@click.option(
    "--once",
    is_flag=True,
    default=False,
    help="Read one request from stdin, answer it, and exit.",
)
@click.option(
    "--checkup",
    "checkup_only",
    is_flag=True,
    default=False,
    help="Print the machine checkup (Steam/winget/disk) and exit.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Plan actions and log them without changing anything.",
)
@click.option(
    "--trusted",
    is_flag=True,
    default=False,
    help="Auto-approve actions that would normally ask first.",
)
@click.option(
    "--no-ritual",
    is_flag=True,
    default=False,
    help="Skip the song/welcome sequence; a clap goes straight to listening.",
)
@click.option(
    "--no-voice",
    is_flag=True,
    default=False,
    help="Typed input only (no microphone, no speech).",
)
@click.option(
    "--install-startup",
    is_flag=True,
    default=False,
    help="Create a Windows Startup shortcut for the panel and exit.",
)
@click.option(
    "--uninstall-startup",
    is_flag=True,
    default=False,
    help="Remove the Windows Startup shortcut and exit.",
)
@click.option(
    "--list-tools",
    "list_tools",
    is_flag=True,
    default=False,
    help="Show the PC tools this session would expose and exit.",
)
@click.option("-m", "--model", "model_name", default="", help="Model id override.")
def wake(
    use_ui: bool,
    once: bool,
    checkup_only: bool,
    dry_run: bool,
    trusted: bool,
    no_ritual: bool,
    no_voice: bool,
    install_startup: bool,
    uninstall_startup: bool,
    list_tools: bool,
    model_name: str,
) -> None:
    """Run JARVIS as a resident operator that can act on this PC.

    \b
    First double clap: plays the track, speaks the welcome line in your
    ElevenLabs voice, focuses Cursor, then reads you the machine checkup.
    Later double claps: open the mic for a request, e.g.
    "check my Steam games and update anything that needs it".
    """
    if install_startup:
        from openjarvis.wake.startup import install_startup as _install

        ok, message = _install()
        click.echo(message)
        raise SystemExit(0 if ok else 1)
    if uninstall_startup:
        from openjarvis.wake.startup import uninstall_startup as _uninstall

        ok, message = _uninstall()
        click.echo(message)
        raise SystemExit(0 if ok else 1)

    from openjarvis.core.config import load_config

    config = load_config()

    if list_tools:
        from openjarvis.tools.pc import pc_tools

        for tool in pc_tools(config=config):
            click.echo(f"{tool.spec.name:18} {tool.spec.description.splitlines()[0]}")
        raise SystemExit(0)

    if checkup_only:
        from openjarvis.pc.brief import collect_brief

        brief = collect_brief(config)
        click.echo("\n".join(brief.lines))
        click.echo("")
        click.echo(brief.speech)
        raise SystemExit(0)

    from openjarvis.wake.session import WakeSession

    try:
        session = WakeSession(
            config,
            dry_run=dry_run,
            trusted=trusted,
            no_ritual=no_ritual,
            no_voice=no_voice,
            model_name=model_name,
            silent=once,
        )
    except RuntimeError as exc:
        click.echo(f"[wake] {exc}", err=True)
        raise SystemExit(1) from exc

    if once:
        request = sys.stdin.readline().strip()
        if not request:
            click.echo("[wake] No request on stdin.", err=True)
            raise SystemExit(2)
        session.run_text(request)
        raise SystemExit(0)

    if use_ui:
        from openjarvis.wake.ui import run_ui

        try:
            run_ui(session)
        except RuntimeError as exc:
            click.echo(f"[wake] {exc}", err=True)
            raise SystemExit(1) from exc
        return

    session.run_terminal()


__all__ = ["wake"]
