"""Core collision detection for casecrash.

Reads the git index (``git ls-files -s -z``) and groups tracked paths by
``NFC(casefold(name))``.  Inside each group the cause is classified:

* ``case`` — names share the same NFC form but differ (pure case
  difference).  Breaks on macOS and Windows.
* ``normalization`` — NFC forms differ by more than case (e.g. NFC ``café``
  vs NFD ``café``).  Breaks on macOS, whose filesystems compare names
  without regard to normalization.
* ``exact-duplicate`` — the identical (name, mode, sha) entry appears twice,
  which indicates index corruption.

Filenames are handled as bytes end-to-end and only decoded for display with
``surrogateescape``, so undecodable names can never crash the tool.
"""

from __future__ import annotations

import os
import subprocess
import unicodedata
from dataclasses import dataclass, field


class NotAGitRepo(Exception):
    """Raised when the target directory is not inside a git repository."""


class GitNotFound(Exception):
    """Raised when the git executable cannot be found."""


GITLINK_MODE = b"160000"


@dataclass
class IndexEntry:
    name: bytes
    mode: bytes
    sha: bytes
    stage: bytes


@dataclass
class Collision:
    kind: str  # "case" | "normalization" | "exact-duplicate"
    names: list[bytes] = field(default_factory=list)

    @property
    def platforms(self) -> list[str]:
        if self.kind == "case":
            return ["macOS", "Windows"]
        if self.kind == "normalization":
            return ["macOS"]
        return ["macOS", "Windows", "Linux"]


@dataclass
class ScanResult:
    repo_root: str
    files_scanned: int
    gitlinks_skipped: int
    collisions: list[Collision] = field(default_factory=list)

    @property
    def has_collisions(self) -> bool:
        return bool(self.collisions)


# ---------------------------------------------------------------------------
# display helpers
# ---------------------------------------------------------------------------

def _safe_char(ch: str) -> str:
    code = ord(ch)
    if 0xDC80 <= code <= 0xDCFF:  # surrogateescape byte
        return "\\x%02x" % (code - 0xDC00)
    return ch


def safe_display(raw: bytes) -> str:
    """Lossless-ish printable form of a raw filename.

    Undecodable bytes become ``\\xNN`` escapes; everything else passes
    through unchanged.
    """
    return "".join(_safe_char(c) for c in raw.decode("utf-8", "surrogateescape"))


def escaped_display(raw: bytes) -> str:
    """Like :func:`safe_display` but renders every non-ASCII char as \\uXXXX.

    Makes NFC-vs-NFD differences visible in reports.
    """
    out = []
    for ch in raw.decode("utf-8", "surrogateescape"):
        code = ord(ch)
        if 0xDC80 <= code <= 0xDCFF:
            out.append("\\x%02x" % (code - 0xDC00))
        elif code > 0x7E or code < 0x20:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    return "".join(out)


def normalization_form(raw: bytes) -> str:
    """Best-effort NFC/NFD label for a filename, '' when not applicable."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    nfc = unicodedata.normalize("NFC", text)
    nfd = unicodedata.normalize("NFD", text)
    if text == nfc and text != nfd:
        return "NFC"
    if text == nfd and text != nfc:
        return "NFD"
    if text != nfc and text != nfd:
        return "mixed"
    return ""  # pure ASCII, form is irrelevant


# ---------------------------------------------------------------------------
# index reading
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise GitNotFound(
            "the 'git' executable was not found on PATH; "
            "casecrash needs git to read the index"
        ) from exc


def repo_root(path: str) -> str:
    """Return the repository root for *path*, or raise NotAGitRepo."""
    if os.path.isfile(path):
        path = os.path.dirname(os.path.abspath(path)) or "."
    proc = _run_git(["rev-parse", "--show-toplevel"], cwd=path)
    if proc.returncode != 0:
        raise NotAGitRepo(
            "%r is not inside a git repository" % os.path.abspath(path)
        )
    return proc.stdout.decode("utf-8", "replace").strip()


def read_index(path: str) -> tuple[list[IndexEntry], int]:
    """Read the git index.  Returns (entries, gitlink_count).

    Gitlinks (submodules) are counted and excluded from the entries so they
    never participate in collision detection.
    """
    root = repo_root(path)
    proc = _run_git(["ls-files", "-s", "-z", "--full-name"], cwd=root)
    if proc.returncode != 0:
        raise NotAGitRepo(
            "could not read the git index of %r: %s"
            % (root, proc.stderr.decode("utf-8", "replace").strip())
        )
    entries: list[IndexEntry] = []
    gitlinks = 0
    for record in proc.stdout.split(b"\x00"):
        if not record:
            continue
        head, _, name = record.partition(b"\t")
        parts = head.split(b" ")
        if len(parts) < 3 or not name:
            continue
        mode, sha, stage = parts[0], parts[1], parts[2]
        if mode == GITLINK_MODE:
            gitlinks += 1
            continue
        entries.append(IndexEntry(name=name, mode=mode, sha=sha, stage=stage))
    return entries, gitlinks


# ---------------------------------------------------------------------------
# collision detection
# ---------------------------------------------------------------------------

def _collision_key(raw: bytes) -> tuple[str, object]:
    """Grouping key: NFC(casefold(name)).

    Names that are not valid UTF-8 fall back to byte-level lowercasing so
    ASCII case collisions in weird filenames are still caught.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ("raw", raw.lower())
    return ("text", unicodedata.normalize("NFC", text.casefold()))


