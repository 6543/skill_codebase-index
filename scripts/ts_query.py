#!/usr/bin/env python3
"""ts_query -- structural code search and indexing via tree-sitter.

Unlike grep, this parses source into a syntax tree, so it can answer questions
about *structure*: which type owns a method, what an interface requires, what a
declaration's doc comment says, what a struct's fields and tags are. Results are
whole declarations rather than matching lines.

Commands
  map     <root>                repo index: languages, files, symbol counts, entrypoints
  outline <file>                module header, imports, types (+members), consts, defs
  decl    <Name> <root>         full source of a declaration, with its doc comment
  refs    <Name> <root>         call sites, keeping the full qualifier expression
  iface   <Name> <root>         interface / trait / protocol member list
  methods <Type> <root>         method set of a type (receivers, impl blocks, class body)
  fields  <Type> <root>         struct / class members: name, type, tags, docs
  consts  <root>                const / static / enum-value declarations
  todos   <root>                TODO / FIXME / HACK comments, attributed to their decl
  impls   <Iface> <root>        types whose method set covers an interface
  trace   <Name> <root>         multi-hop call graph, with unresolved edges listed
  effects <root>                async spawns, channel ops, wire boundaries, callbacks
  complexity <root>             cyclomatic complexity per function, worst first
  cycles  <root>                circular imports between files
  langs                         supported languages and which grammars are installed
  debug-ast <file> [depth]      dump node types -- use when adding a language

Filters (any command taking <root>)
  --tests          include test files (default: excluded)
  --mocks          include generated mocks and generated code (default: excluded)
  --lang=<key>     restrict to one language (see `langs`)
  --json           machine-readable output
  --no-gitignore   do not read .gitignore files
  --min=<n>        complexity: only report functions at or above <n>
  --max-len=<n>    cycles: longest cycle to search for (default 6)
  --depth=<n>      trace: hops to follow (default 3); debug-ast: tree depth
  --callers        trace: follow inbound edges instead of outbound
  --both           trace: follow edges in both directions
  --kind=<k>       effects: only spawn, channel, boundary or callback
  --max-bytes=<n>  skip source files larger than n bytes (default 2000000)

Paths may be a file or a directory. Directories are walked recursively.
"""

import importlib
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _bootstrapped_python():
    """The python of the environment bootstrap.py set up, if there is one."""
    home = os.environ.get("CODEBASE_INDEX_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", "")
        if os.name == "nt"
        else os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
        "codebase-index",
    )
    try:
        with open(os.path.join(home, "current.json")) as handle:
            python = json.load(handle).get("python", "")
    except (OSError, ValueError):
        return None
    return python if os.path.exists(python) else None


try:
    from tree_sitter import Language, Parser
except ImportError:
    # Run with whatever `python3` is at hand: if bootstrap.py has set up an
    # environment, continue in it rather than failing. The marker stops a loop
    # if that environment is itself broken.
    _target = _bootstrapped_python()
    if _target and not os.environ.get("CODEBASE_INDEX_REEXEC"):
        os.environ["CODEBASE_INDEX_REEXEC"] = "1"
        os.execv(_target, [_target, os.path.abspath(__file__), *sys.argv[1:]])
    sys.exit(
        "tree_sitter not installed in this Python. Set up an isolated environment "
        "(works offline from the bundled wheels):\n"
        f"  python3 {os.path.join(_HERE, 'bootstrap.py')} --profile common\n"
        "then run this command again: it switches to that environment by itself."
    )

sys.path.insert(0, _HERE)
import ignores
from languages import (
    ALL,
    ARGUMENT_NODES,
    BY_EXT,
    BY_KEY,
    CSS,
    JAVASCRIPT,
    SHEBANG_INTERPRETERS,
    TYPESCRIPT,
    CONTRACT_NODES,
    DECISION_NODES,
    DECISION_OPERATORS,
    GENERATED_MARKERS,
    LOCKFILES,
    MOCK_PATTERNS,
    REFERENCE_NODES,
    SENSITIVE_NAMES,
    SENSITIVE_PREFIXES,
    SENSITIVE_SUFFIXES,
    SERIALIZATION_CALLEES,
    SKIP_DIRS,
    SPAWN_CALLEES,
    SPAWN_NODES,
    TEST_PATTERNS,
    TEST_PREFIXES,
    TODO_MARKERS,
    canonical_key,
)

# Parse results are cached for the lifetime of this process only. Several
# commands walk the same tree twice (method_sets, then impls), and repo-wide
# commands would otherwise re-parse every file. Nothing is written to disk: the
# index is a helper for the current session, not a database to keep in sync.
_PARSE_CACHE = {}
_CACHE_LIMIT = 4000

_LANG_CACHE = {}
_PARSER_CACHE = {}
_MISSING = set()
_PARSE_ERRORS = set()


# --------------------------------------------------------------------------
# grammar loading
# --------------------------------------------------------------------------


def get_parser(spec):
    """Return a Parser for a LangSpec, or None if its grammar isn't installed."""
    if spec.key in _PARSER_CACHE:
        return _PARSER_CACHE[spec.key]
    try:
        mod = importlib.import_module(spec.module)
        lang = Language(getattr(mod, spec.entry)())
    except (ImportError, AttributeError):
        _MISSING.add(spec.module)
        _PARSER_CACHE[spec.key] = None
        return None
    parser = Parser(lang)
    _LANG_CACHE[spec.key] = lang
    _PARSER_CACHE[spec.key] = parser
    return parser


def spec_for(path):
    """LangSpec for a file: by extension, else by shebang; None if unsupported.

    Only files without an extension are opened here, and callers check
    is_sensitive() first, so a key file is never read to look for a shebang.
    """
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext == ".h":
        return BY_KEY["cpp"] if _looks_like_cpp(path) else BY_KEY["c"]
    if ext:
        return BY_EXT.get(ext)
    key = _shebang_language(path)
    return BY_KEY.get(key) if key else None


_SHEBANG = re.compile(rb"^#!\s*(\S+)((?:\s+\S+)*)")


def _shebang_language(path):
    try:
        with open(path, "rb") as handle:
            first = handle.readline(256)
    except OSError:
        return None
    match = _SHEBANG.match(first)
    if not match:
        return None
    words = [os.path.basename(match.group(1))] + match.group(2).split()
    if words[0] == b"env":
        # `#!/usr/bin/env -S pwsh -NoProfile`: the interpreter is the first
        # word after env that is not one of env's own flags.
        words = [w for w in words[1:] if not w.startswith(b"-")] or [b""]
    interpreter = words[0].decode("utf8", "replace")
    interpreter = re.sub(r"\d+(\.\d+)*$", "", interpreter) or interpreter
    return SHEBANG_INTERPRETERS.get(interpreter) or SHEBANG_INTERPRETERS.get(
        words[0].decode("utf8", "replace")
    )


# `.h` is shared by C and C++. A header is read as C++ only when it uses
# something C cannot express; `extern "C"` guards are C headers made safe for
# C++ callers and do not count.
_CPP_HEADER = re.compile(
    rb"^\s*(?:namespace\s+\w|template\s*<|class\s+\w+[^;\n]*\{|"
    rb"(?:public|private|protected)\s*:|#include\s*<(?:iostream|string|vector|memory|map)>)",
    re.MULTILINE,
)


def _looks_like_cpp(path):
    try:
        with open(path, "rb") as handle:
            head = handle.read(65536)
    except OSError:
        return False
    return _CPP_HEADER.search(head) is not None


SCRIPT_BLOCK = re.compile(
    rb"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>", re.DOTALL | re.IGNORECASE
)
STYLE_BLOCK = re.compile(
    rb"<style(?P<attrs>[^>]*)>(?P<body>.*?)</style\s*>", re.DOTALL | re.IGNORECASE
)
_ATTR = re.compile(rb"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


def _attrs(raw):
    return {
        m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or b"").lower()
        for m in _ATTR.finditer(raw)
    }


def _inline_script_spec(attrs):
    """Grammar for an inline <script>, or None for data blocks and templates."""
    kind = attrs.get(b"type", b"")
    if attrs.get(b"lang") in (b"ts", b"typescript") or kind in (
        b"text/typescript",
        b"application/typescript",
    ):
        return TYPESCRIPT
    if kind in (b"", b"module", b"text/javascript", b"application/javascript"):
        return JAVASCRIPT
    return None  # application/json, importmap, text/x-template, ...


def read_segments(path, spec):
    """Return [(source_bytes, line_offset, spec)] -- one entry per parseable region.

    A plain source file is one region. A single-file component is one region per
    `<script>` block: Vue 3 routinely has two (`<script setup>` for the logic and
    a plain `<script>` for options such as `defineOptions` or a named export),
    and Svelte's `context="module"` block is the same shape. Reading only the
    first block silently lost whichever one happened to come second.

    An HTML page is the document itself plus each inline `<script>` and
    `<style>`, parsed with the JavaScript/TypeScript and CSS grammars at their
    real line offsets, so a function defined in a page is found like any other.
    """
    with open(path, "rb") as handle:
        raw = handle.read()

    def offset_of(match):
        return raw[: match.start("body")].count(b"\n")

    if spec.outline_hook == "html":
        out = [(raw, 0, spec)]
        for pattern, pick in (
            (SCRIPT_BLOCK, _inline_script_spec),
            (
                STYLE_BLOCK,
                lambda a: CSS if a.get(b"type", b"text/css") == b"text/css" else None,
            ),
        ):
            for match in pattern.finditer(raw):
                inner = pick(_attrs(match.group("attrs")))
                if inner is not None and match.group("body").strip():
                    out.append((match.group("body"), offset_of(match), inner))
        return out
    if spec.key == "sql":
        return _sql_segments(raw, spec)
    if not spec.sfc:
        return [(raw, 0, spec)]
    return [
        (match.group("body"), offset_of(match), spec)
        for match in SCRIPT_BLOCK.finditer(raw)
        if match.group("body").strip()
    ]


def _sql_segments(raw, spec):
    """The whole file if it parses cleanly, else one region per statement.

    Splitting keeps an unsupported statement from swallowing the rest of the
    file (see _sql_statement_spans), but a clean dump of 50,000 INSERTs is far
    cheaper as one parse. Line offsets are counted incrementally: recounting
    from the start for every statement made large dumps quadratic.
    """
    parser = get_parser(spec)
    if parser is not None:
        tree = parser.parse(raw)
        if not count_real_errors(tree.root_node, raw, spec):
            return [(raw, 0, spec)]
    segments, line, position = [], 0, 0
    for start, end in _sql_statement_spans(raw):
        line += raw.count(b"\n", position, start)
        position = start
        segments.append((raw[start:end], line, spec))
    return segments


def parse(path):
    """-> [(source, tree, spec, line_offset)], one per region; [] if unsupported."""
    if path in _PARSE_CACHE:
        return _PARSE_CACHE[path]
    result = _parse_uncached(path)
    if len(_PARSE_CACHE) < _CACHE_LIMIT:
        _PARSE_CACHE[path] = result
    return result


def _parse_uncached(path):
    spec = spec_for(path)
    if spec is None or get_parser(spec) is None:
        return []
    out = []
    for src, offset, seg_spec in read_segments(path, spec):
        parser = get_parser(seg_spec)
        if parser is None or not src.strip():
            continue
        spec = seg_spec
        tree = parser.parse(src)
        errors = count_real_errors(tree.root_node, src, spec)
        if errors and spec.key == "go":
            tree, errors = _reparse_go_new(parser, src, tree, errors, spec)
        if errors and spec.key == "css":
            tree, errors = _reparse_css_at_rules(parser, src, tree, errors, spec)
        if errors and spec.key == "sql":
            tree, errors = _reparse_plpgsql(parser, src, tree, errors, spec)
        if errors:
            _PARSE_ERRORS.add(path)
        out.append((src, tree, spec, offset))
    return out


def count_real_errors(root, src, spec):
    """How many parse failures are worth warning about.

    tree-sitter recovers from errors rather than failing, so a partly wrong tree
    looks exactly like a right one. The Nix grammar flags a *missing* identifier
    after a legal trailing comma in a formal argument set, which is valid Nix and
    appears in most NixOS modules; that one artefact is suppressed and every
    other error is reported.
    """
    if not root.has_error:
        return 0
    count = 0
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur.type == "ERROR":
            count += 1
        elif cur.is_missing:
            if spec.key != "nix" or not _harmless_nix_comma(cur, src):
                count += 1
        elif cur.has_error:
            stack.extend(cur.children)
    return count


# Go 1.26 lets the builtin `new` take any expression (`new(int(x))`, `new(42)`).
# tree_sitter_go, up to and including 0.25.0 and upstream master at the time of
# writing, hardcodes `new(` Type `)` and reports everything else as an error.
# Until the grammar catches up, a file that fails to parse is parsed a second
# time with the offending `new` identifiers swapped for a same-length stand-in,
# which turns them into ordinary calls. Byte offsets and line numbers do not
# move; text() swaps the name back so the stand-in never reaches the output.
_GO_NEW = re.compile(rb"(?<![\w.])new(?=\s*\()")
_GO_NEW_MASK = "\ua66e".encode("utf8")  # a letter no source uses, 3 bytes like `new`


