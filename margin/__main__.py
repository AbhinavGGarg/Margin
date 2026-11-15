"""Command line: `python -m margin` starts the app, `python -m margin doctor` checks the setup."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from . import __version__, config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="margin", description="Tap a line of your notes, hear the moment it was explained.")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "doctor"])
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    parser.add_argument("--version", action="version", version="Margin " + __version__)
    args = parser.parse_args(argv)

    config.load_env_file()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

    if args.command == "doctor":
        from . import doctor

        return doctor.run()

    import uvicorn

    port = args.port or config.port()
    url = "http://%s:%d" % ("localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host, port)
    print("\n  Margin is running at " + url + "\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("margin.server:app", host=args.host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
