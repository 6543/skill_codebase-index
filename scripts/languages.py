"""Language registry for ts_query.py.

Each LangSpec lists the tree-sitter node types that carry a given concept in
that grammar. Node type names were verified against the real grammars, not
guessed -- if you add a language, parse a sample file and dump node types
before writing the spec (see `ts_query.py debug-ast`).

Most grammars expose the same field names (`name`, `parameters`, `body`,
`type`, `return_type`, `value`), which is why one generic extractor covers
them all. Where a grammar deviates, set `name_hook`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LangSpec:
    key: str
    module: str  # pip package providing the grammar
    entry: str  # function on that module returning the language pointer
    exts: tuple  # file extensions that map to this language

    defs: tuple = ()  # functions, methods, constructors
    types: tuple = ()  # classes, structs, interfaces, enums, traits, aliases
    ifaces: tuple = ()  # subset of `types` that are pure contracts
    members: tuple = ()  # struct/class fields and properties
    consts: tuple = ()  # const/var/static declarations
    imports: tuple = ()  # import/use/require statements
    calls: tuple = ("call_expression",)
    module_decl: tuple = ()  # package/namespace/module header

    comment: tuple = ("comment", "line_comment", "block_comment")
    name_hook: str = ""  # see resolve_name() in ts_query.py
    impl_blocks: tuple = ()  # Rust-style `impl T for X` containers
    sfc: str = ""  # single-file-component host language, if any
    import_hook: str = ""  # narrows `imports` when the node type is overloaded
    top_level_only: bool = False  # outline lists only outermost declarations

    # Nodes in `calls` that *name* something without invoking it: a Nix
    # attribute select, a SQL table reference, a CSS var(). refs and trace label
    # these "reference" so a read of a table is never presented as a call.
    ref_nodes: tuple = ()
    call_hook: str = ""  # see callee_expression() in ts_query.py
    const_hook: str = ""  # narrows `consts` when the node type is overloaded
    doc_hook: str = ""  # extra doc sources beyond comments (fish --description)
    outline_hook: str = ""  # own outline renderer for non-code documents
    # PowerShell resolves `get-thing` to `Get-Thing`; matching must too.
    case_insensitive: bool = False
    # Commands with no meaning for this language. They answer "not applicable"
    # rather than "nothing found", which would wrongly suggest a search failed.
    unsupported: tuple = ()
    unsupported_reason: str = ""


GO = LangSpec(
    key="go",
    module="tree_sitter_go",
    entry="language",
    exts=(".go",),
    defs=("function_declaration", "method_declaration", "method_elem"),
    types=("type_spec", "type_alias"),
    ifaces=("interface_type",),
    members=("field_declaration",),
    consts=("const_declaration", "var_declaration"),
    imports=("import_spec",),
    module_decl=("package_clause",),
)

PYTHON = LangSpec(
    key="python",
    module="tree_sitter_python",
    entry="language",
    exts=(".py", ".pyi"),
    defs=("function_definition",),
    types=("class_definition",),
    members=("assignment",),
    consts=("assignment",),
    imports=("import_statement", "import_from_statement"),
    calls=("call",),
    comment=("comment", "line_comment", "block_comment", "string"),
    name_hook="python_doc",
)

JAVASCRIPT = LangSpec(
    key="javascript",
    module="tree_sitter_javascript",
    entry="language",
    exts=(".js", ".jsx", ".mjs", ".cjs"),
    defs=(
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "arrow_function",
        "function_expression",
    ),
    types=("class_declaration",),
    members=("field_definition", "public_field_definition"),
    consts=("lexical_declaration", "variable_declaration"),
    # `require('./x')` and `export ... from './x'` load modules too.
    imports=("import_statement", "export_statement", "call_expression"),
    import_hook="js_import",
    name_hook="js_assigned",
)

TYPESCRIPT = LangSpec(
    key="typescript",
    module="tree_sitter_typescript",
    entry="language_typescript",
    exts=(".ts", ".mts", ".cts"),
    defs=(
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "method_signature",
        "arrow_function",
        "function_expression",
    ),
    types=(
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    ),
    ifaces=("interface_declaration",),
    members=(
        "public_field_definition",
        "property_signature",
        "field_definition",
    ),
    consts=("lexical_declaration", "variable_declaration"),
    imports=JAVASCRIPT.imports,
    import_hook="js_import",
    name_hook="js_assigned",
)

TSX = LangSpec(
    key="tsx",
    module="tree_sitter_typescript",
    entry="language_tsx",
    exts=(".tsx",),
    defs=TYPESCRIPT.defs,
    types=TYPESCRIPT.types,
    ifaces=TYPESCRIPT.ifaces,
    members=TYPESCRIPT.members,
    consts=TYPESCRIPT.consts,
    imports=TYPESCRIPT.imports,
    import_hook="js_import",
    name_hook="js_assigned",
)

RUST = LangSpec(
    key="rust",
    module="tree_sitter_rust",
    entry="language",
    exts=(".rs",),
    defs=("function_item", "function_signature_item"),
    types=("struct_item", "enum_item", "trait_item", "type_item", "union_item"),
    ifaces=("trait_item",),
    members=("field_declaration", "enum_variant"),
    consts=("const_item", "static_item"),
    imports=("use_declaration",),
    module_decl=("mod_item",),
    impl_blocks=("impl_item",),
    comment=("line_comment", "block_comment", "comment"),
)

JAVA = LangSpec(
    key="java",
    module="tree_sitter_java",
    entry="language",
    exts=(".java",),
    defs=("method_declaration", "constructor_declaration"),
    types=(
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    ),
    ifaces=("interface_declaration",),
    members=("field_declaration",),
    consts=("field_declaration",),
    imports=("import_declaration",),
    module_decl=("package_declaration",),
    calls=("method_invocation",),
)

KOTLIN = LangSpec(
    key="kotlin",
    module="tree_sitter_kotlin",
    entry="language",
    exts=(".kt", ".kts"),
    defs=("function_declaration",),
    types=("class_declaration", "object_declaration"),
    members=("property_declaration",),
    consts=("property_declaration",),
    imports=("import",),
    module_decl=("package_header",),
    calls=("call_expression",),
)

SWIFT = LangSpec(
    key="swift",
    module="tree_sitter_swift",
    entry="language",
    exts=(".swift",),
    defs=("function_declaration", "init_declaration", "protocol_function_declaration"),
    types=("class_declaration", "protocol_declaration"),
    ifaces=("protocol_declaration",),
    members=("property_declaration", "protocol_property_declaration"),
    consts=("property_declaration",),
    imports=("import_declaration",),
)

CSHARP = LangSpec(
    key="c_sharp",
    module="tree_sitter_c_sharp",
    entry="language",
    exts=(".cs",),
    defs=("method_declaration", "constructor_declaration", "local_function_statement"),
    types=(
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "enum_declaration",
        "record_declaration",
    ),
    ifaces=("interface_declaration",),
    members=("field_declaration", "property_declaration"),
    consts=("field_declaration",),
    imports=("using_directive",),
    module_decl=("namespace_declaration", "file_scoped_namespace_declaration"),
    calls=("invocation_expression",),
)

RUBY = LangSpec(
    key="ruby",
    module="tree_sitter_ruby",
    entry="language",
    exts=(".rb", ".rake"),
    defs=("method", "singleton_method"),
    types=("class", "module"),
    members=("assignment",),
    consts=("assignment",),
    imports=(),
    calls=("call",),
)

PHP = LangSpec(
    key="php",
    module="tree_sitter_php",
    entry="language_php",
    exts=(".php",),
    defs=("function_definition", "method_declaration"),
    types=(
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
    ),
    ifaces=("interface_declaration",),
    members=("property_declaration",),
    consts=("const_declaration",),
    imports=("namespace_use_declaration",),
    module_decl=("namespace_definition",),
    calls=(
        "function_call_expression",
        "member_call_expression",
        "nullsafe_member_call_expression",
        "scoped_call_expression",
    ),
    call_hook="php_call",
)

C = LangSpec(
    key="c",
    module="tree_sitter_c",
    entry="language",
    exts=(".c", ".h"),
    defs=("function_definition",),
    types=("struct_specifier", "enum_specifier", "union_specifier", "type_definition"),
    members=("field_declaration",),
    consts=("preproc_def", "declaration"),
    imports=("preproc_include",),
    name_hook="c_declarator",
)

CPP = LangSpec(
    key="cpp",
    module="tree_sitter_cpp",
    entry="language",
    exts=(".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
    defs=("function_definition",),
    types=("struct_specifier", "class_specifier", "enum_specifier", "union_specifier"),
    members=("field_declaration",),
    consts=("preproc_def", "declaration"),
    imports=("preproc_include",),
    module_decl=("namespace_definition",),
    name_hook="c_declarator",
)

_SHELL_UNSUPPORTED = ("iface", "impls", "methods", "fields")
_SHELL_REASON = "shell scripts have functions and commands, no types or interfaces"

BASH = LangSpec(
    key="bash",
    module="tree_sitter_bash",
    entry="language",
    exts=(".sh", ".bash"),
    defs=("function_definition",),
    consts=("variable_assignment",),
    # `source lib.sh` / `. lib.sh` is an ordinary command node; the hook keeps
    # only those, so the file they load shows up as an import and in `cycles`.
    imports=("command",),
    import_hook="shell_source",
    calls=("command",),
    unsupported=_SHELL_UNSUPPORTED,
    unsupported_reason=_SHELL_REASON,
)

# Fish is not Bash: `set` instead of `x=`, `end` blocks, `and`/`or` keywords.
# Grammar: ram02z/tree-sitter-fish, packaged locally (packaging/fish).
FISH = LangSpec(
    key="fish",
    module="tree_sitter_fish",
    entry="language",
    exts=(".fish",),
    defs=("function_definition",),
    # Assignment is the `set` command; the hook keeps only those.
    consts=("command",),
    const_hook="fish_set",
    imports=("command",),
    import_hook="shell_source",
    calls=("command",),
    call_hook="fish_call",
    doc_hook="fish_description",
    unsupported=_SHELL_UNSUPPORTED,
    unsupported_reason=_SHELL_REASON,
)

POWERSHELL = LangSpec(
    key="powershell",
    module="tree_sitter_powershell",
    entry="language",
    # .psd1 is a module manifest: one hashtable, no code. It is parsed with the
    # same grammar, and its top-level keys are reported as constants.
    exts=(".ps1", ".psm1", ".psd1"),
    defs=("function_statement", "class_method_definition"),
    types=("class_statement", "enum_statement"),
    members=("class_property_definition", "enum_member"),
    consts=("assignment_expression", "hash_entry"),
    const_hook="ps_const",
    # `using module`, `Import-Module` and dot-sourcing are all command nodes.
    imports=("command",),
    import_hook="ps_import",
    calls=("command", "invokation_expression"),
    call_hook="ps_call",
    name_hook="powershell",
    case_insensitive=True,
    unsupported=("iface", "impls"),
    unsupported_reason="PowerShell has no interface declarations to match against",
)

SQL = LangSpec(
    key="sql",
    module="tree_sitter_sql",
    entry="language",
    exts=(".sql",),
    defs=("create_function", "create_trigger"),
    types=("create_table", "create_view", "create_materialized_view", "create_type"),
    members=("column_definition",),
    consts=("create_index", "create_sequence", "create_schema"),
    # A table read is a reference, not a call. Invocations are calls.
    calls=("invocation", "object_reference"),
    ref_nodes=("object_reference",),
    call_hook="sql_ref",
    name_hook="sql_object",
    unsupported=("iface", "impls", "methods", "effects", "complexity", "cycles"),
    unsupported_reason="SQL has no interfaces, methods, imports or async handoffs",
)

# HTML and CSS are documents, not programs. They get their own outline and their
# "declarations" are the things other files point at: element ids, rules,
# custom properties, keyframes. References are links, var() and url().
HTML = LangSpec(
    key="html",
    module="tree_sitter_html",
    entry="language",
    exts=(".html", ".htm"),
    defs=("element", "script_element", "style_element"),
    imports=("attribute",),
    import_hook="html_resource",
    calls=("attribute",),
    ref_nodes=("attribute",),
    call_hook="html_ref",
    name_hook="html_id",
    outline_hook="html",
    comment=("comment",),
    unsupported=(
        "iface",
        "impls",
        "methods",
        "fields",
        "consts",
        "effects",
        "complexity",
        "cycles",
        "trace",
    ),
    unsupported_reason=(
        "HTML elements are not functions or types; inline <script> blocks are "
        "analysed as JavaScript"
    ),
)

CSS = LangSpec(
    key="css",
    module="tree_sitter_css",
    entry="language",
    exts=(".css",),
    defs=("keyframes_statement", "declaration"),
    types=("rule_set",),
    members=("declaration",),
    imports=("import_statement",),
    calls=("call_expression", "declaration", "import_statement"),
    ref_nodes=("call_expression", "declaration", "import_statement"),
    call_hook="css_ref",
    name_hook="css",
    outline_hook="css",
    unsupported=("iface", "impls", "methods", "consts", "effects", "complexity"),
    unsupported_reason="CSS has rules and custom properties, no functions or types",
)

# Single-file components: the script block is extracted and parsed with the
# host grammar, with line numbers offset back to the original file.
VUE = LangSpec(
    key="vue",
    module="tree_sitter_typescript",
    entry="language_typescript",
    exts=(".vue",),
    defs=TYPESCRIPT.defs,
    types=TYPESCRIPT.types,
    ifaces=TYPESCRIPT.ifaces,
    members=TYPESCRIPT.members,
    consts=TYPESCRIPT.consts,
    imports=TYPESCRIPT.imports,
    import_hook="js_import",
    name_hook="js_assigned",
    sfc="script",
)

SVELTE = LangSpec(
    key="svelte",
    module="tree_sitter_typescript",
    entry="language_typescript",
    exts=(".svelte",),
    defs=TYPESCRIPT.defs,
    types=TYPESCRIPT.types,
    ifaces=TYPESCRIPT.ifaces,
    members=TYPESCRIPT.members,
    consts=TYPESCRIPT.consts,
    imports=TYPESCRIPT.imports,
    import_hook="js_import",
    name_hook="js_assigned",
    sfc="script",
)

SCALA = LangSpec(
    key="scala",
    module="tree_sitter_scala",
    entry="language",
    exts=(".scala", ".sc"),
    defs=("function_definition", "function_declaration"),
    types=(
        "class_definition",
        "trait_definition",
        "object_definition",
        "type_definition",
    ),
    ifaces=("trait_definition",),
    members=("val_definition", "var_definition", "val_declaration"),
    consts=("val_definition",),
    imports=("import_declaration",),
    module_decl=("package_clause",),
)

LUA = LangSpec(
    key="lua",
    module="tree_sitter_lua",
    entry="language",
    exts=(".lua",),
    defs=("function_declaration", "function_definition"),
    consts=("variable_declaration",),
    calls=("function_call",),
)

ZIG = LangSpec(
    key="zig",
    module="tree_sitter_zig",
    entry="language",
    exts=(".zig",),
    defs=("function_declaration",),
    types=("struct_declaration", "enum_declaration", "union_declaration"),
    members=("container_field",),
    consts=("variable_declaration",),
    name_hook="zig_parent",
)

JULIA = LangSpec(
    key="julia",
    module="tree_sitter_julia",
    entry="language",
    exts=(".jl",),
    defs=("function_definition", "short_function_definition"),
    types=("struct_definition", "abstract_definition"),
    members=("typed_expression",),
    consts=("const_statement",),
    imports=("using_statement", "import_statement"),
    module_decl=("module_definition",),
    name_hook="julia_nested",
)

# Nix has no classes, methods or interfaces. Its single declaration form is the
# binding (`name = expr;`), so bindings are registered as defs and an attribute
# set is treated as the container that owns them.
NIX = LangSpec(
    key="nix",
    module="tree_sitter_nix",
    entry="language",
    exts=(".nix",),
    defs=("binding",),
    types=(),
    consts=("inherit", "inherit_from"),
    # `import ./x`, `callPackage ./pkg.nix`, `imports = [ ./mod.nix ]`: path
    # literals are the edges between Nix files (see nix_path_refs).
    imports=("apply_expression", "path_expression"),
    # In Nix, `pkgs.foo` is attribute access rather than a call, and that is how
    # values are referenced. Treating select alongside apply makes `refs` answer
    # "where is this used", which is the question that has an answer in a purely
    # functional language with no call/reference distinction.
    calls=("apply_expression", "select_expression", "path_expression"),
    ref_nodes=("select_expression", "path_expression"),
    name_hook="nix_attrpath",
    import_hook="nix_import",
    top_level_only=True,
    unsupported=("iface", "impls", "methods", "fields"),
    unsupported_reason="Nix has bindings and attribute sets, no types or interfaces",
)

ALL = (
    GO,
    PYTHON,
    TYPESCRIPT,
    TSX,
    JAVASCRIPT,
    VUE,
    SVELTE,
    RUST,
    JAVA,
    KOTLIN,
    SWIFT,
    CSHARP,
    RUBY,
    PHP,
    CPP,
    C,
    BASH,
    FISH,
    POWERSHELL,
    SQL,
    HTML,
    CSS,
    SCALA,
    LUA,
    ZIG,
    NIX,
    JULIA,
)

BY_EXT = {ext: spec for spec in ALL for ext in spec.exts}
BY_KEY = {spec.key: spec for spec in ALL}

# Names people type for --lang and bootstrap --languages. Keys are lowercase.
ALIASES = {
    "c#": "c_sharp",
    "cs": "c_sharp",
    "csharp": "c_sharp",
    "c++": "cpp",
    "cxx": "cpp",
    "golang": "go",
    "htm": "html",
    "js": "javascript",
    "jsx": "javascript",
    "ps": "powershell",
    "ps1": "powershell",
    "pwsh": "powershell",
    "py": "python",
    "rs": "rust",
    "sh": "bash",
    "ts": "typescript",
}


def canonical_key(name):
    """Internal language key for a name or alias, or None if unknown."""
    lowered = name.strip().lower()
    if lowered in BY_KEY:
        return lowered
    return ALIASES.get(lowered)


# Extensionless scripts announce their language in the shebang. Interpreter
# basename -> language key; `env [-S] <interp>` is unwrapped first.
SHEBANG_INTERPRETERS = {
    "bash": "bash",
    "sh": "bash",
    "dash": "bash",
    "fish": "fish",
    "pwsh": "powershell",
    "node": "javascript",
    "python": "python",
    "python3": "python",
}

# Directories that are almost always noise when indexing a repo.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "target",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    ".nuxt",
    "coverage",
}

# Generated dependency manifests: large, mechanical, never architecturally
# interesting, and they can dominate a repo's line counts.
LOCKFILES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "poetry.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "go.work.sum",
        "uv.lock",
        "flake.lock",
        "pubspec.lock",
    }
)

# Matched against the lowercased file name. `test_` counts only as a prefix
# (test_api.py), or `latest_version.py` would be a test. Class-per-file
# languages name tests FooTest/FooTests; see is_test().
TEST_PATTERNS = ("_test.", ".test.", ".tests.", ".spec.", "_spec.")
TEST_PREFIXES = ("test_", "tests_")
# Directories that hold tests whatever their files are called: Rust's
# `tests/`, Maven's `src/test/`, Jest's `__tests__/`, Go's `testdata/`.
# Matched against path components below the scanned root only.
TEST_DIRS = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "spec",
        "specs",
        "testdata",
        "fixtures",
        "__fixtures__",
        "e2e",
    }
)
# Third-party code committed to the repo. Excluded by default because it
# dominates counts (a 4,000-definition minified yarn plugin was the "densest
# file" of one repo), but kept reachable with --vendored.
VENDORED_DIRS = frozenset(
    {
        "vendor",
        "third_party",
        "third-party",
        "bower_components",
        "jspm_packages",
        ".yarn",
    }
)
# A minified file is one or a few enormous lines. Above this average line
# length (over the first 64 KiB) a file is treated as minified.
MINIFIED_LINE_LENGTH = 500
MOCK_PATTERNS = ("mock_", "/mocks/", "_mock.", ".mock.")
TODO_MARKERS = ("TODO", "FIXME", "HACK", "XXX", "BUG:", "NOTE:")

# Files that should never be read, let alone indexed. A key or a .env is not
# source, and the cost of accidentally quoting one into a transcript is much
# higher than the cost of skipping a file that happened to match.
SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pgpass",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    }
)
SENSITIVE_SUFFIXES = frozenset({".jks", ".key", ".p12", ".pem", ".pfx"})
SENSITIVE_PREFIXES = (".env.",)

# Generated code announces itself in a header far more reliably than in its
# filename: mockery, protoc, sqlc, stringer and ent all emit one of these, and
# none of them follow the `mock_`/`_gen.` naming conventions consistently.
GENERATED_MARKERS = (
    b"Code generated by",
    b"DO NOT EDIT",
    b"@generated",
    b"autogenerated by",
    b"Automatically generated by",
)

# --------------------------------------------------------------------------
# effect nodes -- the places a straight call graph loses the thread
# --------------------------------------------------------------------------

# Syntax that hands work to another execution context. The call graph still
# shows the edge, but it says nothing about *when* the callee runs, which is
# usually the question being asked.
SPAWN_NODES = {
    "go_statement": "goroutine",
    "await_expression": "await",
    "await": "await",
    "spawn_expression": "spawn",
    "defer_statement": "defer",
    "yield_expression": "yield",
}

# Library entry points that spawn or defer work. Matched against the lowercased
# callee expression, so `tokio::spawn` and `Tokio::Spawn` both hit.
SPAWN_CALLEES = frozenset(
    {
        "asyncio.create_task",
        "asyncio.ensure_future",
        "asyncio.gather",
        "executor.submit",
        "process.nexttick",
        "queuemicrotask",
        "setimmediate",
        "setinterval",
        "settimeout",
        "threading.thread",
        "thread::spawn",
        "tokio::spawn",
        # PowerShell, compared casefolded like everything here
        "start-job",
        "start-threadjob",
    }
)

# Encode/decode pairs. Data identity does not survive one of these: the type on
# the far side is whatever the decoder was told to produce, not what went in.
SERIALIZATION_CALLEES = frozenset(
    {
        "bincode::deserialize",
        "convertfrom-json",
        "convertto-json",
        "export-clixml",
        "import-clixml",
        "bincode::serialize",
        "gob.newdecoder",
        "gob.newencoder",
        "json.dump",
        "json.dumps",
        "json.load",
        "json.loads",
        "json.marshal",
        "json.newdecoder",
        "json.newencoder",
        "json.parse",
        "json.stringify",
        "json.unmarshal",
        "msgpack.marshal",
        "msgpack.unmarshal",
        "objectmapper.readvalue",
        "objectmapper.writevalueasstring",
        "pickle.dumps",
        "pickle.loads",
        "proto.marshal",
        "proto.unmarshal",
        "serde_json::from_slice",
        "serde_json::from_str",
        "serde_json::to_string",
        "serde_json::to_vec",
        "toml.decode",
        "toml.unmarshal",
        "xml.marshal",
        "xml.unmarshal",
        "yaml.dump",
        "yaml.marshal",
        "yaml.safe_load",
        "yaml.unmarshal",
    }
)

# Node types that are a bare reference to something rather than a computation,
# so an argument of this shape is a value being handed over -- a callback, in
# the cases that matter here.
REFERENCE_NODES = frozenset(
    {
        "attribute",
        "field_expression",
        "identifier",
        "member_expression",
        "qualified_identifier",
        "scoped_identifier",
        "selector_expression",
    }
)

ARGUMENT_NODES = frozenset({"argument_list", "arguments", "parenthesized_expression"})

# Declarations that promise behaviour without providing it. A call whose name
# resolves to one of these is dispatched at runtime: the syntax names the
# contract, and which implementation runs is not written down anywhere in the
# file. Grammars that give interface methods the same node type as ordinary ones
# (Java) are caught instead by checking the enclosing type against `ifaces`.
CONTRACT_NODES = frozenset(
    {
        "abstract_method_signature",
        "function_signature_item",
        "method_elem",
        "method_signature",
        "method_spec",
        "protocol_function_declaration",
        "protocol_property_declaration",
    }
)


# Node types that introduce a branch in control flow, used for cyclomatic
# complexity. Listing every grammar's spelling in one set keeps the metric
# language-agnostic; a node type absent from a given grammar is simply never hit.
DECISION_NODES = frozenset(
    {
        # conditionals
        "if_statement",
        "if_expression",
        "if_let_expression",
        "elif_clause",
        "elseif_clause",
        "else_if_clause",
        "conditional_expression",
        "ternary_expression",
        "guard_statement",
        "unless",
        # loops
        "for_statement",
        "for_expression",
        "for_in_statement",
        "for_of_statement",
        "foreach_statement",
        "enhanced_for_statement",
        "while_statement",
        "while_expression",
        "do_statement",
        "repeat_statement",
        "repeat_while_statement",
        "loop_expression",
        "range_clause",
        # multi-way branches
        "switch_statement",
        "switch_expression",
        "expression_switch_statement",
        "type_switch_statement",
        "match_expression",
        "match_statement",
        "case_clause",
        "when_entry",
        "select_statement",
        "communication_case",
        # error handling
        "catch_clause",
        "except_clause",
        "rescue",
        "rescue_clause",
    }
)

# Boolean operators also branch. Counted separately because they are operator
# tokens rather than statement nodes.
DECISION_OPERATORS = frozenset({"&&", "||", "and", "or", "??", "?."})
