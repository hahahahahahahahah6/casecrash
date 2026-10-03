# casecrash

**Git can track two files your Mac cannot check out.**

`README.md` and `readme.md` can sit side by side in a git repo — until
someone on macOS or Windows clones it and the checkout explodes. The same
goes for Unicode lookalikes: NFC `café` vs NFD `café` look identical in
your terminal but are different byte sequences, and macOS treats them as
the same file.

casecrash reads your git index and flags every pair of tracked files that
would collide on a case-insensitive or Unicode-normalizing filesystem —
*before* it breaks a teammate's checkout.

## Quickstart

```bash
pip install casecrash
cd your-repo
casecrash
```

```
casecrash: found 2 filename collision(s) in /tmp/demorepo (5 files scanned):

[normalization] collide after Unicode normalization (macOS):
  'cafe\u0301.md'  [NFD]
  'caf\u00e9.md'  [NFC]
  -> breaks on: macOS

[case] collide on case-insensitive filesystems (macOS, Windows):
  'README.md'
  'readme.md'
  -> breaks on: macOS, Windows

Fix: rename one of each colliding pair before a macOS/Windows user clones this repo.  'git mv' the odd one out and commit.
```

No collisions? Exit 0 and a clean bill of health:

```
casecrash: no filename collisions in the git index (128 files scanned).
```

## What it detects

| Kind | Example | Breaks on |
|---|---|---|
| `case` | `README.md` vs `readme.md`, `É` vs `é` | macOS, Windows |
| `normalization` | NFC `café` vs NFD `café` | macOS (APFS compares names without regard to normalization) |
| `exact-duplicate` | identical index entry twice | everywhere (index corruption) |

Detection is done on the git index (`git ls-files`), not the working
tree, so it works on any OS — a Linux CI runner can catch a collision
that would only bite macOS users. Filenames are handled as raw bytes, so
undecodable names can never crash the tool. Submodules are skipped (and
counted).

## CI usage

casecrash exits **1** when collisions are found, **0** when clean, **2** on
errors — so it drops straight into any pipeline:

```yaml
# .github/workflows/casecrash.yml
name: casecheck
on: [push, pull_request]
jobs:
  casecrash:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install casecrash
      - run: casecrash
```

JSON output for scripting:

```bash
casecrash --format json
```

## Why this happens

Git was born on case-sensitive Linux filesystems and its index happily
stores `File.txt` and `file.txt` as two separate entries. macOS and
Windows disagree: checking out the second name silently overwrites the
first (or the clone fails outright). Normalization collisions are
sneakier — macOS's HFS+/APFS normalizes filenames, so two
byte-different, canonically-equivalent names are the same file there.

This bites real projects: agents and scripts that generate files with
slightly different casings, Unicode filenames pasted from different
platforms, and the occasional CVE (see
[CVE-2021-21300](https://github.blog/security/vulnerability-research/github-security-lab-cve-2021-21300/),
where git's own checkout could be tricked on case-insensitive
filesystems).

## Install

```bash
pip install casecrash
```

Requires Python 3.9+ and git. Zero dependencies.

## License

MIT