def _pair_kind(a: bytes, b: bytes) -> str:
    """Classify why two colliding names collide.

    * ``case`` — the names are equal under Unicode casefolding, so they
      differ only by case (``README.md``/``readme.md``, ``É``/``é``).
      Breaks on macOS and Windows.
    * ``normalization`` — the casefolded forms still differ, so the names
      are distinct spellings that only become equal after Unicode
      normalization (NFC ``café`` vs NFD ``café``).  Breaks on macOS,
      whose filesystems compare names without regard to normalization.
    """
    try:
        fa = a.decode("utf-8", "surrogateescape").casefold()
        fb = b.decode("utf-8", "surrogateescape").casefold()
    except UnicodeDecodeError:  # pragma: no cover - defensive
        return "case"
    return "case" if fa == fb else "normalization"


def _classify(names: list[bytes]) -> str:
    """Classify a group of distinct names sharing a collision key.

    A group is labeled ``normalization`` if any pair inside it collides
    for normalization reasons; otherwise it is a pure ``case`` collision.
    """
    for i, first in enumerate(names):
        for other in names[i + 1:]:
            if _pair_kind(first, other) == "normalization":
                return "normalization"
    return "case"


def find_collisions(entries: list[IndexEntry]) -> list[Collision]:
    """Detect collisions among index entries."""
    collisions: list[Collision] = []

    # 1. Exact duplicates: identical (name, mode, sha) more than once.
    #    (Same name at different unmerged stages is normal merge state,
    #    not corruption, so stage is deliberately ignored here... in fact
    #    we require the full triple to repeat.)
    seen: dict[tuple[bytes, bytes, bytes], int] = {}
    for entry in entries:
        key = (entry.name, entry.mode, entry.sha)
        seen[key] = seen.get(key, 0) + 1
    for (name, _mode, _sha), count in seen.items():
        if count > 1:
            collisions.append(
                Collision(kind="exact-duplicate", names=[name, name])
            )

    # 2. Case / normalization collisions over distinct names.
    distinct: dict[bytes, None] = {}
    for entry in entries:
        distinct.setdefault(entry.name)
    groups: dict[tuple[str, object], list[bytes]] = {}
    for name in distinct:
        groups.setdefault(_collision_key(name), []).append(name)

    for names in groups.values():
        if len(names) < 2:
            continue
        collisions.append(
            Collision(kind=_classify(sorted(names)), names=sorted(names))
        )

    # Deterministic order: exact duplicates first, then by first filename.
    order = {"exact-duplicate": 0, "normalization": 1, "case": 2}
    collisions.sort(
        key=lambda c: (order.get(c.kind, 3), safe_display(c.names[0]))
    )
    return collisions


def scan(path: str = ".") -> ScanResult:
    """Scan the git index containing *path* for filename collisions."""
    root = repo_root(path)
    entries, gitlinks = read_index(path)
    return ScanResult(
        repo_root=root,
        files_scanned=len({e.name for e in entries}),
        gitlinks_skipped=gitlinks,
        collisions=find_collisions(entries),
    )
