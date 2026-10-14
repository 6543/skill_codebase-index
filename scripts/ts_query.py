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

try:
    from tree_sitter import Language, Parser
except ImportError:
    sys.exit("tree_sitter not installed. Run: pip install tree_sitter")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ignores
from languages import (
    ALL,
    ARGUMENT_NODES,
    BY_EXT,
    BY_KEY,
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
    TODO_MARKERS,
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
    _, ext = os.path.splitext(path)
    return BY_EXT.get(ext.lower())


SCRIPT_BLOCK = re.compile(
    rb"<script[^>]*>(?P<body>.*?)</script\s*>", re.DOTALL | re.IGNORECASE
)


def read_segments(path, spec):
    """Return [(source_bytes, line_offset)] -- one entry per parseable region.

    A plain source file is one region. A single-file component is one region per
    `<script>` block: Vue 3 routinely has two (`<script setup>` for the logic and
    a plain `<script>` for options such as `defineOptions` or a named export),
    and Svelte's `context="module"` block is the same shape. Reading only the
    first block silently lost whichever one happened to come second.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if not spec.sfc:
        return [(raw, 0)]
    return [
        (match.group("body"), raw[: match.start("body")].count(b"\n"))
        for match in SCRIPT_BLOCK.finditer(raw)
        if match.group("body").strip()
    ]


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
    if spec is None:
        return []
    parser = get_parser(spec)
    if parser is None:
        return []
    out = []
    for src, offset in read_segments(path, spec):
        if not src.strip():
            continue
        tree = parser.parse(src)
        if has_real_error(tree.root_node, src, spec):
            _PARSE_ERRORS.add(path)
        out.append((src, tree, spec, offset))
    return out


def has_real_error(root, src, spec):
    """Whether a parse failure is worth warning about.

    tree-sitter recovers from errors rather than failing, so a partly wrong tree
    looks exactly like a right one. The Nix grammar flags a *missing* identifier
    after a legal trailing comma in a formal argument set, which is valid Nix and
    appears in most NixOS modules; that one artefact is suppressed and every
    other error is reported.
    """
    if not root.has_error:
        return False
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur.type == "ERROR":
            return True
        if cur.is_missing and (spec.key != "nix" or not _harmless_nix_comma(cur, src)):
            return True
        if cur.has_error:
            stack.extend(cur.children)
    return False


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
    return node.text.decode("utf8", "replace") if node is not None else ""


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


def resolve_name(node, spec):
    """Declaration name, handling grammars that don't expose a `name` field."""
    direct = node.child_by_field_name("name")
    if direct is not None:
        return text(direct)

    if spec.name_hook == "c_declarator":
        # C/C++: int foo(int a) {} -> name is buried in the declarator chain.
        cur = node.child_by_field_name("declarator")
        while cur is not None:
            if cur.type in ("identifier", "field_identifier", "type_identifier"):
                return text(cur)
            nxt = cur.child_by_field_name("declarator")
            if nxt is None:
                for child in cur.named_children:
                    if child.type.endswith("identifier"):
                        return text(child)
                return text(cur)
            cur = nxt

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


def parameters_of(node, spec):
    """Parameter list as source text, normalising the grammars that differ."""
    direct = field(node, "parameters") or field(node, "formal_parameters")
    if direct:
        return " ".join(direct.split())

    if spec.name_hook == "c_declarator":
        # C/C++ keep the parameter list inside the declarator chain.
        for child in walk(node, ("function_declarator",)):
            params = field(child, "parameters")
            if params:
                return " ".join(params.split())

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


def signature(node, spec):
    """One-line rendering: receiver, name, params, return type."""
    name = resolve_name(node, spec)
    recv = field(node, "receiver")
    params = parameters_of(node, spec)
    ret = field(node, "return_type") or field(node, "result") or field(node, "type")
    parts = [f"{recv} " if recv else "", name, params or "()"]
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
    prev = node.prev_sibling
    boundary = node.start_point[0]
    comment_types = set(spec.comment) - {"string"}
    while prev is not None and prev.type in comment_types:
        # Grammars disagree on whether a line comment's extent includes the
        # trailing newline (Rust's does, Go's does not), so accept the comment
        # if it merely spans the line directly above rather than ending on it.
        if not prev.start_point[0] <= boundary - 1 <= prev.end_point[0]:
            break
        out.append(text(prev).rstrip())
        boundary = prev.start_point[0]
        prev = prev.prev_sibling

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
    return list(reversed(out))


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
            return f"{cur.type} {resolve_name(cur, spec)}"
        cur = cur.parent
    return "<file scope>"


def type_body(node):
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
        if child.type in spec.defs:
            out.append(("method", signature(child, spec), doc, line))
        else:
            rendered = " ".join(text(child).split())
            out.append(("field", rendered.rstrip(";,"), doc, line))
    return out


# --------------------------------------------------------------------------
# file walking
# --------------------------------------------------------------------------


def is_test(path):
    base = os.path.basename(path)
    return any(p in base for p in TEST_PATTERNS)


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
            spec = spec_for(path)
            if spec is None:
                continue
            if opts["lang"] and spec.key != opts["lang"]:
                continue
            if is_sensitive(path):
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
    for path in iter_files(root, opts):
        for segment in parse(path):
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
        for node in walk(tree.root_node, tuple(set(spec.defs) | set(spec.types))):
            name = resolve_name(node, spec)
            if name and len(name) > 3:
                declared.setdefault(name, (path, line_of(src, node, offset), spec.key))
                declared_count[name] = declared_count.get(name, 0) + 1
        for node in walk(tree.root_node, spec.calls):
            fn = node.child_by_field_name("function") or node.child_by_field_name(
                "name"
            )
            if fn is None and node.named_children:
                fn = node.named_children[0]
            if fn is None:
                continue
            qualified = " ".join(text(fn).split())
            bare = split_qualifier(qualified)
            # A qualified call whose receiver is a package name is most often
            # a standard-library call (fmt.Errorf, log.Debug). Crediting it to
            # a same-named local declaration is simply wrong, and stdlib verbs
            # are exactly the names that collide, so only unqualified calls
            # count toward fan-in.
            if len(bare) > 3 and bare == qualified:
                mentions.setdefault(bare, {}).setdefault(family(spec.key), set()).add(
                    path
                )

    for path, src, tree, spec, offset in iter_parsed(root, opts):
        bucket = stats.setdefault(
            spec.key, {"files": 0, "lines": 0, "defs": 0, "types": 0, "biggest": []}
        )
        bucket["files"] += 1
        bucket["lines"] += src.count(b"\n") + 1
        ndefs = sum(1 for n in walk(tree.root_node, spec.defs) if resolve_name(n, spec))
        ntypes = sum(1 for _ in walk(tree.root_node, spec.types))
        bucket["defs"] += ndefs
        bucket["types"] += ntypes
        bucket["biggest"].append((ndefs + ntypes, path))

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
    segments = parse(path)
    if not segments:
        emit(f"unsupported or unparseable: {path}")
        return
    for index, segment in enumerate(segments):
        if len(segments) > 1:
            emit(f"--- script block {index + 1} of {len(segments)} ---")
        outline_segment(segment, emit)


def outline_segment(segment, emit):
    src, tree, spec, offset = segment
    root_node = tree.root_node

    for node in walk(root_node, spec.module_decl):
        header = text(node).split("\n")[0].split("{")[0].strip()
        emit(header[:120])
        break

    for line in src.decode("utf8", "replace").split("\n")[:15]:
        stripped = line.strip()
        if stripped.startswith(("//go:build", "# type:")):
            emit(f"buildtag {stripped}")

    imports = [
        preview(n, maxlines=1, width=100)
        for n in walk(root_node, spec.imports)
        if is_import_node(n, spec)
    ]
    if imports:
        emit(f"\nimports ({len(imports)})")
        for imp in imports:
            emit(f"  {imp}")

    types = list(walk(root_node, spec.types))
    if types:
        emit("\ntypes")
        for node in types:
            kind = "interface" if node.type in spec.ifaces else node.type
            for docline in doc_comment(node, spec):
                emit(f"    {docline}")
            emit(f"  {line_of(src, node, offset)}: {kind} {resolve_name(node, spec)}")
            for mkind, rendered, doc, _ in members_of(node, spec, src, offset):
                for docline in doc:
                    emit(f"        {docline}")
                emit(f"        {'fn' if mkind == 'method' else '  '} {rendered}")

    consts = [n for n in walk(root_node, spec.consts) if at_file_scope(n, spec)]
    if consts:
        emit("\nconst/var")
        for node in consts:
            emit(f"  {line_of(src, node, offset)}: {render_const(node, spec)}")

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
    if free:
        emit("\ndeclarations" if spec.top_level_only else "\nfuncs")
        for node in sorted(free, key=lambda n: n.start_byte):
            emit(f"  {line_of(src, node, offset)}: {describe(node, spec)}")
            for docline in doc_comment(node, spec):
                emit(f"        {docline}")


def cmd_decl(name, root, opts, emit):
    found = False
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(tree.root_node, tuple(set(spec.defs) | set(spec.types))):
            if resolve_name(node, spec) != name:
                continue
            found = True
            owner = receiver_type(node, spec)
            label = f"{spec.key} {node.type}" + (f" on {owner}" if owner else "")
            emit(f"=== {path}:{line_of(src, node, offset)} ({label}) ===")
            for docline in doc_comment(node, spec):
                emit(docline)
            emit(text(node))
            emit("")
    if not found:
        emit(f"no declaration named {name!r} found under {root}")


def declaration_sites(name, root, opts):
    """Every declaration of *name*, as (path, line, owner)."""
    sites = []
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(tree.root_node, tuple(set(spec.defs) | set(spec.types))):
            if resolve_name(node, spec) == name:
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


def rank_candidates(candidates, call_site):
    """Order declaration candidates by how plausibly they are the callee.

    Two signals, both cheap and both purely positional -- this is not type
    resolution and does not pretend to be:
      1. Production code does not call into test fixtures, so test candidates
         are dropped for a non-test call site, and preferred for a test one.
      2. Nearer in the directory tree beats further away.
    """
    if len(candidates) <= 1:
        return candidates, "EXTRACTED" if candidates else "UNRESOLVED"

    test_c = [c for c in candidates if is_test(c[0])]
    other_c = [c for c in candidates if not is_test(c[0])]

    if is_test(call_site):
        same_file = [c for c in test_c if c[0] == call_site]
        survivors = same_file or test_c or other_c
    else:
        survivors = other_c or candidates

    survivors = sorted(survivors, key=lambda c: (path_distance(c[0], call_site), c[0]))
    same_file = [c for c in survivors if c[0] == call_site]
    if same_file:
        return same_file, "EXTRACTED"
    if len(survivors) == 1:
        return survivors, "INFERRED"
    return survivors, "AMBIGUOUS"


def callee_expression(node, spec):
    """The expression naming what a call node invokes, or None."""
    fn = node.child_by_field_name("function") or node.child_by_field_name("name")
    if fn is None and node.named_children:
        fn = node.named_children[0]
    if fn is None:
        return None
    return " ".join(text(fn).split())


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
    return rank_candidates(pool, path)


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
        for node in walk(tree.root_node, spec.calls):
            qualified = callee_expression(node, spec)
            if qualified is None:
                continue
            bare = split_qualifier(qualified)
            if bare != name:
                continue
            hits += 1
            is_call = node.type != "select_expression"
            shown = (
                f"{qualified}(...)" if is_call else " ".join(text(node).split())[:70]
            )
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
                f"   [{enclosing_decl(node, spec)}]  {confidence}{target}"
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
        for node in walk(tree.root_node, spec.types):
            if resolve_name(node, spec) != name:
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
        for node in walk(tree.root_node, spec.types):
            if resolve_name(node, spec) != typename:
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


def cmd_consts(root, opts, emit):
    for path, src, tree, spec, offset in iter_parsed(root, opts):
        for node in walk(tree.root_node, spec.consts):
            if not at_file_scope(node, spec):
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
        for node in walk(tree.root_node, spec.types):
            if resolve_name(node, spec) != ifacename:
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
        for node in walk(tree.root_node, tuple(set(spec.defs) | set(spec.types))):
            name = resolve_name(node, spec)
            if not name or name == "<anonymous>":
                continue
            line = line_of(src, node, offset)
            owner = receiver_type(node, spec)
            key = f"{path}:{line}"
            nodes[key] = {
                "path": path,
                "line": line,
                "name": name,
                "owner": owner,
                "label": f"{owner}.{name}" if owner else name,
                "lang": spec.key,
            }
            by_name.setdefault(name, []).append((path, line, owner))
            if node.type in spec.defs:
                by_func.setdefault(name, []).append((path, line, owner))
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
                if resolve_import(target, path, root, by_module, by_stem):
                    continue
                tail = target.replace("\\", "/").rstrip("/").split("/")[-1]
                tail = tail.split(".")[0].strip("\"'")
                if tail:
                    heads.setdefault(path, set()).add(tail)
                alias = field(node, "name")
                if alias and alias.isidentifier():
                    heads.setdefault(path, set()).add(alias)
    return heads


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
        for node in walk(tree.root_node, spec.calls):
            qualified = callee_expression(node, spec)
            if qualified is None:
                continue
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

            targets = [(split_qualifier(qualified), qualified, "call")]
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
        if node["name"] == name or node["label"] == name
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
        emit("unresolved (several candidates survived ranking, so nothing was chosen):")
        for item in open_ends[:25]:
            caller = nodes[item["caller"]]["label"]
            emit(
                f"  {caller} -> {item['expr']}  [{item['kind']}]  "
                f"{item['path']}:{item['line']}"
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
    if spec.import_hook != "nix_import":
        return True
    fn = node.child_by_field_name("function")
    if fn is None:
        return False
    return text(fn).strip() == "import"


def import_targets(node, spec, path):
    """Best-effort module path(s) named by an import statement."""
    raw = text(node)
    quoted = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", raw)
    if quoted:
        return quoted
    cleaned = re.sub(
        r"^\s*(import|from|use|using|require|include|package)\b", "", raw
    ).strip()
    cleaned = cleaned.strip(";").split(" as ")[0].strip()
    return [cleaned] if cleaned else []


def resolve_import(target, importer, root, by_module, by_stem):
    """Map an import target onto a file in this repo, or None if external."""
    if not target:
        return None
    normalised = target.replace("\\", "/").strip("/")

    if normalised.startswith("."):
        base = os.path.dirname(importer)
        candidate = os.path.normpath(os.path.join(base, normalised))
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs"):
            hit = by_module.get(candidate + suffix)
            if hit:
                return hit
            hit = by_module.get(os.path.join(candidate, "index" + suffix))
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
                dest = resolve_import(target, path, root, by_module, by_stem)
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


def cmd_langs(_root, _opts, emit):
    emit(f"{'lang':<12}{'grammar':<28}{'status':<12}extensions")
    missing = []
    for spec in ALL:
        ok = get_parser(spec) is not None
        if not ok:
            missing.append(spec.module)
        emit(
            f"{spec.key:<12}{spec.module:<28}{'installed' if ok else 'MISSING':<12}"
            f"{' '.join(spec.exts)}"
        )
    if missing:
        emit("\ninstall the missing grammars with:")
        emit(f"  pip install {' '.join(sorted(set(missing)))}")


def cmd_debug_ast(path, opts, emit):
    segments = parse(path)
    if not segments:
        emit(f"unsupported or unparseable: {path}")
        return
    _, tree, spec, _ = segments[0]
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

    emit(f"# language: {spec.key}")
    dump(tree.root_node, 0)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

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


def main():
    argv = sys.argv[1:]
    opts = default_opts(
        tests="--tests" in argv,
        mocks="--mocks" in argv,
        json="--json" in argv,
        no_gitignore="--no-gitignore" in argv,
        callers="--callers" in argv,
        both="--both" in argv,
    )
    for arg in argv:
        if arg.startswith("--lang="):
            opts["lang"] = arg.split("=", 1)[1]
        if arg.startswith("--kind="):
            opts["kind"] = arg.split("=", 1)[1]
        if arg.startswith("--depth="):
            opts["depth"] = arg.split("=", 1)[1]
        if arg.startswith("--min="):
            opts["min"] = arg.split("=", 1)[1]
        if arg.startswith("--max-len="):
            opts["max_len"] = arg.split("=", 1)[1]
        if arg.startswith("--max-bytes="):
            opts["max_bytes"] = arg.split("=", 1)[1]
    positional = [a for a in argv if not a.startswith("--")]

    if not positional or positional[0] not in COMMANDS:
        print(__doc__)
        return 0

    cmd, rest = positional[0], positional[1:]
    handler, arity = COMMANDS[cmd]
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
        handler(*(rest[:arity] or [None]), opts, emit)
        if opts["json"]:
            print(json.dumps(buffer[0] if len(buffer) == 1 else buffer, indent=2))
    except BrokenPipeError:
        pass
    if _MISSING:
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
