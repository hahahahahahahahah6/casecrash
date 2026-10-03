"""Tests for casecrash.core: index reading and collision detection."""

import os
import subprocess

import pytest

from casecrash.core import (
    IndexEntry,
    NotAGitRepo,
    _collision_key,
    escaped_display,
    find_collisions,
    normalization_form,
    read_index,
    repo_root,
    safe_display,
    scan,
)
from conftest import add_bytes, add_text, git


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

NFC_CAFE = "caf\u00e9"      # café, single codepoint é
NFD_CAFE = "cafe\u0301"     # café, e + combining acute


def kinds(result):
    return {c.kind for c in result.collisions}


def files_of(result, kind):
    for c in result.collisions:
        if c.kind == kind:
            return sorted(safe_display(n) for n in c.names)
    return []


# ---------------------------------------------------------------------------
# clean repos
# ---------------------------------------------------------------------------

def test_clean_repo_no_collisions(git_repo):
    add_text(git_repo, "README.md")
    add_text(git_repo, "src/main.py")
    add_text(git_repo, "docs/Guide.md")
    result = scan(str(git_repo))
    assert result.collisions == []
    assert result.files_scanned == 3
    assert result.gitlinks_skipped == 0
    assert not result.has_collisions


def test_empty_repo_no_collisions(git_repo):
    result = scan(str(git_repo))
    assert result.collisions == []
    assert result.files_scanned == 0


def test_similar_but_safe_names_no_collision(git_repo):
    add_text(git_repo, "readme.md")
    add_text(git_repo, "readme.txt")   # different extension: fine
    add_text(git_repo, "read_me.md")   # underscore: fine
    assert scan(str(git_repo)).collisions == []


# ---------------------------------------------------------------------------
# case collisions
# ---------------------------------------------------------------------------

def test_simple_case_collision(git_repo):
    add_text(git_repo, "README.md")
    add_text(git_repo, "readme.md")
    result = scan(str(git_repo))
    assert kinds(result) == {"case"}
    assert files_of(result, "case") == ["README.md", "readme.md"]


def test_case_collision_nested_dirs(git_repo):
    add_text(git_repo, "src/Util.py")
    add_text(git_repo, "src/util.py")
    add_text(git_repo, "src/other.py")
    result = scan(str(git_repo))
    assert kinds(result) == {"case"}
    assert files_of(result, "case") == ["src/Util.py", "src/util.py"]


def test_case_collision_three_way(git_repo):
    add_text(git_repo, "Makefile")
    add_text(git_repo, "makefile")
    add_text(git_repo, "MAKEFILE")
    result = scan(str(git_repo))
    assert len(result.collisions) == 1
    assert len(result.collisions[0].names) == 3


def test_case_collision_platforms(git_repo):
    add_text(git_repo, "A.txt")
    add_text(git_repo, "a.txt")
    collision = scan(str(git_repo)).collisions[0]
    assert collision.kind == "case"
    assert collision.platforms == ["macOS", "Windows"]


def test_accented_case_only_is_case_not_normalization(git_repo):
    # É (U+00C9) vs é (U+00E9): pure case difference, breaks Windows too.
    add_text(git_repo, "\u00c9tude.md")
    add_text(git_repo, "\u00e9tude.md")
    result = scan(str(git_repo))
    assert kinds(result) == {"case"}


def test_german_sharp_s_collides(git_repo):
    # ß casefolds to "ss"
    add_text(git_repo, "STRASSE.md")
    add_text(git_repo, "stra\u00dfe.md")
    result = scan(str(git_repo))
    assert kinds(result) == {"case"}


# ---------------------------------------------------------------------------
# normalization collisions
# ---------------------------------------------------------------------------

def test_nfc_nfd_collision(git_repo):
    add_text(git_repo, NFC_CAFE + ".md")
    add_text(git_repo, NFD_CAFE + ".md")
    result = scan(str(git_repo))
    assert kinds(result) == {"normalization"}
    collision = result.collisions[0]
    assert collision.platforms == ["macOS"]


def test_nfc_nfd_collision_reverse_order(git_repo):
    add_text(git_repo, NFD_CAFE + ".md")
    add_text(git_repo, NFC_CAFE + ".md")
    assert kinds(scan(str(git_repo))) == {"normalization"}


def test_mixed_case_and_normalization_is_normalization(git_repo):
    # CAFÉ (NFC, upper) vs café (NFD, lower): normalization dominates,
    # Windows would actually be fine with this pair.
    add_text(git_repo, NFC_CAFE.upper() + ".md")
    add_text(git_repo, NFD_CAFE + ".md")
    result = scan(str(git_repo))
    assert kinds(result) == {"normalization"}
    assert result.collisions[0].platforms == ["macOS"]


def test_nfc_nfd_same_bytes_no_collision(git_repo):
    # Two identical NFC names added twice is one file, not a collision.
    add_text(git_repo, NFC_CAFE + ".md")
    assert scan(str(git_repo)).collisions == []