def _reparse_go_new(parser, src, tree, errors, spec):
    """-> (tree, error count), repaired where `new(expr)` was the only problem."""
    if _GO_NEW_MASK in src:
        return tree, errors
    masked = bytearray(src)
    for match in _GO_NEW.finditer(src):
        # `new([]int)` only parses as the builtin, so calls the grammar already
        # accepted keep their name. Only the ones it choked on are masked.
        node = tree.root_node.descendant_for_byte_range(match.start(), match.end())
        call = node.parent if node is not None else None
        accepted = (
            call is not None and call.type == "call_expression" and not call.has_error
        )
        if not accepted:
            masked[match.start() : match.end()] = _GO_NEW_MASK
    if masked == src:
        return tree, errors
    retry = parser.parse(bytes(masked))
    retry_errors = count_real_errors(retry.root_node, src, spec)
    return (retry, retry_errors) if retry_errors < errors else (tree, errors)


# tree-sitter-sql covers PostgreSQL and MySQL well and other dialects partly,
# and its error recovery is poor at statement level: one CREATE PROCEDURE it
# does not know can turn the next thousand lines into a single ERROR node and
# lose every table after it. A SQL file is therefore parsed one statement at a
# time (read_segments), so an unsupported statement costs only itself.
_SQL_DELIMITER = re.compile(
    rb"[ \t]*delimiter[ \t]+(\S+)[ \t]*\r?(?:\n|$)", re.IGNORECASE
)
_SQL_BATCH = re.compile(rb"[ \t]*(?:/|go)[ \t]*\r?(?:\n|$)", re.IGNORECASE)
_DOLLAR_TAG = re.compile(rb"\$[A-Za-z_]*\$")


def _sql_special(delimiter):
    """Bytes the splitter must stop at; everything between is skipped in C."""
    return re.compile(rb"--|/\*|['\"`$\n]|" + re.escape(delimiter))


def _skip_quoted(src, i, quote):
    """Index just past the string opened at src[i]; doubled quotes and (except
    for backticks) backslash escapes stay inside it."""
    stop = re.compile(re.escape(quote) if quote == b"`" else rb"\\|" + re.escape(quote))
    j = i + 1
    while True:
        m = stop.search(src, j)
        if m is None:
            return len(src)
        if m.group(0) == b"\\":
            j = m.end() + 1
        elif src[m.end() : m.end() + 1] == quote:
            j = m.end() + 1
        else:
            return m.end()


def _sql_statement_spans(src):
    """-> [(start, end)] byte spans of top-level statements.

    Splits on the current delimiter (`;`, or what a MySQL `DELIMITER` line set)
    outside quotes, comments and `$tag$` bodies, and on lines holding only `/`
    (Oracle) or `GO` (T-SQL), which end a batch. Backslash escapes inside
    quotes are honoured (MySQL, PostgreSQL E'' strings).
    """
    spans, start, i, n = [], 0, 0, len(src)
    delimiter = b";"
    special = _sql_special(delimiter)
    at_line_start = True
    while i < n:
        if at_line_start:
            at_line_start = False
            custom = _SQL_DELIMITER.match(src, i)
            batch = None if custom else _SQL_BATCH.match(src, i)
            if custom or batch:
                spans.append((start, i))
                if custom:
                    delimiter = custom.group(1)
                    special = _sql_special(delimiter)
                start = i = (custom or batch).end()
                at_line_start = True
                continue
        m = special.search(src, i)
        if m is None:
            break
        i, token = m.start(), m.group(0)
        if token == b"\n":
            i += 1
            at_line_start = True
        elif token in (b"'", b'"', b"`"):
            i = _skip_quoted(src, i, token)
        elif token == b"--":
            close = src.find(b"\n", i)
            i = n if close == -1 else close + 1
            at_line_start = True
        elif token == b"/*":
            close = src.find(b"*/", i + 2)
            i = n if close == -1 else close + 2
        elif token == b"$":
            tag = _DOLLAR_TAG.match(src, i)
            if tag:
                close = src.find(tag.group(0), tag.end())
                i = n if close == -1 else close + len(tag.group(0))
            else:
                i += 1  # `$1` parameter
        else:  # the delimiter
            i = m.end()
            spans.append((start, i))
            start = i
    spans.append((start, n))
    return [(a, b) for a, b in spans if _has_code(src[a:b])]


def _has_code(piece):
    """Whether a span holds more than whitespace and comments."""
    stripped = re.sub(rb"--[^\n]*|/\*.*?\*/", b"", piece, flags=re.DOTALL)
    return bool(stripped.strip(b" \t\r\n;"))


# tree-sitter-css knows block at-rules generically but rejects a statement
# at-rule whose prelude it does not recognise -- Tailwind v4's `@plugin "x";`,
# `@reference "./a.css";`, `@custom-variant dark (...)` -- and the error runs
# into the following rule, whose selector then comes out garbled. On failure,
# the preludes of such statements are blanked (same length) and the file is
# parsed again; outline_css reads the at-rule text back from the source.
_CSS_STATEMENT_AT_RULE = re.compile(
    rb"(?m)^([ \t]*@(?!import\b|charset\b|namespace\b)[\w-]+)([^;{}\n]*)(;)"
)


def _reparse_css_at_rules(parser, src, tree, errors, spec):
    masked = _CSS_STATEMENT_AT_RULE.sub(
        lambda m: m.group(1) + b" " * len(m.group(2)) + m.group(3), src
    )
    if masked == src:
        return tree, errors
    retry = parser.parse(masked)
    retry_errors = count_real_errors(retry.root_node, masked, spec)
    return (retry, retry_errors) if retry_errors < errors else (tree, errors)


# tree-sitter-sql parses the body of a `LANGUAGE sql` function, but PL/pgSQL
# control flow (DECLARE, IF ... THEN, LOOP, RETURN, :=) is not SQL, and the first
# IF turns the rest of the body into an error, hiding the tables it reads. On
# failure, that control syntax inside `$tag$` bodies is blanked (same length,
# newlines kept), PERFORM becomes the same-length SELECT, and the statement is
# parsed again. BEGIN ... END stays: the grammar accepts a statement list there,
# so the plain SQL statements between the control lines then parse.
# Patterns are anchored at line starts so a CASE WHEN ... THEN inside a query is
# left alone. decl prints from the source, so the blanks never show.
_PLPGSQL_BODY = re.compile(rb"(\$[A-Za-z_]*\$)(.*?)\1", re.DOTALL)
_PLPGSQL_CONTROL = [
    # Each pattern is anchored at a line start with [ \t]* (never \s*, which
    # would run across blank lines) and bounded by `;`, so matching stays
    # linear on large dumps.
    re.compile(p, re.IGNORECASE | re.MULTILINE)
    for p in (
        rb"^[ \t]*DECLARE\b(?:[^\n]*\n)*?(?=[ \t]*BEGIN\b)",
        rb"^[ \t]*(?:ELSE|EXCEPTION|LOOP)\b",
        rb"^[ \t]*(?:IF|ELSIF|ELSEIF|WHEN)\b[^;]*?\bTHEN\b",
        rb"^[ \t]*END[ \t]+(?:IF|LOOP|CASE)[ \t]*;?",
        rb"^[ \t]*(?:RETURN|RAISE|EXIT|CONTINUE|GET[ \t]+DIAGNOSTICS|OPEN|FETCH|CLOSE)\b(?![ \t]+QUERY)[^;]*;",
        rb"^[ \t]*RETURN[ \t]+QUERY\b",
        rb"^[ \t]*FOR(?:EACH)?[ \t]+[^;\n]*?[ \t]IN\b",
        rb"^[ \t]*WHILE\b[^;]*?\bLOOP\b",
        rb"^[ \t]*[\w.]+[ \t]*:=[^;]*;",
        rb"\bINTO[ \t]+(?:STRICT[ \t]+)?[\w.]+(?:[ \t]*,[ \t]*[\w.]+)*(?=\s+FROM\b)",
    )
]
_PLPGSQL_LOOP_END = re.compile(rb"\bLOOP\b(?=[ \t]*$)", re.IGNORECASE | re.MULTILINE)


def _blank(match):
    return re.sub(rb"[^\n]", b" ", match.group(0))


def _mask_plpgsql(body):
    body = re.sub(rb"(?im)^([ \t]*)PERFORM\b", lambda m: m.group(1) + b"SELECT ", body)
    for pattern in _PLPGSQL_CONTROL:
        body = pattern.sub(_blank, body)
    # `FOR r IN SELECT ... LOOP`: the query stays, LOOP becomes its `;`.
    return _PLPGSQL_LOOP_END.sub(b"   ;", body)


def _reparse_plpgsql(parser, src, tree, errors, spec):
    masked = _PLPGSQL_BODY.sub(
        lambda m: m.group(1) + _mask_plpgsql(m.group(2)) + m.group(1), src
    )
    if masked == src:
        return tree, errors
    retry = parser.parse(masked)
    retry_errors = count_real_errors(retry.root_node, masked, spec)
    return (retry, retry_errors) if retry_errors < errors else (tree, errors)


def _harmless_nix_comma(node, src):
    if node.type != "identifier":
        return False
    before = src[: node.start_byte].rstrip()
    after = src[node.start_byte :].lstrip()
    return before.endswith(b",") and after.startswith(b"}")


# --------------------------------------------------------------------------
# node helpers
# --------------------------------------------------------------------------


def text(node):
    if node is None:
        return ""
    return node.text.replace(_GO_NEW_MASK, b"new").decode("utf8", "replace")


def field(node, name):
    return text(node.child_by_field_name(name))


def line_of(src, node, offset=0):
    return src[: node.start_byte].count(b"\n") + 1 + offset


def walk(node, types):
    """Yield every descendant whose type is in `types` (pre-order)."""
    if not types:
        return
    stack = [node]
    wanted = set(types)
    while stack:
        cur = stack.pop()
        if cur.is_named and cur.type in wanted:
            yield cur
        stack.extend(reversed(cur.children))


def walk_shallow(node, types, barriers):
    """Like walk(), but does not descend past a nested container.

    A method body or an inner class belongs to that inner scope, so recursing
    into it would attribute its locals to the outer type.
    """
    wanted, stop = set(types), set(barriers)
    stack = list(reversed(node.children))
    while stack:
        cur = stack.pop()
        if not cur.is_named:
            continue
        if cur.type in wanted:
            yield cur
            continue
        if cur.type in stop:
            continue
        stack.extend(reversed(cur.children))


def _unquote(name):
    """SQL's "Audit Log", `users`, [dbo]: the quotes are not part of the name."""
    if len(name) >= 2 and (name[0], name[-1]) in (('"', '"'), ("`", "`"), ("[", "]")):
        return name[1:-1]
    return name


def _child(node, *types):
    for child in node.named_children:
        if child.type in types:
            return child
    return None


def _html_attr(element, wanted):
    """Value of one attribute on an HTML element's start tag, or ''."""
    tag = _child(element, "start_tag", "self_closing_tag")
    for attr in walk(tag, ("attribute",)) if tag is not None else ():
        name = _child(attr, "attribute_name")
        if name is not None and text(name).lower() == wanted:
            value = _child(attr, "quoted_attribute_value", "attribute_value")
            return text(value).strip("\"'") if value is not None else ""
    return ""


def resolve_name(node, spec):
    """Declaration name, handling grammars that don't expose a `name` field."""
    if spec.name_hook == "sql_object":
        ref = _child(node, "object_reference")
        if ref is not None:
            return _unquote(field(ref, "name") or text(ref))
        ident = _child(node, "identifier")
        return _unquote(text(ident)) if ident is not None else "<anonymous>"

    if spec.name_hook == "html_id":
        # Only elements with an id are declarations: that is what a fragment
        # link, a label's `for`, or a script's getElementById points at.
        return _html_attr(node, "id")

    if spec.name_hook == "css":
        if node.type == "rule_set":
            return " ".join(text(_child(node, "selectors")).split())
        if node.type == "keyframes_statement":
            return field(node, "name") or text(_child(node, "keyframes_name"))
        if node.type == "declaration":
            prop = text(_child(node, "property_name"))
            return prop if prop.startswith("--") else ""

    direct = node.child_by_field_name("name")
    if direct is not None:
        return text(direct)

    if spec.name_hook == "powershell":
        named = _child(node, "function_name", "simple_name")
        if named is not None:
            return text(named)

    if spec.name_hook == "c_declarator":
        # C/C++: int foo(int a) {} -> name is buried in the declarator chain.
        chain = _c_declarator_chain(node)
        if chain:
            return text(chain[-1])

    if spec.name_hook == "nix_attrpath":
        attrpath = node.child_by_field_name("attrpath")
        if attrpath is not None:
            return text(attrpath)

    if spec.name_hook == "zig_parent":
        # `pub const Task = struct {...}` -- the struct node is anonymous and
        # the name lives on the enclosing declaration.
        parent = node.parent
        while parent is not None and parent.type in ("variable_declaration",):
            for child in parent.named_children:
                if child.type == "identifier":
                    return text(child)
            parent = parent.parent

    if spec.name_hook == "julia_nested":
        # Julia wraps the name one level down, in `type_head` or `signature`.
        for child in node.named_children:
            if child.type in ("type_head", "signature"):
                for ident in walk(child, ("identifier",)):
                    return text(ident)

    if spec.name_hook == "js_assigned":
        # const handler = () => {} / handler: function () {}
        parent = node.parent
        while parent is not None and parent.type in (
            "variable_declarator",
            "lexical_declaration",
            "pair",
            "assignment_expression",
            "export_statement",
            "variable_declaration",
        ):
            named = parent.child_by_field_name("name") or parent.child_by_field_name(
                "key"
            )
            if named is not None:
                return text(named)
            parent = parent.parent
        if node.type in ("arrow_function", "function_expression"):
            # An unnamed callback (computed(() => ...), .map(x => ...)). These are
            # control flow, not API surface, so they are dropped rather than
            # reported as "<anonymous>".
            return ""

    for child in node.named_children:
        if child.type.endswith("identifier"):
            return text(child)
    return "<anonymous>"


