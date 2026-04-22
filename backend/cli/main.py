"""Command-line entry point for the ORC CLI.

Usage:
    orc                 # print banner and help
    orc start           # launch HTTP server (uvicorn)
    orc mcp             # launch MCP server (stdio)
    orc version         # print version
    orc --help          # argparse help
"""
from __future__ import annotations

import argparse
import sys

from backend.cli.banner import print_banner, render

try:
    from importlib.metadata import version as _pkg_version
    VERSION = _pkg_version("orchestrator")
except Exception:
    VERSION = "0.1.0"


def _cmd_start(args: argparse.Namespace) -> int:
    print_banner(version=VERSION)
    import uvicorn
    uvicorn.run(
        "backend.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    print_banner(version=VERSION)
    from backend import mcp_server  # noqa: F401 — starts on import path
    # mcp_server runs its own asyncio loop; delegate to its entry
    from backend.mcp_server import main as _mcp_main
    _mcp_main()
    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    sys.stdout.write(f"ORC v{VERSION}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orc",
        description="local operator console for bounded AI work",
    )
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Launch the HTTP server (FastAPI + UI)")
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=8100)
    p_start.add_argument("--reload", action="store_true")
    p_start.set_defaults(func=_cmd_start)

    p_mcp = sub.add_parser("mcp", help="Launch the MCP server (stdio)")
    p_mcp.set_defaults(func=_cmd_mcp)

    p_version = sub.add_parser("version", help="Print version")
    p_version.set_defaults(func=_cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        print_banner(version=VERSION)
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
