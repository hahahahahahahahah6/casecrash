"""Shared fixtures for casecrash tests."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def git(*args, cwd, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    git("init", cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=tmp_path)
    git("config", "user.name", "test", cwd=tmp_path)
    git("config", "commit.gpgsign", "false", cwd=tmp_path)
    return tmp_path


def add_bytes(repo, name: bytes, content: bytes = b"x"):
    """Create a file with a (possibly non-UTF-8) name and git-add it."""
    full = os.path.join(os.fsencode(str(repo)), name)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(content)
    subprocess.run(
        ["git", "add", full],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


def add_text(repo, name: str, content: str = "x"):
    add_bytes(repo, name.encode("utf-8"), content.encode("utf-8"))
