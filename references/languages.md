# Language coverage and quirks

Every node type in `scripts/languages.py` was verified by parsing real code with
the actual grammar, not taken from documentation. If you extend the registry,
do the same — run `debug-ast` first.

## Contents

- [Coverage table](#coverage-table)
- [Per-language notes](#per-language-notes)
- [Concepts and how they map](#concepts-and-how-they-map)
- [Adding a language](#adding-a-language)

## Coverage table

| Language | Extensions | Grammar package | Interfaces | Doc comments | Notes |
|---|---|---|---|---|---|
| Go | `.go` | `tree_sitter_go` | yes | `//` above decl | fullest support; receivers, struct tags, build tags |
| TypeScript | `.ts .mts .cts` | `tree_sitter_typescript` | yes | `//`, `/** */` | interfaces, type aliases, enums, classes |
| TSX | `.tsx` | `tree_sitter_typescript` | yes | same | separate grammar entry point |
| JavaScript | `.js .jsx .mjs .cjs` | `tree_sitter_javascript` | n/a | same | named arrow functions resolved via assignment |
| Vue 3 | `.vue` | `tree_sitter_typescript` | yes | same | `<script setup>` extracted; template not analysed |
| Svelte | `.svelte` | `tree_sitter_typescript` | yes | same | same script-extraction approach |
| Rust | `.rs` | `tree_sitter_rust` | traits | `///`, `//!` | `impl` blocks attributed to their type |
| Python | `.py .pyi` | `tree_sitter_python` | n/a | docstrings | docstring read from the body's first string |
| Java | `.java` | `tree_sitter_java` | yes | `/** */` | records and enums included |
| Kotlin | `.kt .kts` | `tree_sitter_kotlin` | partial | `/** */` | interfaces parse as `class_declaration`; not labelled separately |
| Swift | `.swift` | `tree_sitter_swift` | protocols | `///` | one `parameter` node per argument, joined by the tool |
| C# | `.cs` | `tree_sitter_c_sharp` | yes | `///` | properties reported as fields |
| Ruby | `.rb .rake` | `tree_sitter_ruby` | n/a | `#` | modules and classes both appear as types |
| PHP | `.php` | `tree_sitter_php` | yes | `/** */` | traits treated as types |
| C++ | `.cc .cpp .cxx .hpp .hh .hxx` | `tree_sitter_cpp` | n/a | `//`, `/** */` | name and params live in the declarator chain |
| C | `.c .h` | `tree_sitter_c` | n/a | same | `#define` reported as a constant |
| Bash | `.sh .bash` | `tree_sitter_bash` | n/a | `#` | functions and variable assignments only |
| Scala | `.scala .sc` | `tree_sitter_scala` | traits | `//`, `/** */` | objects, traits, case classes |
| Lua | `.lua` | `tree_sitter_lua` | n/a | `--` | functions and locals; `M.run` keeps its table prefix |
| Zig | `.zig` | `tree_sitter_zig` | n/a | `///` | struct name read from the enclosing `const` binding |
| Julia | `.jl` | `tree_sitter_julia` | n/a | `#` | name nested under `type_head` / `signature` |
| Nix | `.nix` | `tree_sitter_nix` | n/a | `#` | bindings are the only declaration form; see below |

### Nix

Nix is the one target that does not fit the class/method/interface shape the
other specs assume, and pretending otherwise produced bad output until each of
these was handled:

- **Bindings, not functions.** `name = expr;` is the only declaration form, so
  bindings are registered as `defs` and rendered as `name = value`. Rendering
  them as `name()` invented a call signature that does not exist in the source.
- **Imports are ordinary function applications.** There is no import node type;
  `import ./foo.nix` is an `apply_expression`. Registering `apply_expression` as
  the import type meant every call in the file was reported as an import, which
  dumped whole files into the outline. `import_hook="nix_import"` narrows it to
  applications whose function is literally `import`.
- **Attribute sets nest arbitrarily deep.** A NixOS test has hundreds of nested
  bindings; listing them all buries the two or three that describe the file.
  `top_level_only` keeps the outermost bindings in `outline`, while `decl` still
  finds anything nested by name.
- **Hyphens are identifier characters.** `mysql-replication` is one name. The
  qualifier splitter used to treat `-` as a member separator and truncated it to
  `replication`.
- **Attribute access is reference.** `nixosTests.mysql` is a `select_expression`,
  not an application, so `refs` covers both. In a purely functional language
  there is no distinction between using a value and calling it.
- **Package functions take dozens of arguments.** A 51-argument formal list is
  noise in an outline, so it is summarised as `{ 51 args }`.

### Not supported

| Language | Why |
|---|---|
| Dart | methods parse as bare `declaration` nodes with no usable name field; output was `<anonymous>` rows |
| Elixir | the grammar models `defmodule`/`def`/`defp` as generic `call` nodes with `do_block`, so declarations are indistinguishable from ordinary function calls without a bespoke pass |

Both are better left out than shipped producing plausible-looking noise. If
either grammar gains proper declaration nodes, revisit.

## Per-language notes

### Go

The most complete target. `methods <Type>` matches on the receiver, so it
separates `(q *fifo) Poll` from `(p *impl) Poll` — something grep cannot do.
Struct tags are preserved verbatim in `fields`, which matters for ORM and JSON
mapping questions. `outline` surfaces `//go:build` lines, which explains cases
like a package reporting "no test files" when its tests sit behind a build tag.

Interface methods are `method_elem` nodes, not `method_declaration`, so they are
registered as defs to make `iface` work.

### Rust

`impl Type` and `impl Trait for Type` blocks are containers: their functions are
attributed to the implementing type, not reported as free functions. `impls`
works well here because trait satisfaction is structural.

Rust line comments include the trailing newline in their extent while Go's do
not, so doc-comment attachment tests for line overlap rather than exact
adjacency. Keep that in mind if adding a grammar with a third convention.

### Python

There is no separate declaration for a constant, so module-level assignments are
reported as constants and class-level ones as fields. Assignments inside method
bodies (`self.x = y`) are deliberately excluded — they belong to the method's
scope, not the class's surface.

Docstrings are read from the first string expression in the body rather than
from preceding comments.

### Vue and Svelte

No maintained Python wheel exists for either grammar, so `<script>` blocks are
extracted with a regex and parsed as TypeScript. Line numbers are offset back to
the original file and are exact.

**Every** script block is parsed, not just the first. Vue 3 routinely pairs
`<script setup>` with a plain `<script>` holding options or a named export, and
Svelte pairs the instance script with `context="module"`; reading only the first
match silently dropped whichever came second. `outline` labels each block when a
file has more than one.

Template markup and styles are not analysed. A handler bound only in the
template therefore has no visible caller, which is the commonest way a `refs` or
`trace` result on a component is misleading.

### JavaScript and TypeScript

Anonymous callbacks — `computed(() => ...)`, `.map(x => ...)` — are control flow
rather than API surface, so they are dropped instead of being listed as
`<anonymous>`. Arrow functions bound to a name (`const handler = () => {}`) are
kept and named after the binding.

A `const` holding a function body is collapsed to a preview; a `const` group of
plain values is printed in full, since enum-style groups are usually the reason
someone is looking.

### C and C++

The function name and parameter list are nested inside the declarator chain
rather than exposed as fields, which is what `name_hook="c_declarator"` handles.
Preprocessor macros are reported as constants; there is no attempt to expand
them.

### Kotlin

The grammar models interfaces as `class_declaration` with a modifier, so
`outline` cannot label them separately and `iface` will not find them. Method
and property extraction works normally.

## Concepts and how they map

Each `LangSpec` lists node types per concept:

| Field | Meaning |
|---|---|
| `defs` | functions, methods, constructors, interface method signatures |
| `types` | classes, structs, interfaces, enums, traits, type aliases |
| `ifaces` | the subset of `types` that are pure contracts |
| `members` | struct and class fields, properties |
| `consts` | const, static, and module-level variable declarations |
| `imports` | import, use, require, include statements |
| `calls` | call expression node types |
| `module_decl` | package, namespace, or module header |
| `impl_blocks` | Rust-style containers that attach methods to a type |
| `name_hook` | `c_declarator`, `js_assigned`, or `python_doc` |
| `sfc` | set when the file is a single-file component |

Four registries sit alongside the specs rather than inside them, because they
are language-agnostic sets that every grammar contributes to:

| Registry | Used by | Contents |
|---|---|---|
| `DECISION_NODES` | `complexity` | branch points, every grammar's spelling |
| `CONTRACT_NODES` | `trace` | signature-only declarations, so dispatch is not resolved |
| `SPAWN_NODES`, `SPAWN_CALLEES` | `effects` | goroutine, await, defer, thread pool |
| `SERIALIZATION_CALLEES` | `effects` | encode/decode pairs across the ecosystem |

A grammar that lacks one of these node types simply never hits it, which is why
one set covers all languages.

Interface members are recognised either by node type (`method_elem`,
`method_signature`, `function_signature_item`) or, for grammars that give
interface methods the same node type as ordinary ones — Java is the case that
matters — by checking the enclosing type against the spec's `ifaces`.

## Adding a language

1. Install the grammar: `pip install tree_sitter_<lang>`.
2. Write a small sample file exercising a class, an interface, a function, a
   constant, a field, and a doc comment.
3. Dump the tree: `python3 scripts/ts_query.py debug-ast sample.ext --depth=3`.
   This prints node types with their `name`/`parameters`/`type`/`body` fields.
4. Add a `LangSpec` mapping each concept to the node types you observed.
5. Re-run `outline` on the sample and confirm every element appears once, in the
   right section, with its doc comment.

Two failure modes are worth checking explicitly, because both produce
plausible-looking wrong output:

- **Keyword collision.** In some grammars an anonymous keyword token shares its
  type name with a real node (Ruby's `class`, `module`). The walker only yields
  named nodes, which handles this — but if you see every declaration twice, this
  is why.
- **Comment extent.** Grammars disagree on whether a line comment includes its
  trailing newline. If doc comments come back empty, compare the comment's
  `start_point`/`end_point` against the declaration's `start_point`.


## Where the newer features came from

Two prior-art projects were read before extending this skill. Nothing was
copied; the ideas were reimplemented, and both are credited here:

- **[codegraph-rust](https://github.com/Jakedismo/codegraph-rust)** — a Rust
  code-graph indexer. The idea of a language-agnostic cyclomatic-complexity pass
  driven by a shared decision-point node set came from reading it. Note that the
  repository ships no LICENSE file, so none of its source could be reused even
  if that had been desirable.
- **[graphify](https://github.com/Graphify-Labs/graphify)** (Apache-2.0) — a
  Python knowledge-graph builder that also ships as an assistant skill. Three
  ideas were adopted: per-result confidence labels, non-test and path-proximity
  tie-breakers for ambiguous name resolution, and file-level import-cycle
  detection.

Both projects build a persistent graph database. This skill deliberately does
not: it stays a stateless query tool so there is no index to build, invalidate,
or keep in sync with edits. Where they trade setup cost for richer traversal,
this trades traversal depth for zero setup.

A sibling skill, **`trace-codebase-flow`**, solves the same problem with a
different shape: one flat record index (symbols, calls, callbacks, control flow,
channels, boundaries) with `search` and `trace` over it. Reading it produced the
`trace` and `effects` commands here, the refusal to resolve interface dispatch,
credential and header-based generated-code exclusion, the multi-block
single-file-component fix, and the parse-error warning with its Nix
trailing-comma exception. Its command shape was not adopted — this skill keeps
one command per question, and attaches doc comments, which the record model
drops — but its blind-spot analysis was, and is written up in
[tracing.md](tracing.md).
