"""Terminal banner for the ORC CLI.

Prints a bold block-letter "ORC" with a tagline. Auto-detects whether the
current stdout supports 24-bit ANSI color; falls back to plain text on
dumb terminals or when NO_COLOR is set.
"""
from __future__ import annotations

import os
import sys

BANNER_TEXT = r""" ██████╗ ██████╗  ██████╗
██╔═══██╗██╔══██╗██╔════╝
██║   ██║██████╔╝██║
██║   ██║██╔══██╗██║
╚██████╔╝██║  ██║╚██████╗
 ╚═════╝ ╚═╝  ╚═╝ ╚═════╝"""

TAGLINE = "local operator console for bounded AI work"

# Orc-green, 24-bit ANSI
_FG = "\033[38;2;74;124;58m"
_DIM = "\033[38;2;140;160;130m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return getattr(stream, "isatty", lambda: False)()


def render(version: str = "", use_color: bool | None = None) -> str:
    """Return the banner string. Pass use_color to override auto-detection."""
    if use_color is None:
        use_color = _supports_color(sys.stdout)
    lines = BANNER_TEXT.splitlines()
    if use_color:
        lines = [f"{_BOLD}{_FG}{line}{_RESET}" for line in lines]
        tag = f"{_DIM}{TAGLINE}{_RESET}"
    else:
        tag = TAGLINE
    rendered = "\n".join(lines)
    footer = f"  {tag}"
    if version:
        v = f"v{version}"
        footer = f"  {tag}    {v}" if not use_color else f"  {tag}    {_DIM}{v}{_RESET}"
    return f"{rendered}\n{footer}\n"


def print_banner(version: str = "") -> None:
    sys.stdout.write(render(version=version))
    sys.stdout.flush()