# ---------------------------------------------------------------------------
# exact duplicates (synthetic: real git cannot produce these)
# ---------------------------------------------------------------------------

def test_exact_duplicate_detected():
    entries = [
        IndexEntry(name=b"a.txt", mode=b"100644",
                   sha=b"0" * 40, stage=b"0"),
        IndexEntry(name=b"a.txt", mode=b"100644",
                   sha=b"0" * 40, stage=b"0"),
    ]
    collisions = find_collisions(entries)
    assert len(collisions) == 1
    assert collisions[0].kind == "exact-duplicate"
    assert collisions[0].names == [b"a.txt", b"a.txt"]


def test_unmerged_stages_are_not_exact_duplicates():
    # Same name at different merge stages is normal merge state, and the
    # distinct names still collapse to one for the other checks.
    entries = [
        IndexEntry(name=b"a.txt", mode=b"100644",
                   sha=b"0" * 40, stage=b"1"),
        IndexEntry(name=b"a.txt", mode=b"100644",
                   sha=b"1" * 40, stage=b"2"),
        IndexEntry(name=b"b.txt", mode=b"100644",
                   sha=b"2" * 40, stage=b"0"),
    ]
    assert find_collisions(entries) == []


# ---------------------------------------------------------------------------
# submodules
# ---------------------------------------------------------------------------

def test_gitlink_is_skipped(git_repo):
    add_text(git_repo, "real.txt")
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo",
         "160000," + "1" * 40 + ",vendor/lib"],
        cwd=str(git_repo), check=True, capture_output=True,
    )
    result = scan(str(git_repo))
    assert result.gitlinks_skipped == 1
    assert result.files_scanned == 1
    assert result.collisions == []


def test_gitlink_case_similar_to_file_is_not_a_collision(git_repo):
    add_text(git_repo, "submod.txt")
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo",
         "160000," + "1" * 40 + ",SUBMOD.txt"],
        cwd=str(git_repo), check=True, capture_output=True,
    )
    result = scan(str(git_repo))
    assert result.collisions == []
    assert result.gitlinks_skipped == 1


# ---------------------------------------------------------------------------
# weird filenames
# ---------------------------------------------------------------------------

def test_non_utf8_case_collision(git_repo):
    add_bytes(git_repo, b"data_\xff.bin")
    add_bytes(git_repo, b"DATA_\xff.BIN")
    result = scan(str(git_repo))
    assert kinds(result) == {"case"}


def test_non_utf8_no_crash_and_no_false_positive(git_repo):
    add_bytes(git_repo, b"\xff\xfe.bin")
    add_bytes(git_repo, "normal.txt".encode())
    assert scan(str(git_repo)).collisions == []


def test_safe_display_escapes_bad_bytes():
    assert safe_display(b"a\xff" b"b") == "a\\xffb"
    assert safe_display("héllo".encode("utf-8")) == "héllo"


def test_escaped_display_renders_nfd_visibly():
    assert escaped_display(NFD_CAFE.encode("utf-8")) == "cafe\\u0301"
    assert escaped_display(NFC_CAFE.encode("utf-8")) == "caf\\u00e9"


def test_normalization_form_labels():
    assert normalization_form(NFC_CAFE.encode("utf-8")) == "NFC"
    assert normalization_form(NFD_CAFE.encode("utf-8")) == "NFD"
    assert normalization_form(b"plain.txt") == ""
    assert normalization_form(b"\xff\xfe") == ""


def test_collision_key_byte_fallback():
    kind, key = _collision_key(b"ABC\xff")
    assert kind == "raw"
    assert _collision_key(b"abc\xff") == (kind, key)
    assert _collision_key(b"abd\xff") != (kind, key)


# ---------------------------------------------------------------------------
# repo handling
# ---------------------------------------------------------------------------

def test_not_a_git_repo_raises(tmp_path):
    with pytest.raises(NotAGitRepo):
        scan(str(tmp_path))


def test_scan_accepts_file_inside_repo(git_repo):
    add_text(git_repo, "README.md")
    add_text(git_repo, "readme.md")
    target = os.path.join(str(git_repo), "README.md")
    assert kinds(scan(target)) == {"case"}


def test_repo_root_is_toplevel(git_repo):
    add_text(git_repo, "sub/dir/file.txt")
    assert repo_root(os.path.join(str(git_repo), "sub")) == str(git_repo)


def test_read_index_uses_full_names(git_repo):
    add_text(git_repo, "sub/dir/file.txt")
    entries, _ = read_index(os.path.join(str(git_repo), "sub"))
    assert entries[0].name == b"sub/dir/file.txt"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------

def test_kind_sort_order(git_repo):
    add_text(git_repo, "zzz.md")
    add_text(git_repo, "ZZZ.md")          # case
    add_text(git_repo, NFC_CAFE + ".md")
    add_text(git_repo, NFD_CAFE + ".md")  # normalization
    result = scan(str(git_repo))
    assert [c.kind for c in result.collisions] == ["normalization", "case"]