_C_NAMES = frozenset(
    {
        "identifier",
        "field_identifier",
        "type_identifier",
        "operator_name",
        "destructor_name",
        "namespace_identifier",
    }
)


def _c_declarator_chain(node):
    """Declarator nodes from a C/C++ declaration down to its name, name last.

    `parenthesized_declarator` and `pointer_declarator` wrap the real
    declarator without always naming the field, and `app::run` is a
    `qualified_identifier` whose name is the `name` field, not its first child.
    """
    chain = []
    cur = node.child_by_field_name("declarator")
    while cur is not None:
        chain.append(cur)
        if cur.type in _C_NAMES:
            return chain
        if cur.type in ("qualified_identifier", "template_function", "template_type"):
            cur = cur.child_by_field_name("name")
            continue
        nxt = cur.child_by_field_name("declarator")
        if nxt is None:
            nxt = next(
                (
                    c
                    for c in cur.named_children
                    if c.type not in ("parameter_list", "type_qualifier")
                ),
                None,
            )
        cur = nxt
    return chain if chain and chain[-1].type in _C_NAMES else []


_C_SPECIFIERS = frozenset(
    {"struct_specifier", "union_specifier", "enum_specifier", "class_specifier"}
)


def is_declaration(node, spec):
    """Whether a `types`/`defs` node really declares something.

    In C and C++ `struct node *next` names a struct with the same node type
    that defines one; only a specifier with a body is a definition. An
    anonymous struct under a typedef is shown as the typedef instead.
    """
    if spec.name_hook != "c_declarator" or node.type not in _C_SPECIFIERS:
        return True
    if node.child_by_field_name("body") is None:
        return False
    # Anonymous: shown as the typedef or the field that holds it.
    return not (
        node.child_by_field_name("name") is None
        and node.parent is not None
        and node.parent.type in ("type_definition", "field_declaration")
    )


def walk_decls(root, spec, kinds):
    """walk() restricted to nodes that are real declarations."""
    return (n for n in walk(root, kinds) if is_declaration(n, spec))


def decl_names(node, spec):
    """Every name a declaration answers to.

    Usually just its name. A schema-qualified SQL table is also its qualified
    name, and a CSS rule with a selector list is each of its selectors, so
    `decl .card` finds `.card, .panel > h2 { ... }`.
    """
    name = resolve_name(node, spec)
    names = {name} if name else set()
    if spec.name_hook == "sql_object":
        ref = _child(node, "object_reference")
        if ref is not None:
            names.add(".".join(_unquote(part) for part in text(ref).split(".")))
    if spec.name_hook == "css" and node.type == "rule_set":
        selectors = _child(node, "selectors")
        if selectors is not None:
            names.update(
                " ".join(text(sel).split()) for sel in selectors.named_children
            )
    return names


def canon(name, spec):
    """Comparison key for a name: case-folded where the language ignores case."""
    return name.casefold() if spec.case_insensitive else name


def name_matches(node, spec, name):
    wanted = canon(name, spec)
    return any(canon(candidate, spec) == wanted for candidate in decl_names(node, spec))


def bare_name(expression, spec):
    """The name a reference resolves by: trailing member, file name, or fragment.

    A stylesheet link names a file and `#settings` names an element id, so for
    HTML and CSS the qualifier is a path, not a member access.
    """
    if spec.key in ("html", "css"):
        target = expression.strip().strip("\"'")
        if target.startswith("#") and spec.key == "html":
            return target[1:]
        if "/" in target or "." in target:
            return os.path.basename(target.split("?")[0].split("#")[0].rstrip("/"))
        return target
    if spec.key == "sql":
        return _unquote(split_qualifier(expression))
    return split_qualifier(expression)


def parameters_of(node, spec):
    """Parameter list as source text, normalising the grammars that differ."""
    direct = field(node, "parameters") or field(node, "formal_parameters")
    if direct:
        return " ".join(direct.split())

    # SQL routines and PowerShell class methods keep a parameter list node.
    listed = _child(node, "function_arguments", "class_method_parameter_list")
    if listed is not None:
        params = " ".join(text(listed).split())
        return params if params.startswith("(") else f"({params})"
    if node.type == "class_method_definition":
        return "()"

    if spec.name_hook == "powershell":
        # `function f($a)` or a `param(...)` block at the top of the body.
        # Names only: attributes and types make the full block unreadable.
        declared = _child(node, "function_parameter_declaration")
        block = declared or next(iter(walk(node, ("param_block",))), None)
        if block is not None:
            names = [
                text(v)
                for v in walk(block, ("variable",))
                if v.parent.type in ("script_parameter",)
            ]
            return "(" + ", ".join(names) + ")"

    if spec.name_hook == "c_declarator":
        # C/C++ keep the parameter list inside the declarator chain. The one
        # nearest the name is the function's own: in `int (*pick(void))(int)`
        # the outer `(int)` belongs to the pointer pick returns.
        found = [
            n for n in _c_declarator_chain(node) if n.type == "function_declarator"
        ]
        if found:
            return " ".join(field(found[-1], "parameters").split())

    # Swift and friends emit one `parameter` node per argument.
    parts = [text(c) for c in node.named_children if c.type.endswith("parameter")]
    if parts:
        return "(" + ", ".join(" ".join(p.split()) for p in parts) + ")"
    return ""


def describe(node, spec):
    """How a declaration is written in its own language.

    Nix has one declaration form -- `name = expr;` -- and no functions in the
    C sense, so rendering a binding as `name()` invented a call signature that
    does not exist in the source.
    """
    if spec.key == "fish":
        # `function name --on-event x -a who`: options carry the calling
        # convention and event wiring, so the header line is the signature.
        return " ".join(text(node).split("\n")[0].split())
    if spec.key == "nix":
        name = resolve_name(node, spec)
        value = node.child_by_field_name("expression")
        if value is None:
            return name
        if value.type == "function_expression":
            params = value.child_by_field_name("formals") or value.child_by_field_name(
                "universal"
            )
            args = " ".join(text(params).split()) if params is not None else "..."
            if len(args) > 70:
                # A package function can take 60+ named arguments; the full list
                # is noise in an outline and `decl` shows it in full anyway.
                count = args.count(",") + 1
                args = f"{{ {count} args }}"
            return f"{name} = {args}: ..."
        return f"{name} = {preview(value, maxlines=1, width=60)}"
    return signature(node, spec)


def _c_return_type(node):
    """`const char **` for `const char **f(...)`: qualifiers, type, pointers."""
    qualifiers = [text(c) for c in node.named_children if c.type == "type_qualifier"]
    stars = 0
    for link in _c_declarator_chain(node):
        if link.type == "function_declarator":
            break
        if link.type == "pointer_declarator":
            stars += 1
    base = " ".join(qualifiers + [field(node, "type")])
    return f"{base} {'*' * stars}" if stars else base


def signature(node, spec):
    """One-line rendering: receiver, name, params, return type."""
    name = resolve_name(node, spec)
    recv = field(node, "receiver")
    params = parameters_of(node, spec)
    if (
        spec.name_hook == "c_declarator"
        and node.child_by_field_name("type") is not None
    ):
        ret = _c_return_type(node)
    else:
        ret = field(node, "return_type") or field(node, "result") or field(node, "type")
    parts = [f"{recv} " if recv else "", name, params or "()"]
    ret = " ".join(ret.split())
    if len(ret) > 80:
        # A macro the grammar cannot see (`FMT_EXPORT class x {...}`) can turn
        # a whole class body into the "return type". One line stays one line.
        ret = ret[:77] + "..."
    if ret and ret != name:
        parts.append(f" -> {ret}")
    return "".join(parts).strip().replace("\n", " ")


def doc_comment(node, spec):
    """Contiguous comments directly above a declaration, or a Python docstring."""
    if spec.name_hook == "python_doc":
        body = node.child_by_field_name("body")
        if body is not None and body.named_child_count:
            first = body.named_children[0]
            if first.type == "expression_statement" and first.named_child_count:
                literal = first.named_children[0]
                if literal.type == "string":
                    return [
                        ln.strip()
                        for ln in text(literal).strip("\"'").strip().split("\n")
                        if ln.strip()
                    ]

    out = []
    prev = _prev_token(node)
    boundary = node.start_point[0]
    comment_types = set(spec.comment) - {"string"}
    while prev is not None and prev.type in comment_types:
        # Grammars disagree on whether a line comment's extent includes the
        # trailing newline (Rust's does, Go's does not), so accept the comment
        # if it merely spans the line directly above rather than ending on it.
        if not prev.start_point[0] <= boundary - 1 <= prev.end_point[0]:
            break
        # Multi-line block comments (`/** */`, PowerShell `<# #>` help) become
        # one entry per line, so callers can indent them consistently.
        out.extend(reversed([ln.rstrip() for ln in text(prev).strip().split("\n")]))
        boundary = prev.start_point[0]
        prev = _prev_token(prev)

    if (
        not out
        and node.parent is not None
        and node.parent.start_point[0] == node.start_point[0]
        and node.parent.named_children[:1] == [node]
    ):
        # Several grammars wrap the named declaration in an outer node carrying
        # the keyword -- Go's `type_declaration` around `type_spec`, TypeScript's
        # `export_statement` around the class. The comment is a sibling of the
        # wrapper, so it is invisible from the inner node. Only a wrapper opening
        # on the same line, with this node as its first child, is followed, so
        # this cannot walk out of a block and steal an unrelated comment.
        return doc_comment(node.parent, spec)
    if spec.doc_hook == "fish_description":
        return list(reversed(out)) + _fish_description(node)
    return list(reversed(out))


def _prev_token(node):
    """Previous sibling, stepping over whitespace tokens (Fish keeps newlines)."""
    prev = node.prev_sibling
    while prev is not None and not prev.is_named and not prev.text.strip():
        prev = prev.prev_sibling
    return prev


def _fish_description(node):
    """`function f -d 'text'`: Fish's own docstring, shown by `functions -D`."""
    options = [
        child
        for index, child in enumerate(node.children)
        if node.field_name_for_child(index) == "option"
    ]
    for flag, value in zip(options, options[1:]):
        if text(flag) in ("-d", "--description"):
            return [text(value).strip("\"'")]
    return []


def receiver_type(node, spec):
    """Bare type name a method is attached to; '' for free functions."""
    if spec.key == "go":
        recv = node.child_by_field_name("receiver")
        if recv is None:
            return ""
        for child in recv.named_children:
            typ = child.child_by_field_name("type")
            if typ is not None:
                return text(typ).lstrip("*&")
        return ""

    # Otherwise: nearest enclosing type declaration (class body, impl block).
    owners = set(spec.types) | set(spec.impl_blocks)
    cur = node.parent
    while cur is not None:
        if cur.type in owners:
            if cur.type in spec.impl_blocks:
                return field(cur, "type").lstrip("*&")
            return resolve_name(cur, spec)
        cur = cur.parent
    return ""


def enclosing_decl(node, spec):
    cur = node
    interesting = set(spec.defs) | set(spec.types) | set(spec.impl_blocks)
    while cur is not None:
        if cur.type in spec.impl_blocks:
            # `impl Trait for Type` -- the interesting half is the type.
            return f"impl {field(cur, 'type') or resolve_name(cur, spec)}"
        if cur.type in interesting:
            name = resolve_name(cur, spec)
            # A CSS `color:` declaration or an HTML element without an id is
            # not a declaration; the enclosing rule or element with a name is.
            if name or not spec.outline_hook:
                return f"{cur.type} {name}"
        cur = cur.parent
    return "<file scope>"


def type_body(node):
    if node.type == "type_definition":
        # `typedef struct { ... } item;` -- the fields live on the struct.
        inner = node.child_by_field_name("type")
        return (
            type_body(inner)
            if inner is not None and inner.type in _C_SPECIFIERS
            else None
        )
    for name in ("body", "declaration_list", "field_declaration_list"):
        found = node.child_by_field_name(name)
        if found is not None:
            return found
    for child in node.named_children:
        if child.type.endswith(("body", "declaration_list", "list")):
            return child
    return None


def members_of(node, spec, src, offset):
    """-> (kind, rendered, doc, line) for each member of a type declaration."""
    body = type_body(node) or node
    out = []
    wanted = tuple(set(spec.defs) | set(spec.members))
    barriers = set(spec.types) | set(spec.defs) | set(spec.impl_blocks)
    for child in walk_shallow(body, wanted, barriers):
        line = line_of(src, child, offset)
        doc = doc_comment(child, spec)
        # CSS declarations are both members of a rule and, when custom
        # properties, definitions. Inside a rule they read as members.
        if child.type in spec.defs and child.type not in spec.members:
            out.append(("method", signature(child, spec), doc, line))
        else:
            rendered = " ".join(text(child).split())
            out.append(("field", rendered.rstrip(";,"), doc, line))
    return out


# --------------------------------------------------------------------------
# file walking
# --------------------------------------------------------------------------


_CLASS_TEST = re.compile(r"[a-z0-9_](?:Test|Tests)$")  # JsonParserTest, StoreTests


def is_test(path):
    base = os.path.basename(path)
    lowered = base.lower()
    return (
        lowered.startswith(TEST_PREFIXES)
        or any(p in lowered for p in TEST_PATTERNS)
        or _CLASS_TEST.search(base.split(".")[0]) is not None
    )


def is_mock(path):
    lowered = path.replace(os.sep, "/").lower()
    return any(p in lowered for p in MOCK_PATTERNS)


