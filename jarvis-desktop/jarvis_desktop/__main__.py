"""Console entry point: start the local server and open the UI."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import socket
import sys
import threading
import webbrowser

from . import __version__
from .paths import log_path


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_path(), encoding="utf-8")
    ]
    if sys.stderr is not None:  # windowed builds have no stderr
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _pick_port(host: str, preferred: int, attempts: int = 20) -> int:
    for candidate in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(
        f"No free port between {preferred} and {preferred + attempts - 1}"
    )


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(
        prog="jarvis-desktop",
        description="Double-clap desktop assistant with a local UI.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="Preferred port (default: 8765)"
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the UI")
    parser.add_argument("--version", action="version", version=f"Jarvis {__version__}")
    args = parser.parse_args(argv)

    _configure_logging()
    log = logging.getLogger("jarvis")
    log.info("Starting Jarvis %s", __version__)

    try:
        port = _pick_port(args.host, args.port)
    except RuntimeError as exc:
        log.error("%s", exc)
        if sys.stderr is not None:
            print(exc, file=sys.stderr)
        return 1

    browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{browser_host}:{port}/"
    log.info("UI available at %s", url)
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()

    import uvicorn

    from .app import app

    uvicorn.run(app, host=args.host, port=port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
