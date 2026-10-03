"""Tests for the casecrash CLI."""

import json
import os

import pytest

from casecrash.cli import (
    EXIT_COLLISIONS,
    EXIT_ERROR,
    EXIT_OK,
    build_parser,
    main,
    render_json,
    render_text,
)
from casecrash.core import scan
from conftest import add_text


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------

def test_cli_clean_repo_exit_0(git_repo, capsys):
    add_text(git_repo, "README.md")
    assert main([str(git_repo)]) == EXIT_OK
    assert main([str(git_repo)]) == 0
    out = capsys.readouterr().out
    assert "no filename collisions" in out


def test_cli_collision_exit_1(git_repo, capsys):
    add_text(git_repo, "README.md")
    add_text(git_repo, "readme.md")
    assert main([str(git_repo)]) == EXIT_COLLISIONS
    assert main([str(git_repo)]) == 1


def test_cli_not_a_git_repo_exit_2(tmp_path, capsys):
    assert main([str(tmp_path)]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "not inside a git repository" in err


def test_cli_empty_repo_exit_0(git_repo, capsys):
    assert main([str(git_repo)]) == EXIT_OK


def test_cli_defaults_to_cwd(git_repo, capsys, monkeypatch):
    add_text(git_repo, "a.txt")
    monkeypatch.chdir(git_repo)
    assert main([]) == EXIT_OK


# ---------------------------------------------------------------------------
# text output
# ---------------------------------------------------------------------------

def test_cli_text_report_shows_both_names(git_repo, capsys):
    add_text(git_repo, "README.md")
    add_text(git_repo, "readme.md")
    main([str(git_repo)])
    out = capsys.readouterr().out
    assert "[case]" in out
    assert "README.md" in out and "readme.md" in out
    assert "macOS" in out and "Windows" in out


def test_cli_text_report_shows_fix_hint(git_repo, capsys):
    add_text(git_repo, "A.md")
    add_text(git_repo, "a.md")
    main([str(git_repo)])
    assert "git mv" in capsys.readouterr().out


def test_cli_text_nfd_is_visible(git_repo, capsys):
    add_text(git_repo, "caf\u00e9.md")
    add_text(git_repo, "cafe\u0301.md")
    main([str(git_repo)])
    out = capsys.readouterr().out
    assert "[normalization]" in out
    assert "\\u0301" in out  # NFD form is visibly different
    assert "[NFD]" in out and "[NFC]" in out


def test_cli_text_mentions_skipped_submodules(git_repo, capsys):
    import subprocess
    add_text(git_repo, "a.txt")
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo",
         "160000," + "1" * 40 + ",vendor/lib"],
        cwd=str(git_repo), check=True, capture_output=True,
    )
    main([str(git_repo)])
    assert "1 submodule(s) skipped" in capsys.readouterr().out


def test_cli_text_weird_bytes_do_not_crash(git_repo, capsys):
    from conftest import add_bytes
    add_bytes(git_repo, b"\xff.md")
    add_bytes(git_repo, b"A\xff.md")
    rc = main([str(git_repo)])
    out = capsys.readouterr().out
    assert rc in (EXIT_OK, EXIT_COLLISIONS)
    out.encode("utf-8")  # must be printable


# ---------------------------------------------------------------------------
# json output
# ---------------------------------------------------------------------------

def test_cli_json_is_valid(git_repo, capsys):
    add_text(git_repo, "README.md")
    add_text(git_repo, "readme.md")
    assert main([str(git_repo), "--format", "json"]) == EXIT_COLLISIONS
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "casecrash"
    assert payload["files_scanned"] == 2
    assert payload["gitlinks_skipped"] == 0
    assert len(payload["collisions"]) == 1
    collision = payload["collisions"][0]
    assert collision["kind"] == "case"
    assert sorted(collision["files"]) == ["README.md", "readme.md"]
    assert collision["platforms"] == ["macOS", "Windows"]


def test_cli_json_clean_repo(git_repo, capsys):
    add_text(git_repo, "a.txt")
    assert main([str(git_repo), "--format", "json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["collisions"] == []


def test_cli_json_error_is_not_json(tmp_path, capsys):
    # errors go to stderr as plain text even in json mode
    assert main([str(tmp_path), "--format", "json"]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error" in captured.err


def test_render_json_is_ascii_safe():
    from casecrash.core import ScanResult, Collision
    result = ScanResult(
        repo_root="/r", files_scanned=1, gitlinks_skipped=0,
        collisions=[Collision(kind="case", names=[b"a\xff", b"A\xff"])],
    )
    payload = json.loads(render_json(result))
    assert payload["collisions"][0]["files"] == ["a\\xff", "A\\xff"]


def test_render_text_clean_mentions_count():
    from casecrash.core import ScanResult
    out = render_text(ScanResult(repo_root="/r", files_scanned=5,
                                 gitlinks_skipped=2))
    assert "5 files" in out and "2 submodule(s) skipped" in out


# ---------------------------------------------------------------------------
# argument handling
# ---------------------------------------------------------------------------

def test_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "casecrash" in capsys.readouterr().out


def test_cli_bad_format_flag():
    with pytest.raises(SystemExit) as exc:
        main(["--format", "yaml"])
    assert exc.value.code == 2


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "macOS" in capsys.readouterr().out


def test_build_parser_defaults():
    args = build_parser().parse_args([])
    assert args.path == "."
    assert args.format == "text"


def test_git_not_found_gives_clear_error(monkeypatch, tmp_path, capsys):
    import casecrash.core as core

    def boom(*a, **k):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(core.subprocess, "run", boom)
    assert main([str(tmp_path)]) == EXIT_ERROR
    assert "git" in capsys.readouterr().err