def is_sensitive(path):
    """Credentials and keys, excluded before the file is ever opened."""
    name = os.path.basename(path).lower()
    _, ext = os.path.splitext(name)
    return (
        name in SENSITIVE_NAMES
        or ext in SENSITIVE_SUFFIXES
        or name.startswith(SENSITIVE_PREFIXES)
    )


def is_generated(path, head_bytes=2048):
    """Whether a file announces itself as machine-written in its header.

    Naming conventions for generated code are inconsistent enough that a
    filename check alone misses most of it. The header marker is a convention
    the generators themselves follow, so it catches mockery, protoc, sqlc and
    stringer output without a per-tool pattern list.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(head_bytes)
    except OSError:
        return False
    return any(marker in head for marker in GENERATED_MARKERS)


# A source file this large is minified, vendored, or a data blob. Parsing one
# costs seconds and its declarations are never the answer to the question.
_MAX_BYTES = 2_000_000


def iter_files(root, opts):
    """Yield source files under *root*, honouring .gitignore.

    A repo's own ignore file already encodes which paths are generated or
    vendored, and it stays correct as the project changes -- far better than a
    hardcoded directory list, which is kept only as a backstop for build
    output that is committed anyway.
    """
    if os.path.isfile(root):
        yield root
        return

    walker = (
        os.walk(root)
        if opts.get("no_gitignore")
        else ((d, f) for d, f in ignores.walk(root))
    )
    for entry in walker:
        if len(entry) == 3:  # plain os.walk
            dirpath, dirnames, filenames = entry
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        else:
            dirpath, filenames = entry

        parts = set(os.path.relpath(dirpath, root).split(os.sep))
        if parts & SKIP_DIRS:
            continue
        if not opts["mocks"] and "mocks" in parts:
            continue

        for name in sorted(filenames):
            if name in LOCKFILES:
                continue
            path = os.path.join(dirpath, name)
            # Before spec_for(), which may open an extensionless file to read
            # its shebang: a credential must never be opened at all.
            if is_sensitive(path):
                continue
            spec = spec_for(path)
            if spec is None:
                continue
            if opts["lang"] and spec.key != opts["lang"]:
                continue
            if not opts["tests"] and is_test(path):
                continue
            if not opts["mocks"] and (is_mock(path) or is_generated(path)):
                continue
            try:
                if os.path.getsize(path) > int(opts.get("max_bytes", _MAX_BYTES)):
                    continue
            except OSError:
                continue
            yield path


def iter_parsed(root, opts):
    skip = opts.get("skip_langs", ())
    for path in iter_files(root, opts):
        for segment in parse(path):
            if segment[2].key not in skip:
                yield (path,) + segment


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


# Languages that share a module system and call each other freely, so a caller
# in one may legitimately reference a declaration in another.
_LANG_FAMILY = {
    "typescript": "js",
    "tsx": "js",
    "javascript": "js",
    "vue": "js",
    "svelte": "js",
    "c": "c",
    "cpp": "c",
}


def family(lang_key):
    return _LANG_FAMILY.get(lang_key, lang_key)


def cmd_map(root, opts, emit):
    stats = {}
    # Degree here is a proxy for architectural centrality: how many distinct
    # files mention a symbol. It is intentionally cheap and name-based, which
    # is why common short names are filtered out below.
    mentions = {}
    declared = {}
    declared_count = {}
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk_decls(
            tree.root_node, spec, tuple(set(spec.defs) | set(spec.types))
        ):
            name = resolve_name(node, spec)
            if name and len(name) > 3:
                declared.setdefault(name, (path, line_of(src, node, offset), spec.key))
                declared_count[name] = declared_count.get(name, 0) + 1
        for _node, qualified in iter_calls(tree.root_node, spec):
            bare = bare_name(qualified, spec)
            # A qualified call whose receiver is a package name is most often
            # a standard-library call (fmt.Errorf, log.Debug). Crediting it to
            # a same-named local declaration is simply wrong, and stdlib verbs
            # are exactly the names that collide, so only unqualified calls
            # count toward fan-in.
            if len(bare) > 3 and bare == qualified:
                mentions.setdefault(bare, {}).setdefault(family(spec.key), set()).add(
                    path
                )

    # A file parsed as several regions (SQL statements, Vue script blocks) is
    # still one file: counts and density are summed per path.
    per_file = {}
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        bucket = stats.setdefault(
            spec.key, {"files": 0, "lines": 0, "defs": 0, "types": 0, "biggest": []}
        )
        bucket["lines"] += src.count(b"\n") + 1
        ndefs = sum(1 for n in walk(tree.root_node, spec.defs) if resolve_name(n, spec))
        ntypes = sum(1 for _ in walk_decls(tree.root_node, spec, spec.types))
        bucket["defs"] += ndefs
        bucket["types"] += ntypes
        key = (spec.key, path)
        per_file[key] = per_file.get(key, 0) + ndefs + ntypes
    for (lang, path), count in per_file.items():
        stats[lang]["files"] += 1
        stats[lang]["biggest"].append((count, path))

    if opts["json"]:
        emit(
            {
                k: {
                    "files": v["files"],
                    "lines": v["lines"],
                    "defs": v["defs"],
                    "types": v["types"],
                    "densest": [p for _, p in sorted(v["biggest"], reverse=True)[:5]],
                }
                for k, v in stats.items()
            }
        )
        return

    if not stats:
        emit("no supported source files found")
        return
    emit(f"{'lang':<12}{'files':>7}{'lines':>9}{'defs':>7}{'types':>7}")
    for key in sorted(stats, key=lambda k: -stats[k]["lines"]):
        val = stats[key]
        emit(
            f"{key:<12}{val['files']:>7}{val['lines']:>9}{val['defs']:>7}{val['types']:>7}"
        )
    emit("\ndensest files (most declarations -- usually the API surface)")
    combined = [item for v in stats.values() for item in v["biggest"]]
    for count, path in sorted(combined, reverse=True)[:10]:
        emit(f"  {count:>4}  {path}")

    # Fan-in is only attributable when the name has exactly one declaration in
    # the repo. With two or more, this cannot say which one is being called.
    # Fan-in is only attributable when the name has exactly one declaration in
    # the repo, and callers must be in the same language: `toRef` is both a Go
    # helper here and Vue's built-in, and crediting one to the other is wrong.
    hubs = []
    for name, per_lang in mentions.items():
        if declared_count.get(name) != 1:
            continue
        callers = per_lang.get(family(declared[name][2]), set())
        if len(callers) > 1:
            hubs.append((len(callers), name))
    if hubs:
        emit("\nmost-referenced symbols (unambiguous name, called from many files)")
        for count, name in sorted(hubs, reverse=True)[:12]:
            path, line, _ = declared[name]
            emit(f"  {count:>4} files  {name}  ({os.path.relpath(path, root)}:{line})")


def any_ancestor_is_def(node, spec):
    cur = node.parent
    while cur is not None:
        if cur.type in spec.defs:
            return True
        cur = cur.parent
    return False


def preview(node, maxlines=6, width=110):
    """Single- or few-line rendering of a declaration body."""
    lines = [ln.strip() for ln in text(node).split("\n") if ln.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0][:width]
    head = " ".join(lines[:maxlines])
    if len(lines) > maxlines or len(head) > width:
        return head[:width].rstrip() + f" ... (+{len(lines) - 1} lines)"
    return head


def render_const(node, spec):
    """Const groups list every value; a const holding a function body does not.

    An enum-style group is exactly what someone greps for, so it is worth the
    lines. `const handler = () => {...}` is a function in disguise and gets
    collapsed instead.
    """
    if any(walk(node, spec.defs)):
        return preview(node)
    lines = [ln.strip() for ln in text(node).split("\n") if ln.strip()]
    if len(lines) == 1:
        return lines[0]
    return "\n" + "\n".join(f"        {ln}" for ln in lines)


def cmd_outline(path, opts, emit):
    if os.path.isdir(path):
        emit(
            f"`outline` takes a file; {path} is a directory. use `map` on it, then outline its densest files."
        )
        return
    segments = parse(path)
    if not segments:
        emit(f"unsupported or unparseable: {path}")
        return
    host = spec_for(path)
    for index, segment in enumerate(segments):
        _, _, spec, offset = segment
        if host.outline_hook == "html":
            if index:
                tag = "style" if spec.key == "css" else "script"
                emit(f"\n--- inline <{tag}> at line {offset + 1} ({spec.key}) ---")
        elif host.key == "sql":
            continue  # merged below: one outline per file, not per statement
        elif len(segments) > 1:
            emit(f"--- script block {index + 1} of {len(segments)} ---")
        outline_segment(segment, emit)
    if host.key == "sql":
        merged = {}
        for segment in segments:
            for title, lines in outline_sections(segment).items():
                merged.setdefault(title, []).extend(lines)
        emit_sections(merged, emit)
    if host.sfc:
        with open(path, "rb") as handle:
            if re.search(rb"<template[\s>]", handle.read()):
                emit(
                    "\n(template not analysed: only <script> blocks are parsed, so "
                    "components, directives and bindings used in the template "
                    "leave no edges)"
                )


def type_heading(node, spec, kind):
    """`kind name` for a type in an outline. SQL reads as `table app.orders`."""
    if spec.name_hook == "sql_object":
        ref = _child(node, "object_reference")
        label = node.type.replace("create_", "").replace("_", " ")
        return f"{label} {text(ref) if ref is not None else resolve_name(node, spec)}"
    return f"{kind} {resolve_name(node, spec)}"


_LANDMARKS = frozenset(
    {"header", "nav", "main", "section", "article", "aside", "footer", "form", "dialog"}
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_RESOURCE_KIND = {
    "script": "script",
    "img": "image",
    "a": "link",
    "form": "form",
    "iframe": "frame",
    "source": "media",
    "video": "media",
    "audio": "media",
}


def outline_html(src, tree, spec, offset, emit):
    """Document outline: title, landmarks and ids, headings, linked resources.

    Elements are not functions, so the code outline's sections would all be
    empty or wrong. What a reader of an unfamiliar page wants first is its
    shape and what it pulls in.
    """
    root_node = tree.root_node
    elements = list(walk(root_node, ("element", "script_element", "style_element")))

    def tag_of(element):
        tag = _child(element, "start_tag", "self_closing_tag")
        return text(_child(tag, "tag_name")).lower() if tag is not None else ""

    for element in elements:
        if tag_of(element) == "title":
            emit(f"title: {' '.join(text(_child(element, 'text')).split())}")
            break

    shown = set()
    structure = []
    for element in elements:
        tag = tag_of(element)
        ident = _html_attr(element, "id")
        if not (ident or tag in _LANDMARKS or tag in _HEADINGS):
            continue
        depth = 0
        cur = element.parent
        while cur is not None:
            if (cur.start_byte, cur.end_byte) in shown:
                depth += 1
            cur = cur.parent
        shown.add((element.start_byte, element.end_byte))
        label = f"{tag} #{ident}" if ident else tag
        if tag in _HEADINGS:
            label += " " + " ".join(text(_child(element, "text")).split())[:80]
        structure.append(f"  {line_of(src, element, offset)}: {'  ' * depth}{label}")
    if structure:
        emit("\nstructure")
        for line in structure:
            emit(line)

    resources = []
    for attr in walk(root_node, ("attribute",)):
        target = _html_ref(attr, spec)
        if not target or target.startswith("#") and attr.parent is None:
            continue
        name = text(_child(attr, "attribute_name")).lower()
        if name == "for":
            continue
        tag_node = _child(attr.parent, "tag_name") if attr.parent is not None else None
        tag = text(tag_node).lower()
        kind = _RESOURCE_KIND.get(tag, name)
        if tag == "link":
            element = attr.parent.parent if attr.parent is not None else None
            rel = _html_attr(element, "rel") if element is not None else ""
            kind = " ".join(rel.lower().split()) if rel else "link"
        resources.append(f"  {line_of(src, attr, offset)}: {kind} {target}")
    if resources:
        emit("\nresources and links")
        for line in resources:
            emit(line)

    inline = []
    for element in walk(root_node, ("script_element", "style_element")):
        body = _child(element, "raw_text")
        if body is None or not text(body).strip():
            continue
        tag = "style" if element.type == "style_element" else "script"
        start = line_of(src, element, offset)
        end = start + text(element).count("\n")
        inline.append(f"  {start}-{end}: inline <{tag}>")
    if inline:
        emit("\ninline code (outlined below with its own grammar)")
        for line in inline:
            emit(line)


def outline_css(src, tree, spec, offset, emit):
    """Imports, custom properties, rules (with @media context), keyframes."""
    root_node = tree.root_node
    imports = [
        (line_of(src, node, offset), import_targets(node, spec, ""))
        for node in walk(root_node, spec.imports)
    ]
    if imports:
        emit("imports")
        for line, targets in imports:
            emit(f"  {line}: {' '.join(targets)}")

    tokens = [
        node for node in walk(root_node, ("declaration",)) if resolve_name(node, spec)
    ]
    if tokens:
        emit("\ncustom properties")
        for node in tokens:
            rule = node.parent.parent if node.parent is not None else None
            where = (
                f"   [in {resolve_name(rule, spec)}]"
                if rule is not None and rule.type == "rule_set"
                else ""
            )
            emit(
                f"  {line_of(src, node, offset)}: {' '.join(text(node).split()).rstrip(';')}{where}"
            )

    rules = list(walk(root_node, ("rule_set",)))
    if rules:
        emit("\nrules")
        for node in rules:
            for docline in doc_comment(node, spec):
                emit(f"    {docline}")
            context = []
            cur = node.parent
            while cur is not None:
                if cur.type in ("media_statement", "supports_statement", "at_rule"):
                    context.append(text(cur).split("{")[0].strip())
                cur = cur.parent
            where = f"   (in {' / '.join(reversed(context))})" if context else ""
            emit(f"  {line_of(src, node, offset)}: {resolve_name(node, spec)}{where}")

    def source_text(node):
        # From the source, not node.text: the prelude may have been blanked
        # for parsing (see _reparse_css_at_rules).
        return src[node.start_byte : node.end_byte].decode("utf8", "replace")

    other = [
        (node, f"@keyframes {resolve_name(node, spec)}")
        for node in walk(root_node, ("keyframes_statement",))
    ] + [
        (node, " ".join(source_text(node).split("{")[0].split()).rstrip(";"))
        for node in walk(root_node, ("at_rule",))
    ]
    if other:
        emit("\nat-rules")
        for node, label in sorted(other, key=lambda item: item[0].start_byte):
            emit(f"  {line_of(src, node, offset)}: {label}")


_OUTLINE_HOOKS = {"html": outline_html, "css": outline_css}


_SECTION_ORDER = (
    "",
    "imports",
    "types",
    "impl blocks",
    "const/var",
    "prototypes",
    "declarations",
    "funcs",
)


def outline_segment(segment, emit):
    src, tree, spec, offset = segment
    if spec.outline_hook:
        _OUTLINE_HOOKS[spec.outline_hook](src, tree, spec, offset, emit)
        return
    emit_sections(outline_sections(segment), emit)


def emit_sections(sections, emit):
    for title in _SECTION_ORDER:
        lines = sections.get(title)
        if not lines:
            continue
        if title == "imports":
            emit(f"\nimports ({sum(1 for ln in lines if not ln.startswith('    '))})")
        elif title:
            emit(f"\n{title}")
        for line in lines:
            emit(line)


def outline_sections(segment):
    """-> {section title: [lines]} for one parsed region; "" holds the header.

    Collected rather than printed so that a file parsed as several regions
    (SQL, one statement each) still reads as one outline.
    """
    src, tree, spec, offset = segment
    sections = {}

    def add(title, line):
        sections.setdefault(title, []).append(line)

    root_node = tree.root_node

    for node in walk(root_node, spec.module_decl):
        header = text(node).split("\n")[0].split("{")[0].strip()
        add("", header[:120])
        break

    for line in src.decode("utf8", "replace").split("\n")[:15]:
        stripped = line.strip()
        if stripped.startswith(("//go:build", "# type:")):
            add("", f"buildtag {stripped}")

    for n in walk(root_node, spec.imports):
        if is_import_node(n, spec):
            add("imports", f"  {preview(n, maxlines=1, width=100)}")

    types = list(walk_decls(root_node, spec, spec.types))
    for node in types:
        kind = "interface" if node.type in spec.ifaces else node.type
        for docline in doc_comment(node, spec):
            add("types", f"    {docline}")
        add(
            "types", f"  {line_of(src, node, offset)}: {type_heading(node, spec, kind)}"
        )
        for mkind, rendered, doc, _ in members_of(node, spec, src, offset):
            for docline in doc:
                add("types", f"        {docline}")
            add("types", f"        {'fn' if mkind == 'method' else '  '} {rendered}")

    # Rust puts methods in `impl` blocks, apart from the type. Without this
    # section they were owned by no listed container and vanished.
    for node in walk(root_node, spec.impl_blocks):
        trait = field(node, "trait")
        header = (
            f"impl {trait} for {field(node, 'type')}"
            if trait
            else f"impl {field(node, 'type')}"
        )
        add("impl blocks", f"  {line_of(src, node, offset)}: {header}")
        for mkind, rendered, doc, _ in members_of(node, spec, src, offset):
            for docline in doc:
                add("impl blocks", f"        {docline}")
            add(
                "impl blocks",
                f"        {'fn' if mkind == 'method' else '  '} {rendered}",
            )

    for node in walk(root_node, spec.consts):
        if is_file_const(node, spec):
            add(
                "const/var",
                f"  {line_of(src, node, offset)}: {render_const(node, spec)}",
            )
        elif is_prototype(node, spec) and at_file_scope(node, spec):
            # A header's API surface: listed like functions, apart from data.
            add(
                "prototypes", f"  {line_of(src, node, offset)}: {signature(node, spec)}"
            )
            for docline in doc_comment(node, spec):
                add("prototypes", f"        {docline}")

    containers = list(types) + list(walk(root_node, spec.impl_blocks))
    owned = {
        (m.start_byte, m.end_byte)
        for node in containers
        for m in walk(type_body(node) or node, spec.defs)
    }
    free = [
        n
        for n in walk(root_node, spec.defs)
        if (n.start_byte, n.end_byte) not in owned and resolve_name(n, spec)
    ]
    if spec.top_level_only:
        # A deeply nested config tree has hundreds of bindings and listing them
        # all buries the handful that describe the file. Keep the outermost
        # ones; `decl` still finds anything nested by name.
        free = [n for n in free if not any_ancestor_is_def(n, spec)]
    title = "declarations" if spec.top_level_only else "funcs"
    for node in sorted(free, key=lambda n: n.start_byte):
        add(title, f"  {line_of(src, node, offset)}: {describe(node, spec)}")
        for docline in doc_comment(node, spec):
            add(title, f"        {docline}")
    return sections


def cmd_decl(name, root, opts, emit):
    found = False
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk_decls(
            tree.root_node, spec, tuple(set(spec.defs) | set(spec.types))
        ):
            if not name_matches(node, spec, name):
                continue
            found = True
            owner = receiver_type(node, spec)
            label = f"{spec.key} {node.type}" + (f" on {owner}" if owner else "")
            emit(f"=== {path}:{line_of(src, node, offset)} ({label}) ===")
            for docline in doc_comment(node, spec):
                emit(docline)
            # From the source, not node.text: a retry parse may have blanked
            # syntax the grammar rejects (PL/pgSQL control flow, Go `new`).
            emit(src[node.start_byte : node.end_byte].decode("utf8", "replace"))
            emit("")
    if not found:
        emit(f"no declaration named {name!r} found under {root}")


def declaration_sites(name, root, opts):
    """Every declaration of *name*, as (path, line, owner)."""
    sites = []
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk_decls(
            tree.root_node, spec, tuple(set(spec.defs) | set(spec.types))
        ):
            if name_matches(node, spec, name) or name_matches(
                node, spec, query_key(name, spec)
            ):
                sites.append(
                    (path, line_of(src, node, offset), receiver_type(node, spec))
                )
    return sites


def path_distance(a, b):
    """Directory distance between two files: a longer shared prefix scores lower."""
    pa = os.path.normpath(a).split(os.sep)[:-1]
    pb = os.path.normpath(b).split(os.sep)[:-1]
    shared = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        shared += 1
    return (len(pa) - shared) + (len(pb) - shared)


# Member access separators. '-' is deliberately absent: it is a valid
# identifier character in Nix attribute names (mysql-replication) and in Lisp
# family languages, so splitting on it truncated real names.
_QUALIFIER = re.compile(r"(?:->|::|\.|:)")


def split_qualifier(expression):
    """Trailing member name of a possibly-qualified call expression."""
    return _QUALIFIER.split(expression)[-1].strip()


def enclosing_span(node, spec):
    """Byte span of the declaration containing *node*, or None."""
    interesting = set(spec.defs) | set(spec.types) | set(spec.impl_blocks)
    cur = node.parent
    while cur is not None:
        if cur.type in interesting:
            return (cur.start_byte, cur.end_byte)
        cur = cur.parent
    return None


def rank_candidates(candidates, call_site, prefer_same_file=True):
    """Order declaration candidates by how plausibly they are the callee.

    Two signals, both cheap and both purely positional -- this is not type
    resolution and does not pretend to be:
      1. Production code does not call into test fixtures, so test candidates
         are dropped for a non-test call site, and preferred for a test one.
      2. Nearer in the directory tree beats further away.
    """
    if not candidates:
        return candidates, "UNRESOLVED"
    if len(candidates) == 1:
        # One declaration with this name is still only a name match when it
        # lives in another file; only a same-file target is read off the syntax.
        return candidates, "EXTRACTED" if candidates[0][0] == call_site else "INFERRED"

    test_c = [c for c in candidates if is_test(c[0])]
    other_c = [c for c in candidates if not is_test(c[0])]

    if is_test(call_site):
        same_file = [c for c in test_c if c[0] == call_site]
        survivors = same_file or test_c or other_c
    else:
        survivors = other_c or candidates

    survivors = sorted(survivors, key=lambda c: (path_distance(c[0], call_site), c[0]))
    same_file = [c for c in survivors if c[0] == call_site]
    if same_file and prefer_same_file:
        return same_file, "EXTRACTED"
    if len(survivors) == 1:
        return survivors, "INFERRED"
    return survivors, "AMBIGUOUS"


def callee_expression(node, spec):
    """The expression naming what a call node invokes, or None.

    For a reference node (`ref_nodes`) it is what is being referred to. None
    also means "this node is not a call or reference after all", which is how
    the hooks drop a SQL table's own name or a CSS `color:` declaration.
    """
    if spec.call_hook:
        return _CALL_HOOKS[spec.call_hook](node, spec)
    fn = node.child_by_field_name("function") or node.child_by_field_name("name")
    if fn is None and node.named_children:
        fn = node.named_children[0]
    if fn is None:
        return None
    return " ".join(text(fn).split())


def _ps_call(node, spec):
    if node.type == "invokation_expression":
        # `$this.Helper(1)`, `[Worker]::new()`: no callee field, the callee is
        # everything before the argument list.
        args = _child(node, "argument_list")
        head = node.text[
            : (args.start_byte if args else node.end_byte) - node.start_byte
        ]
        return " ".join(head.decode("utf8", "replace").split()) or None
    operator = _child(node, "command_invokation_operator")
    if operator is not None:
        # `. lib.ps1` loads a file (an import) and `& script.ps1` runs one;
        # `& $cmd` calls whatever $cmd holds, which trace lists as dynamic.
        target = " ".join(
            text(_child(node, "command_name_expr", "command_name")).split()
        )
        return target if text(operator) == "&" and target.startswith("$") else None
    return field(node, "command_name") or None


_SQL_DECLARING = frozenset(
    {
        "create_table",
        "create_view",
        "create_materialized_view",
        "create_type",
        "create_function",
        "create_trigger",
        "create_sequence",
    }
)


def _sql_ref(node, spec):
    if node.type == "invocation":
        ref = _child(node, "object_reference")
        return text(ref) if ref is not None else None
    parent = node.parent
    if parent is None or parent.type == "invocation":
        return None  # counted once, as the invocation
    if parent.type in _SQL_DECLARING and _child(parent, "object_reference") == node:
        return None  # `CREATE TABLE app.orders`: the declaration, not a use
    return text(node)


_HTML_REF_ATTRS = frozenset(
    {"href", "src", "action", "formaction", "poster", "data", "for"}
)


def _html_ref(node, spec):
    name = text(_child(node, "attribute_name")).lower()
    if name not in _HTML_REF_ATTRS:
        return None
    value = _child(node, "quoted_attribute_value", "attribute_value")
    target = text(value).strip("\"'") if value is not None else ""
    if not target:
        return None
    return "#" + target if name == "for" else target  # `for` names an element id


def _css_ref(node, spec):
    if node.type == "import_statement":
        targets = import_targets(node, spec, "")
        return targets[0] if targets else None
    if node.type == "declaration":
        prop = text(_child(node, "property_name")).lower()
        if prop in ("animation", "animation-name"):
            value = _child(node, "plain_value")
            return text(value) if value is not None else None
        return None
    function = text(_child(node, "function_name")).lower()
    args = _child(node, "arguments")
    if function not in ("var", "url") or args is None or not args.named_children:
        return None
    return text(args.named_children[0]).strip("\"'")


_CALL_HOOKS = {
    "ps_call": _ps_call,
    "sql_ref": _sql_ref,
    "html_ref": _html_ref,
    "css_ref": _css_ref,
}


def is_reference(node, spec):
    """Whether a `calls` node names something rather than invoking it.

    The one SQL exception: `EXECUTE FUNCTION f()` in a trigger runs f, though
    the grammar spells it as a bare object reference.
    """
    if node.type not in spec.ref_nodes:
        return False
    if (
        spec.key == "sql"
        and node.parent is not None
        and node.parent.type == "create_trigger"
    ):
        before = node.prev_named_sibling
        return before is None or before.type not in (
            "keyword_function",
            "keyword_procedure",
        )
    return True


def iter_calls(root_node, spec):
    """-> (node, callee expression) for every call or reference in a tree."""
    for node in walk(root_node, spec.calls):
        qualified = callee_expression(node, spec)
        if qualified:
            yield node, qualified


def query_key(name, spec):
    """A user's query as references are matched: `app.orders` -> `orders`.

    Only for languages where a reference's bare name is not an identifier
    (SQL qualifiers, HTML/CSS paths and fragments). Elsewhere the query is
    taken literally, as it always was.
    """
    return bare_name(name, spec) if spec.key in ("sql", "html", "css") else name


def resolve_call(qualified, node, spec, path, src, offset, declarations):
    """Rank the declarations that could be the target of one call site."""
    pool = declarations
    # A qualified call such as `p.q.Poll` is dispatched on another receiver, so
    # the enclosing method of the same name is not the callee -- without this, a
    # wrapper method resolves to itself.
    if any(sep in qualified for sep in (".", "::", "->")):
        span = enclosing_span(node, spec)
        if span is not None:
            enclosing_line = src[: span[0]].count(b"\n") + 1 + offset
            pool = [
                c for c in declarations if not (c[0] == path and c[1] == enclosing_line)
            ] or declarations
    return rank_candidates(
        pool, path, prefer_same_file=_calls_own_scope(qualified, node, spec)
    )


# Receivers that mean "this object", so a same-file declaration is the one
# being called. `view.render()` names some other object, whose type the syntax
# does not give; a same-named method in this file is no evidence for it.
_SELF_RECEIVERS = frozenset(
    {"this", "self", "Self", "cls", "super", "base", "$this", "static", "parent"}
)


def _calls_own_scope(qualified, node, spec):
    if spec.key in ("sql", "html", "css") or not any(
        sep in qualified for sep in (".", "::", "->")
    ):
        return True  # bare call, or a qualifier that is a schema or a path
    head = re.split(r"->|::|\.", qualified)[0].strip()
    if head in _SELF_RECEIVERS:
        return True
    if spec.key == "go":
        # Go names its receiver freely: `func (q *fifo) Poll() { q.helper() }`.
        cur = node.parent
        while cur is not None and cur.type != "method_declaration":
            cur = cur.parent
        receiver = cur.child_by_field_name("receiver") if cur is not None else None
        for param in (
            walk(receiver, ("parameter_declaration",)) if receiver is not None else ()
        ):
            if field(param, "name") == head:
                return True
    return False


def argument_references(node, spec):
    """Bare references passed as arguments -- a callback handed to a registrar.

    `router.POST("/hook", api.PostHook)` creates no call edge to PostHook: the
    framework calls it later, from a stack that has no textual link to this
    line. Passing a function by name is the one part of that handoff that *is*
    visible in the syntax, so it is worth reporting as an indirect edge.
    """
    args = node.child_by_field_name("arguments") or node.child_by_field_name(
        "argument_list"
    )
    if args is None:
        for child in node.named_children:
            if child.type in ARGUMENT_NODES:
                args = child
                break
    if args is None:
        return []
    return [
        " ".join(text(child).split())
        for child in args.named_children
        if child.type in REFERENCE_NODES
    ]


def cmd_refs(name, root, opts, emit):
    declarations = declaration_sites(name, root, opts)
    hits = 0
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        wanted = canon(query_key(name, spec), spec)
        for node, qualified in iter_calls(tree.root_node, spec):
            if canon(bare_name(qualified, spec), spec) != wanted:
                continue
            hits += 1
            is_ref = is_reference(node, spec)
            shown = " ".join(text(node).split())[:70] if is_ref else f"{qualified}(...)"
            ranked, confidence = resolve_call(
                qualified, node, spec, path, src, offset, declarations
            )
            target = ""
            if ranked:
                best = ranked[0]
                owner = f"{best[2]}." if best[2] else ""
                target = f" -> {owner}{name} at {best[0]}:{best[1]}"
                if confidence == "AMBIGUOUS":
                    target += f" (+{len(ranked) - 1} more)"
            emit(
                f"{path}:{line_of(src, node, offset)}: {shown}"
                f"   [{enclosing_decl(node, spec)}]  "
                f"{'reference, ' if is_ref else ''}{confidence}{target}"
            )
    if not hits:
        emit(f"no calls to {name!r} found under {root}")
        return
    emit(
        "\nEXTRACTED = callee declared in this same file. "
        "INFERRED = one plausible declaration elsewhere. "
        "AMBIGUOUS = several survive ranking; verify before trusting."
    )


def cmd_iface(name, root, opts, emit):
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk_decls(tree.root_node, spec, spec.types):
            if not name_matches(node, spec, name):
                continue
            # Go puts the interface_type under the type_spec.
            target = node
            if spec.ifaces:
                inner = next(iter(walk(node, spec.ifaces)), None)
                if inner is not None:
                    target = inner
                elif node.type not in spec.ifaces:
                    continue
            emit(f"=== {path}:{line_of(src, node, offset)} interface {name} ===")
            for docline in doc_comment(node.parent or node, spec):
                emit(docline)
            for _, rendered, doc, _ in members_of(target, spec, src, offset):
                for docline in doc:
                    emit(f"  {docline}")
                emit(f"  {rendered}")


def method_sets(root, opts, skip_ifaces=False):
    """-> {type_name: [(path, line, method_name, signature, doc)]}"""
    sets = {}
    iface_names = set()
    if skip_ifaces:
        for _, _, tree, spec, _ in iter_parsed(root, opts):
            for node in walk(tree.root_node, spec.ifaces or ()):
                iface_names.add(resolve_name(node, spec))
                if node.parent is not None:
                    iface_names.add(resolve_name(node.parent, spec))
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(tree.root_node, spec.defs):
            owner = receiver_type(node, spec)
            if not owner or owner in iface_names:
                continue
            sets.setdefault(owner, []).append(
                (
                    path,
                    line_of(src, node, offset),
                    resolve_name(node, spec),
                    signature(node, spec),
                    doc_comment(node, spec),
                )
            )
    return sets


def cmd_methods(typename, root, opts, emit):
    entries = method_sets(root, opts).get(typename, [])
    if not entries:
        emit(f"no methods found on type {typename!r} under {root}")
        return
    for path, line, _, sig, doc in sorted(entries, key=lambda e: (e[0], e[1])):
        emit(f"{path}:{line}: {sig}")
        for docline in doc:
            emit(f"      {docline}")


def cmd_fields(typename, root, opts, emit):
    found = False
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk_decls(tree.root_node, spec, spec.types):
            if not name_matches(node, spec, typename):
                continue
            members = [
                m for m in members_of(node, spec, src, offset) if m[0] == "field"
            ]
            if not members:
                continue
            found = True
            emit(f"=== {path}:{line_of(src, node, offset)} {node.type} {typename} ===")
            for _, rendered, doc, line in members:
                for docline in doc:
                    emit(f"  {docline}")
                emit(f"  {line}: {rendered}")
    if not found:
        emit(f"no fields found on type {typename!r} under {root}")


def at_file_scope(node, spec):
    """True when no type or function declaration encloses this node.

    Class attributes and locals are reported as members of their owner, so
    surfacing them again as package constants would just be noise.
    """
    scopes = set(spec.types) | set(spec.defs) | set(spec.impl_blocks)
    cur = node.parent
    while cur is not None:
        if cur.type in scopes:
            return False
        cur = cur.parent
    return True


_PS_DATA_BARRIERS = frozenset(
    {
        "assignment_expression",
        "command",
        "hash_entry",
        "function_statement",
        "class_statement",
        "script_block",
    }
)


def is_prototype(node, spec):
    """A C/C++ declaration of a function without its body (`void f(int);`)."""
    return (
        spec.name_hook == "c_declarator"
        and node.type == "declaration"
        and any(n.type == "function_declarator" for n in _c_declarator_chain(node))
    )


def is_file_const(node, spec):
    """A file-scope constant, narrowed for grammars whose node is overloaded."""
    if not at_file_scope(node, spec) or is_prototype(node, spec):
        return False
    if spec.const_hook == "fish_set":
        return field(node, "name") == "set"
    if spec.const_hook == "ps_const" and node.type == "hash_entry":
        # Only the keys of a manifest-style top-level hashtable (.psd1). A
        # hashtable assigned to a variable or passed to a command is a value.
        cur = node.parent
        while cur is not None:
            if cur.type in _PS_DATA_BARRIERS:
                return False
            cur = cur.parent
    return True


def cmd_consts(root, opts, emit):
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(tree.root_node, spec.consts):
            if not is_file_const(node, spec):
                continue
            for docline in doc_comment(node, spec):
                emit(f"  {docline}")
            emit(f"{path}:{line_of(src, node, offset)}: {render_const(node, spec)}")


def cmd_todos(root, opts, emit):
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(
            tree.root_node, tuple(t for t in spec.comment if t != "string")
        ):
            body = text(node)
            if not any(marker in body for marker in TODO_MARKERS):
                continue
            emit(f"{path}:{line_of(src, node, offset)}: [{enclosing_decl(node, spec)}]")
            for line in body.split("\n"):
                emit(f"  {line.strip()}")


def cmd_impls(ifacename, root, opts, emit):
    required = None
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk_decls(tree.root_node, spec, spec.types):
            if not name_matches(node, spec, ifacename):
                continue
            target = next(iter(walk(node, spec.ifaces)), node)
            names = {
                rendered.split("(")[0].strip().split()[-1]
                for kind, rendered, _, _ in members_of(target, spec, src, offset)
                if kind == "method"
            }
            if names:
                required = names
    if not required:
        emit(f"no interface {ifacename!r} with methods found under {root}")
        return
    emit(f"interface {ifacename} requires: {', '.join(sorted(required))}\n")
    for typename, entries in sorted(method_sets(root, opts, skip_ifaces=True).items()):
        have = {e[2] for e in entries}
        if required <= have:
            extra = sorted(have - required)
            emit(f"{typename}  (extra: {', '.join(extra) if extra else 'none'})")
    emit(
        "\nnote: name-based method-set cover. It ignores signatures and does not "
        "expand embedded/inherited members, so verify before relying on it."
    )


# --------------------------------------------------------------------------
# call graph
# --------------------------------------------------------------------------


def enclosing_def(node, spec):
    """Nearest enclosing function, else nearest enclosing type, else None.

    A call is attributed to the function it sits in. Calls at file scope -- a
    top-level registration in an `init()`-less module, a Nix binding applied at
    the root of the file -- have no enclosing function and are attributed to the
    file instead, since dropping them would hide exactly the framework wiring
    the graph is supposed to expose.
    """
    cur = node.parent
    fallback = None
    while cur is not None:
        if cur.type in spec.defs:
            return cur
        if fallback is None and cur.type in spec.types:
            fallback = cur
        cur = cur.parent
    return fallback


def is_contract(node, spec):
    """Whether a declaration only promises behaviour, leaving the target dynamic."""
    if node.type in CONTRACT_NODES:
        return True
    if not spec.ifaces:
        return False
    cur = node.parent
    while cur is not None:
        if cur.type in spec.ifaces:
            return True
        if cur.type in spec.defs or cur.type in spec.impl_blocks:
            return False
        cur = cur.parent
    return False


def collect_declarations(root, opts):
    """-> (nodes, by_name, by_func, contract_keys)

    The key is `path:line`, which is unique and stable regardless of how many
    same-named declarations the repo has. `label` is what a human wants to read.
    `by_func` holds functions only: a value handed to a call is a callback when
    it names a function, and a variable sharing a name with a *type* is the
    commonest false positive there is. `contract_keys` holds the declarations
    that are interface members rather than implementations.
    """
    nodes = {}
    by_name = {}
    by_func = {}
    contract_keys = set()
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk_decls(
            tree.root_node, spec, tuple(set(spec.defs) | set(spec.types))
        ):
            name = resolve_name(node, spec)
            if not name or name == "<anonymous>":
                continue
            line = line_of(src, node, offset)
            owner = receiver_type(node, spec)
            key = f"{path}:{line}"
            names = decl_names(node, spec)
            nodes[key] = {
                "path": path,
                "line": line,
                "name": name,
                "owner": owner,
                "label": f"{owner}.{name}" if owner else name,
                "lang": spec.key,
                "aliases": sorted(names),
            }
            for alias in {canon(n, spec) for n in names}:
                by_name.setdefault(alias, []).append((path, line, owner))
                if node.type in spec.defs:
                    by_func.setdefault(alias, []).append((path, line, owner))
            if is_contract(node, spec):
                contract_keys.add(key)
    return nodes, by_name, by_func, contract_keys


def external_heads(root, opts):
    """-> {path: {head, ...}} for imports that resolve outside this repo.

    `rand.New(...)` after `import "math/rand"` has nothing to do with a local
    `New`, but the name matches, so without this every stdlib call drags the
    repo's own same-named declarations into the candidate list. Imports that do
    resolve to a file in the repo are left alone: `queue.Push` after
    `import { queue } from "./queue"` is a real edge worth following.
    """
    by_module, by_stem = {}, {}
    for path, _, _, _, _ in iter_parsed(root, opts):
        stem, _ = os.path.splitext(path)
        by_module[stem] = path
        by_module[path] = path
        rel = os.path.relpath(stem, root).replace(os.sep, "/")
        by_module.setdefault(rel, path)
        by_module.setdefault(os.path.dirname(rel), path)
        by_stem.setdefault(os.path.basename(stem), []).append(path)

    heads = {}
    for path, _, tree, spec, _ in iter_parsed(root, opts):
        for node in walk(tree.root_node, spec.imports):
            if not is_import_node(node, spec):
                continue
            for target in import_targets(node, spec, path):
                if resolve_import(target, path, root, by_module, by_stem, spec):
                    continue
                if spec.key == "python":
                    heads.setdefault(path, set()).update(_python_bound_names(node))
                    continue
                tail = target.replace("\\", "/").rstrip("/").split("/")[-1]
                tail = tail.split(".")[0].strip("\"'")
                if tail:
                    heads.setdefault(path, set()).add(tail)
                alias = field(node, "name")
                if alias and alias.isidentifier():
                    heads.setdefault(path, set()).add(alias)
    return heads


_SHELLS = frozenset({"bash", "fish", "powershell"})


def build_graph(root, opts):
    """-> (nodes, out_edges, in_edges, unresolved)

    An edge is only recorded when ranking left exactly one plausible target.
    Where several survive, the call becomes an *unresolved* entry instead: a
    guessed edge that reads like a fact is worse than an admitted gap, and
    interface dispatch -- the case that matters most -- is precisely the case
    that stays ambiguous.
    """
    nodes, by_name, by_func, contract_keys = collect_declarations(root, opts)
    outside = external_heads(root, opts)
    out_edges, in_edges, unresolved = {}, {}, []

    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node, qualified in iter_calls(tree.root_node, spec):
            line = line_of(src, node, offset)
            owner_node = enclosing_def(node, spec)
            caller = (
                f"{path}:{line_of(src, owner_node, offset)}"
                if owner_node is not None
                else f"{path}:0"
            )
            if caller not in nodes:
                nodes.setdefault(
                    caller,
                    {
                        "path": path,
                        "line": 0,
                        "name": os.path.basename(path),
                        "owner": "",
                        "label": f"<file scope> {os.path.basename(path)}",
                        "lang": spec.key,
                    },
                )

            if spec.key in _SHELLS and "$" in qualified:
                # `_item_$name`, `"$handler"`, `& $cmd`: the command name is
                # built at runtime. No edge can be drawn, but it must not vanish.
                unresolved.append(
                    {
                        "caller": caller,
                        "expr": qualified,
                        "path": path,
                        "line": line,
                        "kind": "dynamic",
                        "candidates": [],
                    }
                )
                continue
            kind = "reference" if is_reference(node, spec) else "call"
            targets = [(canon(bare_name(qualified, spec), spec), qualified, kind)]
            targets += [
                (split_qualifier(arg), arg, "callback")
                for arg in argument_references(node, spec)
            ]
            for bare, expr, kind in targets:
                head = re.split(r"->|::|\.", expr)[0].strip()
                if head != expr and head in outside.get(path, ()):
                    continue
                table = by_func if kind == "callback" else by_name
                if len(bare) < 3 or bare not in table:
                    continue
                pool = table[bare]

                # A name shared by an interface member and its implementations
                # is dynamic dispatch. The syntax records which contract is
                # being called and says nothing about which body runs, so the
                # only honest answer is the candidate list.
                implementations = [
                    c for c in pool if f"{c[0]}:{c[1]}" not in contract_keys
                ]
                dispatched = len(implementations) != len(pool)
                if dispatched and len(implementations) != 1:
                    unresolved.append(
                        {
                            "caller": caller,
                            "expr": expr,
                            "path": path,
                            "line": line,
                            "kind": "dispatch",
                            "candidates": [f"{c[0]}:{c[1]}" for c in pool],
                        }
                    )
                    continue
                if dispatched:
                    pool = implementations

                ranked, confidence = resolve_call(
                    expr, node, spec, path, src, offset, pool
                )
                if dispatched:
                    # One implementor is a strong guess, not a fact: another may
                    # live outside this repo.
                    confidence = "INFERRED"
                if not ranked:
                    continue
                best = f"{ranked[0][0]}:{ranked[0][1]}"
                if confidence == "AMBIGUOUS":
                    unresolved.append(
                        {
                            "caller": caller,
                            "expr": expr,
                            "path": path,
                            "line": line,
                            "kind": kind,
                            "candidates": [f"{c[0]}:{c[1]}" for c in ranked],
                        }
                    )
                    continue
                if best == caller or best not in nodes:
                    continue
                edge = (caller, best, path, line, kind, confidence)
                out_edges.setdefault(caller, []).append(edge)
                in_edges.setdefault(best, []).append(edge)
    return nodes, out_edges, in_edges, unresolved


def seed_keys(nodes, name):
    """Declarations a trace starts from.

    Exact matches only, unless there are none: a substring fallback on a name
    like `Scale` matches every method of an `Autoscaler`, which turns one
    question into fifty answers. The fallback matches the declaration's own name
    rather than its qualified label, for the same reason.
    """

    def ordered(keys):
        return sorted(keys, key=lambda key: (nodes[key]["path"], nodes[key]["line"]))

    exact = [
        key
        for key, node in nodes.items()
        if node["name"] == name
        or node["label"] == name
        or name in node.get("aliases", ())
    ]
    if exact:
        return ordered(exact)
    lowered = name.casefold()
    return ordered(
        key for key, node in nodes.items() if lowered in node["name"].casefold()
    )


def cmd_trace(name, root, opts, emit):
    nodes, out_edges, in_edges, unresolved = build_graph(root, opts)
    seeds = seed_keys(nodes, name)
    if not seeds:
        emit(f"no declaration matching {name!r} found under {root}")
        return

    depth = int(opts.get("depth", 3))
    if opts.get("both"):
        direction, sources = "callers and callees", (in_edges, out_edges)
    elif opts.get("callers"):
        direction, sources = "callers", (in_edges,)
    else:
        direction, sources = "callees", (out_edges,)

    visited = set(seeds)
    reported = []

    def expand(key, level, prefix):
        if level >= depth:
            return
        seen_here = []
        for table in sources:
            for edge in table.get(key, []):
                other = edge[1] if table is out_edges else edge[0]
                arrow = "->" if table is out_edges else "<-"
                seen_here.append((other, arrow, edge))
        for other, arrow, edge in sorted(
            {(o, a, e) for o, a, e in seen_here}, key=lambda item: item[2][3]
        ):
            _, _, epath, eline, kind, confidence = edge
            node = nodes[other]
            repeat = other in visited
            emit(
                f"{prefix}{arrow} {node['label']}  ({node['path']}:{node['line']})"
                f"  [{kind}, {confidence}]  at {epath}:{eline}"
                + ("  (already shown)" if repeat else "")
            )
            reported.append((other, edge))
            if repeat:
                continue
            visited.add(other)
            expand(other, level + 1, prefix + "  ")

    for seed in seeds:
        node = nodes[seed]
        emit(f"{node['label']}  ({node['path']}:{node['line']})")
        expand(seed, 0, "  ")
        emit("")

    open_ends = [item for item in unresolved if item["caller"] in visited]
    if open_ends:
        emit(
            "unresolved (no single target: several candidates survived ranking, "
            "or the name is computed at runtime):"
        )
        for item in open_ends[:25]:
            caller = nodes[item["caller"]]["label"]
            emit(
                f"  {caller} -> {item['expr']}  [{item['kind']}]  "
                f"{item['path']}:{item['line']}"
            )
            if not item["candidates"]:
                emit(
                    "      (name computed at runtime; read the code to see what it can be)"
                )
            for candidate in item["candidates"][:4]:
                emit(f"      candidate: {nodes[candidate]['label']}  ({candidate})")
        if len(open_ends) > 25:
            emit(f"  ... {len(open_ends) - 25} more")

    emit(
        f"\ndirection {direction}, depth {depth}. Edges are name-based, not type-"
        "resolved: an interface call, a reflected call, or a call through a "
        "generated client leaves no edge here. Verify an INFERRED hop before "
        "relying on it."
    )


# --------------------------------------------------------------------------
# effects
# --------------------------------------------------------------------------


def channel_effect(node, src, spec):
    """Go channel send or receive, as (kind, detail) or None."""
    if spec.key != "go":
        return None
    if node.type == "send_statement":
        channel = field(node, "channel")
        return ("channel", f"send to {channel}") if channel else None
    if node.type == "unary_expression":
        body = " ".join(text(node).split())
        if body.startswith("<-"):
            return ("channel", f"receive from {body[2:].strip()}")
    return None


def cmd_effects(root, opts, emit):
    """Report the transitions a call graph cannot carry.

    Everything here is a point where control or data leaves the path the reader
    was following: work handed to another execution context, a value crossing a
    channel, a value crossing an encoder, or a function handed to something that
    will call it later. These are where a trace goes wrong silently, so they are
    worth listing before drawing conclusions from one.
    """
    wanted = opts.get("kind", "")
    functions = set()
    for _, _, tree, spec, _ in iter_parsed(root, opts):
        for node in walk(tree.root_node, spec.defs):
            name = resolve_name(node, spec)
            if name and len(name) > 2:
                functions.add(name)

    rows = []
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        types = (
            tuple(SPAWN_NODES)
            + tuple(spec.calls)
            + ("send_statement", "unary_expression")
        )
        for node in walk(tree.root_node, types):
            line = line_of(src, node, offset)
            where = enclosing_decl(node, spec)
            found = []

            if node.type in SPAWN_NODES:
                found.append(("spawn", SPAWN_NODES[node.type]))
            channel = channel_effect(node, src, spec)
            if channel is not None:
                found.append(channel)
            if node.type in spec.calls:
                qualified = callee_expression(node, spec)
                if qualified is not None:
                    lowered = qualified.casefold()
                    if lowered in SERIALIZATION_CALLEES:
                        found.append(("boundary", qualified))
                    elif lowered in SPAWN_CALLEES:
                        found.append(("spawn", qualified))
                    for arg in argument_references(node, spec):
                        if split_qualifier(arg) in functions:
                            found.append(("callback", f"{qualified}(... {arg} ...)"))

            for kind, detail in found:
                if wanted and kind != wanted:
                    continue
                rows.append((path, line, kind, detail, where))

    if not rows:
        emit(f"no async, channel, serialization or callback effects found under {root}")
        return
    if opts["json"]:
        emit(
            [
                {"file": p, "line": ln, "kind": k, "detail": d, "in": w}
                for p, ln, k, d, w in rows
            ]
        )
        return

    counts = {}
    for _, _, kind, _, _ in rows:
        counts[kind] = counts.get(kind, 0) + 1
    emit("  ".join(f"{kind}: {count}" for kind, count in sorted(counts.items())) + "\n")
    for path, line, kind, detail, where in sorted(rows)[:200]:
        emit(f"{path}:{line}: [{kind}] {detail}   [{where}]")
    if len(rows) > 200:
        emit(f"\n... {len(rows) - 200} more")
    emit(
        "\nchannel detection is Go-only. A callback is reported when an argument "
        "names a function declared in this repo, so a local variable that shadows "
        "a function name shows up here too."
    )


def cyclomatic(node):
    """McCabe complexity: one, plus one per branch point in the subtree.

    Counted structurally from node types rather than from source text, so a
    `&&` inside a string literal cannot inflate the score. Nested functions are
    not excluded: a closure's branches are branches the reader still has to
    follow when reading the enclosing function.
    """
    score = 1
    stack = [node]
    while stack:
        cur = stack.pop()
        if (
            cur.is_named
            and cur.type in DECISION_NODES
            or not cur.is_named
            and cur.type in DECISION_OPERATORS
        ):
            score += 1
        stack.extend(cur.children)
    return score


def cmd_complexity(root, opts, emit):
    threshold = int(opts.get("min", 1))
    rows = []
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(tree.root_node, spec.defs):
            name = resolve_name(node, spec)
            if not name:
                continue
            score = cyclomatic(node)
            if score < threshold:
                continue
            owner = receiver_type(node, spec)
            label = f"{owner}.{name}" if owner else name
            rows.append((score, path, line_of(src, node, offset), label))

    if not rows:
        emit(f"no functions found under {root}")
        return
    rows.sort(reverse=True)

    if opts["json"]:
        emit(
            [
                {"score": s_, "file": p_, "line": l_, "name": n_}
                for s_, p_, l_, n_ in rows[:200]
            ]
        )
        return

    emit(f"{'score':>6}  location")
    for score, path, line, label in rows[:40]:
        emit(f"{score:>6}  {path}:{line}  {label}")
    total = sum(r[0] for r in rows)
    emit(f"\n{len(rows)} functions, mean complexity {total / len(rows):.1f}")
    emit(
        "Roughly: 1-5 simple, 6-10 moderate, 11-20 worth a second look, "
        "20+ usually hard to test exhaustively."
    )


def is_import_node(node, spec):
    """Whether an `imports` candidate really is an import.

    Some grammars have no dedicated import node. Nix spells an import as an
    ordinary function application, so without this check every call in the file
    is reported as an import.
    """
    hook = spec.import_hook
    if hook == "nix_import":
        fn = node.child_by_field_name("function")
        return fn is not None and text(fn).strip() == "import"
    if hook == "shell_source":
        return field(node, "name") in ("source", ".")
    if hook == "ps_import":
        operator = _child(node, "command_invokation_operator")
        if operator is not None:
            return text(operator) == "."  # dot-sourcing; `&` only runs a script
        return field(node, "command_name").casefold() in (
            "import-module",
            "ipmo",
            "using",
        )
    if hook == "html_resource":
        name = text(_child(node, "attribute_name")).lower()
        target = _html_ref(node, spec) or ""
        return name in ("href", "src") and not target.startswith(_EXTERNAL)
    return True


_EXTERNAL = ("#", "http:", "https:", "//", "mailto:", "data:", "javascript:", "tel:")
_IMPORT_KEYWORDS = frozenset({"module", "namespace", "assembly"})


def _field_children(node, name):
    return [
        c for i, c in enumerate(node.children) if node.field_name_for_child(i) == name
    ]


def _python_dotted(node):
    inner = node.child_by_field_name("name") if node.type == "aliased_import" else node
    return text(inner)


def _python_import_targets(node):
    """`from ..a.b import c` -> ['../a/b']; `from . import util` -> ['./util']."""
    names = _field_children(node, "name")
    if node.type == "import_statement":
        return [_python_dotted(n).replace(".", "/") for n in names]
    module = field(node, "module_name")
    dots = len(module) - len(module.lstrip("."))
    rest = module[dots:].replace(".", "/")
    base = ("./" if dots == 1 else "../" * (dots - 1)) if dots else ""
    if rest:
        return [base + rest]
    # `from . import util`: each name is a module of the package.
    return [base.rstrip("/") + "/" + _python_dotted(n) for n in names]


def _python_bound_names(node):
    """Local names an import binds: `import os.path` binds `os`, `as p` binds p."""
    bound = []
    for name in _field_children(node, "name"):
        alias = (
            name.child_by_field_name("alias") if name.type == "aliased_import" else None
        )
        if alias is not None:
            bound.append(text(alias))
        elif node.type == "import_statement":
            bound.append(text(name).split(".")[0])
        else:
            bound.append(text(name).split(".")[-1])
    return bound


def import_targets(node, spec, path):
    """Best-effort module path(s) named by an import statement."""
    if spec.key == "python":
        return _python_import_targets(node)
    if spec.import_hook in ("shell_source", "ps_import"):
        # `source ./lib.fish`, `. ./lib.sh`, `using module ./M.psm1`,
        # `Import-Module -Name ./M.psm1`: the first plain argument.
        words = [
            w
            for w in text(node).split()[1:]
            if not w.startswith("-") and w.lower() not in _IMPORT_KEYWORDS
        ]
        if not words:
            return []
        return [words[0].strip("\"'").replace("$PSScriptRoot", ".")]
    if spec.import_hook == "html_resource":
        target = _html_ref(node, spec)
        return [target] if target else []
    if spec.key == "css":
        for kind in ("string_content", "plain_value", "string_value"):
            for found in walk(node, (kind,)):
                return [text(found).strip("\"'")]
        return []
    raw = text(node)
    quoted = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", raw)
    if quoted:
        return quoted
    cleaned = re.sub(
        r"^\s*(import|from|use|using|require|include|package)\b", "", raw
    ).strip()
    cleaned = cleaned.strip(";").split(" as ")[0].strip()
    return [cleaned] if cleaned else []


# Languages whose imports are file paths relative to the importing file, even
# without a leading `./`: `@import "theme.css"`, `source lib/x.fish`.
_RELATIVE_IMPORTS = frozenset({"css", "html", "bash", "fish", "powershell"})


def resolve_import(target, importer, root, by_module, by_stem, spec=None):
    """Map an import target onto a file in this repo, or None if external."""
    if not target:
        return None
    if (
        spec is not None
        and spec.key in _RELATIVE_IMPORTS
        and not target.startswith(("/", ".", "~", "$"))
        and "://" not in target
    ):
        target = "./" + target
    normalised = target.replace("\\", "/").strip("/")

    if normalised.startswith("."):
        base = os.path.dirname(importer)
        candidate = os.path.normpath(os.path.join(base, normalised))
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs"):
            hit = by_module.get(candidate + suffix)
            if hit:
                return hit
            for index in ("index", "__init__"):  # JS barrel, Python package
                hit = by_module.get(os.path.join(candidate, index + suffix))
                if hit:
                    return hit
        return None

    for prefix_len in range(normalised.count("/") + 1, 0, -1):
        tail = "/".join(normalised.split("/")[-prefix_len:])
        hit = by_module.get(tail)
        if hit:
            return hit

    stem = normalised.split("/")[-1].split(".")[-1]
    matches = by_stem.get(stem)
    if matches and len(matches) == 1:
        return matches[0]
    return None


def cmd_cycles(root, opts, emit):
    """Circular imports at file level.

    Import statements name modules, not files, so the mapping back to a path is
    a resolution step and it is where the uncertainty lives. Only cycles whose
    every edge resolved to a file inside this repo are reported.
    """
    edges = {}
    by_module = {}
    by_stem = {}
    files = []

    for path, src, tree, spec, offset in iter_parsed(root, opts):
        files.append(path)
        stem, _ = os.path.splitext(path)
        by_module[stem] = path
        by_module[path] = path
        rel = os.path.relpath(stem, root).replace(os.sep, "/")
        by_module.setdefault(rel, path)
        by_module.setdefault(os.path.dirname(rel), path)
        by_stem.setdefault(os.path.basename(stem), []).append(path)

    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(tree.root_node, spec.imports):
            if not is_import_node(node, spec):
                continue
            for target in import_targets(node, spec, path):
                dest = resolve_import(target, path, root, by_module, by_stem, spec)
                if dest and dest != path:
                    edges.setdefault(path, set()).add(dest)

    cycles = find_cycles(edges, max_len=int(opts.get("max_len", 6)))
    if not cycles:
        emit(
            f"no import cycles found among {len(files)} files "
            f"({sum(len(v) for v in edges.values())} internal import edges resolved)"
        )
        return

    if opts["json"]:
        emit(
            [
                {"length": len(c), "files": [os.path.relpath(f, root) for f in c]}
                for c in cycles
            ]
        )
        return

    emit(f"{len(cycles)} import cycle(s):\n")
    for cycle in cycles:
        shown = [os.path.relpath(f, root) for f in cycle]
        emit(f"  [{len(cycle)}] " + " -> ".join(shown + [shown[0]]))
    emit(
        "\nImport targets are resolved to files by path and module name, so a "
        "cycle through a re-exporting barrel file or an aliased path may be "
        "missed. Reported cycles resolved fully; absence is weaker evidence."
    )


def find_cycles(edges, max_len=6, limit=25):
    """Simple cycles in a directed graph, shortest first, deduplicated."""
    found = {}

    def dfs(start, node, path, seen):
        if len(path) > max_len:
            return
        for nxt in sorted(edges.get(node, ())):
            if nxt == start and len(path) > 1:
                canonical = tuple(sorted(path))
                if canonical not in found or len(path) < len(found[canonical]):
                    found[canonical] = list(path)
            elif nxt not in seen and nxt > start:
                dfs(start, nxt, path + [nxt], seen | {nxt})

    for start in sorted(edges):
        dfs(start, start, [start], {start})
        if len(found) > limit * 4:
            break
    ordered = sorted(found.values(), key=lambda c: (len(c), c))
    return ordered[:limit]


def install_hint(keys):
    """The command to print for missing grammars.

    The bootstrap builds an isolated environment from pinned, hash-checked
    wheels, offline where the skill bundles them. Global `pip install` is not
    offered: it is refused on PEP 668 Pythons and impossible on Nix, and an
    unpinned grammar is exactly how a parser ABI mismatch gets in.
    """
    bootstrap = os.path.join(_HERE, "bootstrap.py")
    return (
        f"  python3 {bootstrap} --languages {','.join(sorted(set(keys)))}\n"
        f"  then run this script with the python it reports "
        f"(--profile common installs every bundled language at once)"
    )


def cmd_langs(_root, _opts, emit):
    emit(f"{'lang':<12}{'grammar':<28}{'status':<12}extensions")
    missing = []
    for spec in ALL:
        ok = get_parser(spec) is not None
        if not ok:
            missing.append(spec.key)
        emit(
            f"{spec.key:<12}{spec.module:<28}{'installed' if ok else 'MISSING':<12}"
            f"{' '.join(spec.exts)}"
        )
    if missing:
        emit("\ninstall the missing grammars with:")
        emit(install_hint(missing))


def cmd_debug_ast(path, opts, emit):
    segments = parse(path)
    if not segments:
        emit(f"unsupported or unparseable: {path}")
        return
    maxdepth = int(opts["depth"])

    def dump(node, depth):
        if depth > maxdepth:
            return
        if node.is_named:
            fields = []
            for fname in ("name", "parameters", "type", "return_type", "body", "value"):
                child = node.child_by_field_name(fname)
                if child is not None:
                    fields.append(f"{fname}={text(child)[:28]!r}")
            emit("  " * depth + node.type + ("  " + " ".join(fields) if fields else ""))
        for child in node.children:
            dump(child, depth + 1)

    for index, (_, tree, spec, offset) in enumerate(segments):
        header = f"# language: {spec.key}"
        if len(segments) > 1:
            header += (
                f"  (region {index + 1} of {len(segments)}, from line {offset + 1})"
            )
        emit(header)
        dump(tree.root_node, 0)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

# Index of the <root> argument per command, for the applicability check.
_ROOT_ARG = {
    "map": 0,
    "decl": 1,
    "refs": 1,
    "iface": 1,
    "methods": 1,
    "fields": 1,
    "consts": 0,
    "todos": 0,
    "impls": 1,
    "trace": 1,
    "effects": 0,
    "complexity": 0,
    "cycles": 0,
}


def run_command(cmd, args, opts, emit):
    """Run one command, answering "not applicable" where the question has no
    meaning for every language in scope (an interface in CSS, a goroutine in
    SQL). That answer is different from "nothing found", which reads as a
    search that came up empty. In a mixed scope the command runs on the
    languages it applies to and names the ones it skipped.
    """
    handler, arity = COMMANDS[cmd]
    blocked = []
    index = _ROOT_ARG.get(cmd)
    if index is not None and len(args) > index:
        in_scope = {seg[3].key for seg in iter_parsed(args[index], opts)}
        blocked = sorted(k for k in in_scope if cmd in BY_KEY[k].unsupported)
        reason = "; ".join(sorted({BY_KEY[k].unsupported_reason for k in blocked}))
        if blocked and len(blocked) == len(in_scope):
            if opts["json"]:
                emit({"not_applicable": cmd, "languages": blocked, "reason": reason})
            else:
                emit(f"`{cmd}` is not applicable to {', '.join(blocked)}: {reason}")
            return
        if blocked:
            opts = dict(opts, skip_langs=set(blocked))
    handler(*(list(args[:arity]) or [None]), opts, emit)
    if blocked and not opts["json"]:
        emit(f"\n[`{cmd}` not applicable, skipped: {', '.join(blocked)} ({reason})]")


def resolve_lang(value):
    """--lang accepts aliases (`c#`, `ts`, `pwsh`); unknown names pass through."""
    return canonical_key(value) or value


COMMANDS = {
    "map": (cmd_map, 1),
    "outline": (cmd_outline, 1),
    "decl": (cmd_decl, 2),
    "refs": (cmd_refs, 2),
    "iface": (cmd_iface, 2),
    "methods": (cmd_methods, 2),
    "fields": (cmd_fields, 2),
    "consts": (cmd_consts, 1),
    "todos": (cmd_todos, 1),
    "impls": (cmd_impls, 2),
    "trace": (cmd_trace, 2),
    "effects": (cmd_effects, 1),
    "complexity": (cmd_complexity, 1),
    "cycles": (cmd_cycles, 1),
    "langs": (cmd_langs, 0),
    "debug-ast": (cmd_debug_ast, 1),
}


def default_opts(**overrides):
    """Baseline flag values. One definition, shared by the CLI and the tests."""
    opts = {
        "tests": False,
        "mocks": False,
        "json": False,
        "no_gitignore": False,
        "callers": False,
        "both": False,
        "min": 1,
        "max_len": 6,
        "lang": "",
        "kind": "",
        "depth": 3,
        "max_bytes": _MAX_BYTES,
    }
    opts.update(overrides)
    return opts


_SWITCHES = {
    "--tests": "tests",
    "--mocks": "mocks",
    "--json": "json",
    "--no-gitignore": "no_gitignore",
    "--callers": "callers",
    "--both": "both",
}
_VALUED = {
    "--lang": "lang",
    "--kind": "kind",
    "--depth": "depth",
    "--min": "min",
    "--max-len": "max_len",
    "--max-bytes": "max_bytes",
}


def parse_args(argv):
    """-> (opts, positional).

    Only known flags are flags. Anything else is positional, so a CSS custom
    property such as `--brand` can be a name; `--` ends option parsing for a
    name that collides with a flag (`decl -- --json`).
    """
    opts = default_opts()
    positional = []
    args = iter(argv)
    for arg in args:
        if arg == "--":
            positional.extend(args)
            break
        key, _, value = arg.partition("=")
        if arg in _SWITCHES:
            opts[_SWITCHES[arg]] = True
        elif key in _VALUED and value:
            opts[_VALUED[key]] = resolve_lang(value) if key == "--lang" else value
        else:
            positional.append(arg)
    return opts, positional


def main():
    opts, positional = parse_args(sys.argv[1:])

    if not positional or positional[0] not in COMMANDS:
        print(__doc__)
        return 0

    cmd, rest = positional[0], positional[1:]
    _, arity = COMMANDS[cmd]
    if opts["lang"] and opts["lang"] not in BY_KEY:
        print(f"unknown --lang={opts['lang']}. Run `ts_query.py langs`.")
        return 2
    if len(rest) < arity:
        print(__doc__)
        return 2
    if cmd == "debug-ast" and len(rest) > 1:
        opts["depth"] = rest[1]

    buffer = []

    def emit(item):
        if opts["json"]:
            buffer.append(item)
        else:
            print(item)

    try:
        run_command(cmd, rest[:arity], opts, emit)
        if opts["json"]:
            print(json.dumps(buffer[0] if len(buffer) == 1 else buffer, indent=2))
    except BrokenPipeError:
        pass
    if _MISSING and cmd != "langs":  # langs already tabulates what is missing
        print(
            f"\n[grammars not installed, those files were skipped: "
            f"{' '.join(sorted(_MISSING))}]",
            file=sys.stderr,
        )
    if _PARSE_ERRORS:
        # tree-sitter recovers rather than failing, so a bad parse yields a
        # partial tree that looks exactly like a good one. Results from these
        # files are incomplete in ways nothing downstream can detect.
        shown = sorted(_PARSE_ERRORS)[:5]
        extra = f" (+{len(_PARSE_ERRORS) - 5} more)" if len(_PARSE_ERRORS) > 5 else ""
        print(
            f"\n[parse errors, results from these files are partial: "
            f"{' '.join(shown)}{extra}]",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
