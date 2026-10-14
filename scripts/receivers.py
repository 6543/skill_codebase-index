"""What a call's receiver is, read from the syntax around it.

`x.Poll()` names a method, not a declaration: which `Poll` runs depends on the
type of `x`. Name matching alone turned `ctx.Err()` on a `context.Context` into
an edge to a repo method called `Err`, and `paths.is_empty()` on a `Vec` into a
call of a free function `is_empty`. This module answers the narrower question
the syntax *can* answer: is the receiver this object, an imported module, a
type, a local whose type is written down, or something unknown.

It is deliberately textual and local: one declaration of the name before the
call, inside the enclosing function. Anything it cannot see is "unknown", and
an unknown receiver produces no edge.
"""

import functools
import re

# Languages where `a.b()` calls a method on a value. Nix, SQL, HTML, CSS and
# the shells have no receivers; their qualifiers are attribute paths, schemas,
# file paths or nothing at all.
RECEIVER_LANGS = frozenset(
    {
        "go",
        "python",
        "rust",
        "javascript",
        "typescript",
        "tsx",
        "vue",
        "svelte",
        "java",
        "kotlin",
        "swift",
        "c_sharp",
        "scala",
        "php",
        "ruby",
        "cpp",
        "c",
        "lua",
        "zig",
        "julia",
    }
)

# Languages where `x.f()` cannot reach a free function, so a method call needs
# a candidate that has an owner. JavaScript can (`app.render = function render`)
# and C can (a function pointer in a struct), so they are absent.
METHODS_ONLY = frozenset(
    {"go", "rust", "java", "kotlin", "swift", "c_sharp", "scala", "python", "cpp"}
)

SELF_RECEIVERS = frozenset(
    {"this", "self", "Self", "cls", "super", "base", "$this", "static", "parent"}
)

_SEPARATOR = re.compile(r"(\?\.|->|::|\.)")

# Words a type-before-name pattern can capture that are not types.
_NOT_TYPES = frozenset(
    {
        "return",
        "new",
        "else",
        "case",
        "throw",
        "delete",
        "goto",
        "await",
        "yield",
        "typeof",
        "var",
        "val",
        "let",
        "auto",
        "const",
        "final",
        "static",
        "in",
        "out",
        "ref",
        "using",
        "def",
        "func",
        "fn",
        "if",
        "for",
        "while",
        "and",
        "or",
        "not",
        "is",
        "as",
        "lambda",
        "mut",
        "pub",
        "public",
        "private",
        "protected",
        "readonly",
        "override",
        "virtual",
        "unsafe",
        "extern",
        "inline",
        "volatile",
        "register",
        "Optional",
        "Option",
    }
)

_COLON_TYPED = frozenset(
    {"python", "typescript", "tsx", "vue", "svelte", "rust", "kotlin", "swift", "scala"}
)
_TYPE_FIRST = frozenset({"java", "c_sharp", "cpp", "c", "php"})


def split_receiver(qualified):
    """`a.b.c` -> ('a.b', '.'); `Foo::new` -> ('Foo', '::'); bare -> (None, None)."""
    parts = _SEPARATOR.split(qualified)
    if len(parts) < 3:
        return None, None
    return "".join(parts[:-2]).strip(), parts[-2]


def segments(receiver):
    """`self.app.cfg` -> ['self', 'app', 'cfg']."""
    return [p.strip() for p in _SEPARATOR.split(receiver)[::2] if p.strip()]


def normalise_type(raw):
    """`*pkg.Store[T]` -> ('pkg', 'Store'); `Store::new` -> (None, 'Store')."""
    raw = raw.strip().lstrip("*&?").strip()
    raw = re.split(r"[<\[(]", raw, maxsplit=1)[0].strip()
    parts = [p for p in re.split(r"::|\.|\\", raw) if p]
    if not parts:
        return None, ""
    # `Store::new` / `Store.create`: a constructor call, the type is before it.
    if len(parts) > 1 and parts[-1][:1].islower() and parts[-2][:1].isupper():
        parts = parts[:-1]
    qualifier = parts[-2] if len(parts) > 1 else None
    return qualifier, parts[-1]


def type_matches(name, scope_text, lang):
    """-> [(position, written type)] for every declaration of *name* in
    *scope_text*, in source order."""
    if not name or not re.fullmatch(r"\$?[A-Za-z_][\w]*", name):
        return []
    if lang == "php" and not name.startswith("$"):
        return []  # a bare word before `->` is not a PHP variable
    found = []
    for pattern in _type_patterns(name, lang):
        for match in pattern.finditer(scope_text):
            word = match.group(1)
            if word.split(".")[0].split("::")[0] not in _NOT_TYPES:
                found.append((match.start(), word))
    return sorted(found)


def local_type(name, scope_text, lang):
    """Type of *name* as written in *scope_text* (function source up to the
    call), or None. The declaration nearest the call wins."""
    found = type_matches(name, scope_text, lang)
    return found[-1][1] if found else None


@functools.lru_cache(maxsize=4096)
def _type_patterns(name, lang):
    """Compiled declaration patterns for one name in one language."""
    n = re.escape(name)
    patterns = []
    if lang in _COLON_TYPED:
        patterns.append(
            rf"(?<![\w.$\"']){n}\s*\??\s*:\s*(?:&\s*(?:'\w+\s+)?(?:mut\s+)?|\*\s*)?"
            rf"(?:readonly\s+|dyn\s+|impl\s+|mut\s+)?([A-Za-z_][\w.:]*)"
        )
    if lang == "go":
        patterns += [
            rf"(?<![\w.]){n}(?:\s*,\s*\w+)*\s+(?:\*|\[\]|\.\.\.)*([A-Za-z_][\w.]*)",
            rf"(?<![\w.]){n}(?:\s*,\s*\w+)*\s*:?=\s*&?([A-Za-z_][\w.]*)\s*\{{",
            rf"(?<![\w.]){n}\s*:?=\s*new\(\s*([A-Za-z_][\w.]*)",
        ]
    # Construction: `s = Store()`, `x = new Foo<T>(`, `let s = Store::new(`.
    patterns.append(
        rf"(?<![\w.$]){n}\s*:?=\s*(?:new\s+)?([A-Z][\w.:]*)\s*(?:<[^<>;]*>)?\s*[({{]"
    )
    if lang in _TYPE_FIRST:
        patterns.append(
            rf"(?<![\w.$])\??([A-Za-z_\\][\w.:\\]*)\s*(?:<[^;(){{}}]*>)?(?:\s*[*&]+\s*|\s+){n}\s*[=;,)]"
        )
    return tuple(re.compile(p) for p in patterns)


def declaration_positions(name, scope_text):
    """Positions where *name* is assigned or bound in *scope_text*."""
    n = re.escape(name)
    return [
        m.start()
        for m in re.finditer(
            rf"(?<![\w.]){n}(?:\s*,\s*\w+)*\s*(?::=|=(?!=))|\b(?:var|let|const)\s+{n}\b",
            scope_text,
        )
    ]


def declared_locally(name, scope_text):
    """Whether *name* is assigned or bound in the scope before the call: a
    local shadowing a package of the same name."""
    return bool(declaration_positions(name, scope_text))
