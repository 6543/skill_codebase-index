# Language coverage and quirks

Every node type in `scripts/languages.py` verified by parsing real code with the
actual grammar, not read from docs. Extending registry? Do same. Run `debug-ast`
first.

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
| Vue 3 | `.vue` | `tree_sitter_typescript` | yes | same | every `<script>` block extracted; template not analysed |
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

Nix is one target not fitting class/method/interface shape other specs assume.
Pretending otherwise gave bad output until each of these handled:

- **Bindings, not functions.** `name = expr;` is only declaration form. Bindings
  registered as `defs`, rendered `name = value`. Rendering as `name()` invented
  call signature not in source.
- **Imports are ordinary applications.** No import node type exists.
  `import ./foo.nix` is `apply_expression`. Registering `apply_expression` as
  import type reported every call as import, dumping whole files into outline.
  `import_hook="nix_import"` narrow to applications whose function literally
  `import`.
- **Attribute sets nest deep.** NixOS test have hundreds of nested bindings.
  Listing all bury the two or three describing the file. `top_level_only` keep
  outermost bindings in `outline`. `decl` still find nested by name.
- **Hyphens are identifier characters.** `mysql-replication` is one name.
  Qualifier splitter used to treat `-` as member separator, truncating to
  `replication`.
- **Attribute access is reference.** `nixosTests.mysql` is `select_expression`,
  not application, so `refs` cover both. Purely functional language draw no line
  between using a value and calling it.
- **Package functions take dozens of arguments.** 51-argument formal list is
  outline noise. Summarised as `{ 51 args }`.

### Not supported

| Language | Why |
|---|---|
| Dart | methods parse as bare `declaration` nodes with no usable name field; output was `<anonymous>` rows |
| Elixir | the grammar models `defmodule`/`def`/`defp` as generic `call` nodes with `do_block`, so declarations are indistinguishable from ordinary function calls without a bespoke pass |

Better left out than shipped producing plausible noise. Revisit if either grammar
gain proper declaration nodes.

## Per-language notes

### Go

Most complete target. `methods <Type>` match on receiver, separating
`(q *fifo) Poll` from `(p *impl) Poll`. Grep cannot. Struct tags preserved
verbatim in `fields`: matters for ORM and JSON mapping questions. `outline`
surface `//go:build` lines, explaining cases like package reporting "no test
files" when tests sit behind build tag.

Interface methods are `method_elem`, not `method_declaration`, so registered as
defs to make `iface` work.

### Rust

`impl Type` and `impl Trait for Type` blocks are containers. Their functions
attributed to implementing type, not reported as free functions. `impls` work
well here: trait satisfaction is structural.

Rust line comments include trailing newline in extent, Go's do not. So
doc-comment attachment test line overlap, not exact adjacency. Remember when
adding grammar with third convention.

### Python

No separate constant declaration exists. Module-level assignments reported as
constants, class-level as fields. Assignments inside method bodies (`self.x = y`)
excluded on purpose: they belong to method scope, not class surface.

Docstrings read from first string expression in body, not from preceding
comments.

### Vue and Svelte

No maintained Python wheel exist for either grammar. `<script>` blocks extracted
with regex, parsed as TypeScript. Line numbers offset back to original file,
exact.

**Every** script block parsed, not just first. Vue 3 routinely pair
`<script setup>` with plain `<script>` holding options or named export. Svelte
pair instance script with `context="module"`. Reading only first match silently
dropped whichever came second. `outline` label each block when file have more
than one.

Template markup and styles not analysed. Handler bound only in template have no
visible caller: commonest way a `refs` or `trace` result on a component mislead.

### JavaScript and TypeScript

Anonymous callbacks (`computed(() => ...)`, `.map(x => ...)`) are control flow,
not API surface. Dropped, not listed as `<anonymous>`. Arrow functions bound to
name (`const handler = () => {}`) kept, named after binding.

`const` holding function body collapse to preview. `const` group of plain values
print in full: enum-style groups usually the reason someone look.

### C and C++

Function name and parameter list nest inside declarator chain, not exposed as
fields. That is what `name_hook="c_declarator"` handle. Preprocessor macros
reported as constants. No expansion attempted.

### Kotlin

Grammar model interfaces as `class_declaration` with modifier. `outline` cannot
label them separately, `iface` will not find them. Method and property extraction
work normally.

## Concepts and how they map

Each `LangSpec` list node types per concept:

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

Four registries sit beside specs, not inside: language-agnostic sets every
grammar contribute to.

| Registry | Used by | Contents |
|---|---|---|
| `DECISION_NODES` | `complexity` | branch points, every grammar's spelling |
| `CONTRACT_NODES` | `trace` | signature-only declarations, so dispatch is not resolved |
| `SPAWN_NODES`, `SPAWN_CALLEES` | `effects` | goroutine, await, defer, thread pool |
| `SERIALIZATION_CALLEES` | `effects` | encode/decode pairs across the ecosystem |

Grammar lacking one of these node types simply never hit it. That is why one set
cover all languages.

Interface members recognised by node type (`method_elem`, `method_signature`,
`function_signature_item`), or, for grammars giving interface methods same node
type as ordinary ones (Java is the case that matters), by checking enclosing type
against spec's `ifaces`.

## Adding a language

1. Install grammar: `pip install tree_sitter_<lang>`.
2. Write small sample exercising class, interface, function, constant, field, doc
   comment.
3. Dump tree: `python3 scripts/ts_query.py debug-ast sample.ext --depth=3`. Print
   node types with `name`/`parameters`/`type`/`body` fields.
4. Add `LangSpec` mapping each concept to node types observed.
5. Re-run `outline` on sample. Confirm every element appear once, right section,
   with doc comment.

Two failure modes worth explicit check. Both produce plausible-looking wrong
output:

- **Keyword collision.** Some grammars have anonymous keyword token sharing type
  name with real node (Ruby `class`, `module`). Walker yield only named nodes,
  which handle it. Seeing every declaration twice? This is why.
- **Comment extent.** Grammars disagree whether line comment include trailing
  newline. Doc comments come back empty? Compare comment `start_point`/`end_point`
  against declaration `start_point`.

## Where the newer features came from

Prior art read before extending this skill. Nothing copied. Ideas reimplemented.
Credit:

- **[codegraph-rust](https://github.com/Jakedismo/codegraph-rust)** — Rust
  code-graph indexer. Source of the language-agnostic cyclomatic-complexity pass
  driven by shared decision-point node set. Repo ships no LICENSE file, so none
  of its source could be reused even if wanted.
- **[graphify](https://github.com/Graphify-Labs/graphify)** (Apache-2.0) — Python
  knowledge-graph builder, also shipped as assistant skill. Three ideas adopted:
  per-result confidence labels, non-test and path-proximity tie-breakers for
  ambiguous name resolution, file-level import-cycle detection.

Both build persistent graph database. This skill deliberately not: stateless
query tool, no index to build, invalidate, or sync with edits. They trade setup
cost for richer traversal. This trades traversal depth for zero setup.

Sibling skill **`trace-codebase-flow`** solve same problem, different shape: one
flat record index (symbols, calls, callbacks, control flow, channels, boundaries)
with `search` and `trace` over it. Reading it produced `trace` and `effects` here,
refusal to resolve interface dispatch, credential and header-based generated-code
exclusion, multi-block single-file-component fix, parse-error warning with its Nix
trailing-comma exception. Command shape not adopted: this skill keep one command
per question and attach doc comments, which record model drop. Blind-spot analysis
adopted, written up in [tracing.md](tracing.md).
