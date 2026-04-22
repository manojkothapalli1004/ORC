"""Smoke tests for the ORC CLI banner and entrypoint."""
from __future__ import annotations


def test_banner_renders_without_color_when_forced():
    from backend.cli.banner import render

    out = render(version="0.1.0", use_color=False)
    assert "ORC" not in out  # block-letter art, not the literal string
    assert "local operator console for bounded AI work" in out
    assert "v0.1.0" in out
    assert "\033[" not in out  # no ANSI escapes when color disabled


def test_banner_contains_ansi_codes_when_color_forced():
    from backend.cli.banner import render

    out = render(version="0.1.0", use_color=True)
    assert "\033[" in out  # ANSI escapes present
    assert "v0.1.0" in out


def test_orc_version_in_process(capsys):
    """Calling main(['version']) prints the version line to stdout."""
    from backend.cli.main import main

    rc = main(["version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("ORC v")


def test_orc_parser_has_expected_subcommands():
    from backend.cli.main import build_parser

    parser = build_parser()
    help_text = parser.format_help()
    assert "start" in help_text
    assert "mcp" in help_text
    assert "version" in help_text
