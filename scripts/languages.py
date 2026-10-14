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
    imports=("import_statement",),
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
    imports=("import_statement",),
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
        "scoped_call_expression",
    ),
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

BASH = LangSpec(
    key="bash",
    module="tree_sitter_bash",
    entry="language",
    exts=(".sh", ".bash"),
    defs=("function_definition",),
    consts=("variable_assignment",),
    calls=("command",),
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
    imports=("apply_expression",),
    # In Nix, `pkgs.foo` is attribute access rather than a call, and that is how
    # values are referenced. Treating select alongside apply makes `refs` answer
    # "where is this used", which is the question that has an answer in a purely
    # functional language with no call/reference distinction.
    calls=("apply_expression", "select_expression"),
    name_hook="nix_attrpath",
    import_hook="nix_import",
    top_level_only=True,
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
    SCALA,
    LUA,
    ZIG,
    NIX,
    JULIA,
)

BY_EXT = {ext: spec for spec in ALL for ext in spec.exts}
BY_KEY = {spec.key: spec for spec in ALL}

# Directories that are almost always noise when indexing a repo.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "vendor",
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

TEST_PATTERNS = ("_test.", ".test.", ".spec.", "test_", "_spec.")
MOCK_PATTERNS = ("mock_", "/mocks/", "_mock.", ".mock.")
TODO_MARKERS = ("TODO", "FIXME", "HACK", "XXX", "BUG:", "NOTE:")


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
