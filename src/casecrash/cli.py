"""Command-line interface for casecrash."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .core import (
    GitNotFound,
    NotAGitRepo,
    escaped_display,
    normalization_form,
    safe_display,
    scan,
)

EXIT_OK = 0
EXIT_COLLISIONS = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="casecrash",
        description=(
            "Find filenames in your git index that would collide on "
            "case-insensitive or Unicode-normalizing filesystems "
            "(macOS, Windows) — before they break someone's checkout."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="directory inside the git repository to scan (default: .)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="casecrash " + __version__,
    )
    return parser


def _platform_note(kind: str) -> str:
    if kind == "case":
        return "collide on case-insensitive filesystems (macOS, Windows)"
    if kind == "normalization":
        return "collide after Unicode normalization (macOS)"
    return "identical index entries (index corruption)"


def _format_name(raw: bytes) -> str:
    form = normalization_form(raw)
    shown = escaped_display(raw)
    if form:
        return "'%s'  [%s]" % (shown, form)
    return "'%s'" % shown


def render_text(result) -> str:
    lines = []
    if not result.collisions:
        lines.append(
            "casecrash: no filename collisions in the git index "
            "(%d files scanned%s)." % (
                result.files_scanned,
                ", %d submodule(s) skipped" % result.gitlinks_skipped
                if result.gitlinks_skipped else "",
            )
        )
        return "\n".join(lines) + "\n"

    lines.append(
        "casecrash: found %d filename collision(s) in %s "
        "(%d files scanned):"
        % (len(result.collisions), result.repo_root, result.files_scanned)
    )
    for collision in result.collisions:
        lines.append("")
        lines.append("[%s] %s:" % (collision.kind, _platform_note(collision.kind)))
        for raw in collision.names:
            lines.append("  %s" % _format_name(raw))
        lines.append(
            "  -> breaks on: %s" % ", ".join(collision.platforms)
        )
    if result.gitlinks_skipped:
        lines.append("")
        lines.append(
            "note: %d submodule(s) skipped (not checked)"
            % result.gitlinks_skipped
        )
    lines.append("")
    lines.append(
        "Fix: rename one of each colliding pair before a macOS/Windows "
        "user clones this repo.  "
        "'git mv' the odd one out and commit."
    )
    return "\n".join(lines) + "\n"


def render_json(result) -> str:
    payload = {
        "tool": "casecrash",
        "version": __version__,
        "repo_root": result.repo_root,
        "files_scanned": result.files_scanned,
        "gitlinks_skipped": result.gitlinks_skipped,
        "collisions": [
            {
                "kind": collision.kind,
                "platforms": collision.platforms,
                "files": [safe_display(n) for n in collision.names],
            }
            for collision in result.collisions
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = scan(args.path)
    except (NotAGitRepo, GitNotFound) as exc:
        print("casecrash: error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    if args.format == "json":
        sys.stdout.write(render_json(result))
    else:
        sys.stdout.write(render_text(result))

    return EXIT_COLLISIONS if result.has_collisions else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
