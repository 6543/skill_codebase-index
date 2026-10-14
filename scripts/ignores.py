"""Gitignore-aware directory walking.

Written from the gitignore pattern spec rather than adapted from an existing
implementation, and deliberately small: it covers the pattern forms that
actually appear in real ignore files and treats anything it cannot interpret as
"do not ignore", so a parsing gap can only ever cost extra files in the index,
never silently drop real source.

Semantics implemented:
  - one ruleset per directory, inherited by subdirectories
  - last matching rule wins, so a later `!pattern` re-includes
  - a leading `/` anchors to the directory holding the ignore file
  - a trailing `/` matches directories only
  - `**` spans path separators, `*` and `?` do not
  - a pattern with no `/` matches at any depth
"""

import fnmatch
import os
import re

IGNORE_FILENAMES = (".gitignore",)


class Rule:
    __slots__ = ("base", "dir_only", "negated", "regex")

    def __init__(self, pattern, base):
        self.base = base
        self.negated = pattern.startswith("!")
        if self.negated:
            pattern = pattern[1:]
        self.dir_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
        pattern = pattern.lstrip("/")
        self.regex = re.compile(_translate(pattern, anchored))

    def matches(self, relpath, is_dir):
        if self.dir_only and not is_dir:
            return False
        return self.regex.match(relpath) is not None


def _translate(pattern, anchored):
    """Convert a gitignore pattern to a regex against a '/'-joined relative path."""
    out = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i):
            out.append("(?:/.*)?")
            i += 3
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[":
            end = pattern.find("]", i)
            if end == -1:
                out.append(re.escape(char))
                i += 1
            else:
                out.append(pattern[i : end + 1])
                i = end + 1
        else:
            out.append(re.escape(char))
            i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    return f"{prefix}{body}(?:/.*)?$"


def load_rules(directory):
    """Read the ignore files directly inside *directory*. Returns a rule list."""
    rules = []
    for name in IGNORE_FILENAMES:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf8", errors="replace") as handle:
                lines = handle.read().split("\n")
        except OSError:
            continue
        for raw in lines:
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            # An escaped '#' or '!' is a literal first character.
            line = line.removeprefix("\\")
            try:
                rules.append(Rule(line, directory))
            except re.error:
                continue  # unparseable pattern: fail open, do not ignore
    return rules


def is_ignored(path, is_dir, rules):
    """Last-match-wins across the inherited rule stack."""
    verdict = False
    relative = {}  # one relpath per ignore file, not per rule: 365-rule files exist
    for rule in rules:
        if rule.base not in relative:
            try:
                rel = os.path.relpath(path, rule.base)
            except ValueError:
                rel = ".."
            relative[rule.base] = rel.replace(os.sep, "/")
        rel = relative[rule.base]
        if rel.startswith(".."):
            continue
        if rule.matches(rel, is_dir):
            verdict = not rule.negated
    return verdict


def walk(root, on_dir_rules=None):
    """os.walk, honouring every .gitignore found along the way.

    Yields (dirpath, filenames) with ignored entries already removed. The `.git`
    directory is always pruned: it is never source and is not always listed in
    the repo's own ignore file.
    """
    root = os.path.abspath(root)
    inherited = {root: load_rules(root)}
    for dirpath, dirnames, filenames in os.walk(root):
        rules = inherited.get(dirpath, [])
        own = load_rules(dirpath) if dirpath != root else []
        if own:
            rules = rules + own
            inherited[dirpath] = rules
        if on_dir_rules is not None:
            on_dir_rules(dirpath, rules)

        kept_dirs = []
        for name in dirnames:
            if name == ".git":
                continue
            full = os.path.join(dirpath, name)
            if is_ignored(full, True, rules):
                continue
            kept_dirs.append(name)
            inherited[full] = rules
        dirnames[:] = kept_dirs

        kept_files = [
            name
            for name in filenames
            if not is_ignored(os.path.join(dirpath, name), False, rules)
        ]
        yield dirpath, kept_files


def matches_any(name, patterns):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
