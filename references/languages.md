# Language coverage and quirks

Every node type in `scripts/languages.py` verified by parsing real code with the
actual grammar, not read from docs. Extending registry? Do same. Run `debug-ast`
first.

## Contents

- [Coverage table](#coverage-table)
- [Commands that do not apply](#commands-that-do-not-apply)
- [Real-world validation](#real-world-validation)
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
| PHP | `.php` | `tree_sitter_php` | yes | `/** */` | bundled; `$this->f()`, `$x->f()`, `A::f()` keep their receiver; traits treated as types |
| C++ | `.cc .cpp .cxx .hpp .hh .hxx`, C++ `.h` | `tree_sitter_cpp` | n/a | `//`, `/** */` | name and params live in the declarator chain |
| C | `.c .h` | `tree_sitter_c` | n/a | same | `#define` as constant; prototypes listed apart |
| Bash | `.sh .bash`, shebang | `tree_sitter_bash` | n/a | `#` | functions, assignments, `source`/`.` as imports |
| Fish | `.fish`, shebang | `tree_sitter_fish` (local, `packaging/fish`) | n/a | `#`, `--description` | functions, global `set`, `source` as imports |
| PowerShell | `.ps1 .psm1 .psd1`, shebang | `tree_sitter_powershell` | n/a | `#`, `<# #>` help | functions, filters, classes, enums; case-insensitive names |
| SQL | `.sql` | `tree_sitter_sql` | n/a | `--` | tables, views, types as types; functions, triggers as defs; parsed per statement |
| HTML | `.html .htm` | `tree_sitter_html` | n/a | `<!-- -->` | document outline; inline script/style parsed with their grammars |
| CSS | `.css` | `tree_sitter_css` | n/a | `/* */` | rules, custom properties, keyframes, `@import` |
| Scala | `.scala .sc` | `tree_sitter_scala` | traits | `//`, `/** */` | objects, traits, case classes |
| Lua | `.lua` | `tree_sitter_lua` | n/a | `--` | functions and locals; `M.run` keeps its table prefix |
| Zig | `.zig` | `tree_sitter_zig` | n/a | `///` | struct name read from the enclosing `const` binding |
| Julia | `.jl` | `tree_sitter_julia` | n/a | `#` | name nested under `type_head` / `signature` |
| Nix | `.nix` | `tree_sitter_nix` | n/a | `#` | bindings are the only declaration form; lexical scope and path imports; see below |

## Commands that do not apply

These answer `not applicable to <lang>: <reason>` (JSON: `{"not_applicable": ...}`)
instead of an empty result, which would read as a search that found nothing:

| Language | Not applicable |
|---|---|
| Bash, Fish | `iface`, `impls`, `methods`, `fields` |
| Nix | `iface`, `impls`, `methods`, `fields` |
| PowerShell | `iface`, `impls` |
| SQL | `iface`, `impls`, `methods`, `effects`, `complexity`, `cycles` |
| HTML | everything but `map`, `outline`, `decl`, `refs`, `todos`, `debug-ast` (inline scripts still answer code questions) |
| CSS | `iface`, `impls`, `methods`, `consts`, `effects`, `complexity` |

## Real-world validation

Every adapter was run against a shallow clone of a real project (2026-10-14):
every command, text and JSON, 603 runs, none failed. Parse errors per region:

| Language | Project | Regions with errors | Cause |
|---|---|---|---|
| Go, Rust, Java, C#, Python, Nix, Fish, TSX, Vue | woodpecker, ripgrep, gson, MediatR, flask, disko, tide, swr | 0% | |
| Bash | nvm, ripgrep | 0.3% | nested brace expansion in a completion script |
| JavaScript, TypeScript | express, swr | 0.7%, 1.9% | Jinja inside inline scripts; one generic overload form |
| CSS | css-examples, woodpecker | 1.3% | `url(../x)` unquoted in `shape-outside`, range media queries |
| HTML | css-examples, express, flask | 7% | `</textarea\n>`, EJS and Jinja templates |
| PowerShell | Pester | 21% | grammar gaps, see below; 96% of functions still found |
| SQL | sakila (8 dialects) | 29% of statements | dialect features; every table found except Postgres `PARTITION OF` partitions and one T-SQL table |
| C, C++ | jq, fmt | 46%, 52% | macros (no preprocessor); 98% of jq functions found |

A partial file is always listed in the stderr `parse errors` note.

A second round (2026-10-14, one repository per bundled language, 18 plus PHP)
compared each command with a grep or regex ground truth. What it found, and the
state after the fixes:

| Language | Project | Before | After |
|---|---|---|---|
| C | lua | 57 of 68 files partial; `LUA_API lua_CFunction f(...)` lost 2 of 81 API functions | 26 partial; 81 of 81 |
| C++ | leveldb | `class LEVELDB_EXPORT DB` vanished; `methods DBImpl` 3 entries | 12 partial (was 29); `DB` found, `impls DB` lists `DBImpl`; 40 methods incl. out-of-class |
| SQL | graphile/starter (`pg_dump`) | 26 of 40 functions, `users` table missing | 40 of 40, 12 of 12 tables; trigger reaches its function |
| TypeScript | zod | 4 densest files partial (`<in T>`, `<out O>`) | 0 |
| CSS | animate.css | 6 files partial (`6.5% {`) | 0 |
| Bash | bats-core | 3 partial (`${1//&/\&amp;}`) | 2 |
| Fish | tide | `refs` 40 of 46 (command after `a=$x \` continuation) | 46 of 46 |
| Java | gson | `impls TypeAdapterFactory`: 20 listed, 14 wrong (anonymous classes) | declared implementors only |
| C# | MediatR | `impls IRequestHandler` matched any `Handle`; `iface IMediator` empty | base list is authoritative; inherited interfaces expanded |
| Go | woodpecker | `ctx.Err()` drawn as edge to `Runtime.Err`; `os/exec` matched `cli/exec`, false cycles | receiver typed or unresolved; imports resolved through `go.mod` |
| Rust | fd | `paths.is_empty()` drawn to free fn `is_empty`; `cycles` 0 edges | no edge; `crate::`/`super::` resolve |
| Python | flask | `self.app` argument drawn as callback; 25 cycles from `TYPE_CHECKING` imports | no callback; those imports reported apart |
| Nix | disko | `lib.mkOption` EXTRACTED to an unrelated `lib` binding; 5 of 115 path edges | lexical scope; `import`/`callPackage`/`imports` paths resolve |
| PHP | Slim | not bundled; `$x->go()` read as bare `go()` | bundled; receivers kept |

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
- **Lexical scope.** A name bound by an enclosing lambda (`{ lib, ... }:`), `let`
  or `rec` set is resolved there, never to a same-named binding elsewhere: `refs`
  labels a lambda argument `LOCAL`, a `let` name resolves to that binding
  (`EXTRACTED`), and an attribute selected off a local (`lib.mkOption`) draws no
  edge. A free name or one only a `with` can supply stays name-based, at most
  `INFERRED`.
- **Paths are the module edges.** `import ./x`, `callPackage ./pkg.nix {}` and
  `imports = [ ./mod.nix ./dir ]` (a directory means its `default.nix`) are edges
  for `cycles`, and `refs <file>` lists who imports a file with the kind. Other
  relative path literals (`src = ./.`) are references only. `./x/${name}.nix` and
  `import "${p}/x.nix"` are computed: `cycles` lists them as unresolved.

Grammar provenance: the bundle ships PyPI `tree-sitter-nix` 0.1.0 (MIT, homepage
`github.com/tree-sitter/tree-sitter-nix`, the repository now maintained as
`nix-community/tree-sitter-nix`). It predates current nix-community master, and
nixpkgs builds its own grammar from yet another revision. The Nix route of this
skill installs the same wheel, not nixpkgs' grammar, so both routes parse
identically; every node type used is pinned by `NixTest` and `NixScopeTest`.

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

Go 1.26 `new(expr)` (`new(int(x))`, `new(42)`, `new(f())`): `tree_sitter_go`
0.25.0 and upstream master hardcode `new(` Type `)` and flag the rest as `ERROR`.
`_reparse_go_new` in `ts_query.py` parse failing Go file second time with rejected
`new` identifiers swapped for same-length stand-in, so they read as plain calls.
Offsets and lines unchanged, `text()` swap name back. Calls grammar accepted
(`new([]int)`, `new(T)`) keep builtin form. Retry kept only if it lower error
count, so genuine error still reported. Drop workaround once grammar release
accept expression operand: `GoNewExprTest` then pass without it.

### Rust

`impl Type` and `impl Trait for Type` blocks are containers. Their functions
attributed to implementing type, not reported as free functions. `impls` work
well here: trait satisfaction is structural.

Rust line comments include trailing newline in extent, Go's do not. So
doc-comment attachment test line overlap, not exact adjacency. Remember when
adding grammar with third convention.

### Python

`from .x import y`, `from . import util` and `from ..a.b import c` resolve to
files in the repo (packages through `__init__.py`), so calls through those names
are followed and import cycles are found. Only names bound by an import that
resolves outside the repo count as external.

No separate constant declaration exists. Module-level assignments reported as
constants, class-level as fields. Assignments inside method bodies (`self.x = y`)
excluded on purpose: they belong to method scope, not class surface.

Docstrings read from first string expression in body, not from preceding
comments.

### SQL

Grammar: DerekStride/tree-sitter-sql. Covers PostgreSQL and MySQL best, ANSI
SQL mostly, other dialects partly. Its recovery is poor at statement level: one
statement it does not know could swallow a thousand following lines into one
`ERROR`. So files are split into statements (`;`, MySQL `DELIMITER`, Oracle `/`,
T-SQL `GO` lines; quotes, comments and `$tag$` bodies respected) and each is
parsed on its own, with its real line offset. On sakila this took table recall
from 1/16 to 16/16 for DB2 and Oracle, 2/16 to 15/16 for SQL Server.

- Types: `CREATE TABLE/VIEW/MATERIALIZED VIEW/TYPE`. Columns are fields. Defs:
  `CREATE FUNCTION/TRIGGER`. Constants: indexes, sequences, schemas.
- Names: `decl orders` and `decl app.orders` both work; quoted identifiers match
  unquoted (`"Audit Log"`).
- `refs` reports table reads and writes as `reference`, function invocations as
  calls, and a trigger's `EXECUTE FUNCTION f()` as a call. The declaration itself
  is not a reference.
- `trace` from a view or function reaches the tables it reads. PL/pgSQL bodies
  (`DECLARE`, `IF ... THEN`, loops, `RETURN`, `:=`) are not SQL; when a body fails,
  that control syntax is blanked for a second parse, so queries inside `IF` blocks
  are found. `PERFORM f()` counts as a call. `decl` still prints the body as written.
- A file that parses whole stays one parse; only a failing file is split into
  statements, so 8 MB dumps outline in seconds.
- Not parsed: T-SQL `CREATE PROCEDURE`, Oracle PL/SQL blocks, Postgres
  `PARTITION OF`, SQLite `AUTOINCREMENT` triggers, psql meta-commands
  (`\restrict`). Each costs only its own statement. Dynamic SQL is invisible.

Dialect coverage: PostgreSQL (including `pg_dump` output and PL/pgSQL bodies)
and MySQL are the supported dialects; ANSI SQL parses mostly; SQLite, T-SQL,
Oracle and DB2 parse statement by statement, partly. Statements the grammar
rejects are retried with same-length masks, applied only when they lower the
error count: function attribute lines (`SET search_path TO ...`, `SECURITY
DEFINER`, `STABLE`, ...), `OPERATOR(schema.op)`, parameter defaults
(`DEFAULT NULL::citext`), keyword-named parameters (`name text`), PL/pgSQL
control flow and `=` assignments. As a last resort a routine's body is blanked:
the declaration survives, references inside the body do not, and stderr lists
those routines. `decl` always prints the original text.

### PHP

Bundled in the common profile (PyPI `tree-sitter-php` 0.25.1, MIT). A member call
keeps its receiver (`$this->save()`, `$x->go()`, `self::make()`,
`Foo::bar()`), so `$this->`/`self::` resolve in the class, a typed property or
parameter (`private Store $store`, `function f(Store $s)`) selects that class,
and an untyped variable draws no edge. `use App\Routing\Route;` resolves through
the trailing path (PSR-4: `src/Routing/Route.php`). `impls` lists classes whose
`implements`/`extends` clause names the interface.

### PowerShell

Grammar: airbus-cert/tree-sitter-powershell 0.26.4. Functions and filters
(`function_statement`), classes with properties and methods, enums. `<# .SYNOPSIS
#>` help above a function is its doc comment. Calls are commands
(`Get-Thing -Name x`) and member invocations (`$this.Run()`, `[T]::new()`), matched
case-insensitively. Imports: `using module`, `Import-Module`, dot-sourcing (`. ./x.ps1`);
`& ./x.ps1` runs a script and is not an import. `.psd1` manifests list their
top-level keys as constants. `Start-Job`, `Start-ThreadJob` are spawns,
`ConvertTo-Json` and friends are boundaries.

Grammar gaps, still at upstream HEAD (2026-07): empty script blocks (`$x = {}`),
comma lists as command arguments (`Get-Module A, B`), `param (` with a space,
`$PSScriptRoot/path` arguments. Error recovery keeps most declarations.

### Fish

Not Bash: parsed with ram02z/tree-sitter-fish, packaged locally because no Python
distribution exists. `function name -d 'text'` contributes its description as
doc. Event handlers (`--on-event`) show in the signature line; the event wiring
itself leaves no call edge. A call whose name is computed (`_item_$name`) is
listed by `trace` under `unresolved` as a runtime name, not dropped. Global `set` is a constant, `set -l` inside a function
is not. `source`/`.` are imports and take part in `cycles`. Calls inside command
substitutions, pipes and `and`/`or` chains are found. Dynamic names
(`function $name`, `eval`) are listed as written and resolve nothing.

### HTML and CSS

HTML `outline`: title, landmarks and elements with an `id` (nested), headings,
links and resources (`<link>` kind from its `rel`), and inline code blocks, which
are then outlined with the JavaScript/TypeScript or CSS grammar. `decl top-nav`
finds `<nav id="top-nav">`. `refs settings` finds `href="#settings"` and
`<label for=settings>`. `refs site.css` finds every page linking it.

CSS `outline`: imports, custom properties (with the rule they sit in), rules
(with their `@media`/`@layer` context), at-rules. `decl .card` finds a rule even
inside a selector list. `fields .card` lists its declarations. `refs --brand`
finds `var(--brand)`; `refs fade-in` finds `animation: fade-in`. Tailwind v4
statement at-rules (`@plugin "x";`, `@reference`, `@custom-variant`) are not in the
grammar; on error their arguments are blanked and the file parsed again, so they
no longer corrupt the following rule.

### Vue and Svelte

No maintained Python wheel exist for either grammar. `<script>` blocks extracted
with regex, parsed as TypeScript. Line numbers offset back to original file,
exact.

**Every** script block parsed, not just first. Vue 3 routinely pair
`<script setup>` with plain `<script>` holding options or named export. Svelte
pair instance script with `context="module"`. Reading only first match silently
dropped whichever came second. `outline` label each block when file have more
than one.

Template markup and styles not analysed; `outline` says so. Handler bound only in
template have no visible caller: commonest way a `refs` or `trace` result on a
component mislead.

### JavaScript and TypeScript

Anonymous callbacks (`computed(() => ...)`, `.map(x => ...)`) are control flow,
not API surface. Dropped, not listed as `<anonymous>`. Arrow functions bound to
name (`const handler = () => {}`) kept, named after binding.

`const` holding function body collapse to preview. `const` group of plain values
print in full: enum-style groups usually the reason someone look.

### C and C++

Function name and parameter list nest inside declarator chain, not exposed as
fields. That is what `name_hook="c_declarator"` handle: it follows pointer and
parenthesized declarators and takes the `name` of a qualified one, so
`void app::run()` is `run` and `int (*pick(void))(int)` is `pick(void)`. Return
types keep qualifiers and pointers (`-> const char *`).

- `struct node *next` names a struct with the same node type that defines one;
  only a specifier with a body is listed as a type. An anonymous struct under a
  `typedef` is shown as the typedef, with its fields.
- Prototypes (`void f(int);`) are listed under `prototypes`, apart from variables.
- `.h` is C++ when it uses something C cannot (`namespace`, `template<`, `class`
  with a body, access specifiers, `<iostream>`-style includes), else C.
- Preprocessor macros reported as constants. No expansion attempted, so macro
  wrappers (`FMT_BEGIN_NAMESPACE`) leave partial parses; a garbled signature is cut
  to one line.
- Attribute-like macros are blanked before parsing: export/visibility macros
  (`LUA_API`, `LEVELDB_EXPORT`, `FOO_DLL`) everywhere outside `#` lines, and on
  error lines also `class MACRO Name`, a leading all-caps macro before a
  declaration, and trailing annotations (`EXCLUSIVE_LOCKS_REQUIRED(mu_)`,
  `GUARDED_BY(mu)`). `decl` prints the line as written.
- `Status DBImpl::Get(...) {}` belongs to `DBImpl`: `methods` lists out-of-class
  definitions (flagged) and in-class declarations without a definition in scope.
  `impls` follows `: public Base`.
- `#include "x.h"` resolves beside the includer first, then by path suffix, so
  `cycles` sees include loops.

### Grammar gaps masked on error lines

Same-length masks, kept only when they lower the error count:

| Language | Syntax the grammar rejects | Mask |
|---|---|---|
| TypeScript | variance annotations `<in T>`, `<out O>`, `<in out I>` (TS 4.7) | keyword blanked |
| CSS | fractional keyframe selector `6.5% {` | `65% {` |
| Bash | `&`, `<`, `>` in `${x//pat/rep}`; `$` offset in `${x:$i:1}` | character replaced |
| C, C++ | see C and C++ above | macro blanked |

Still not parsed: Lua's `cast(int *, ud)`-style type-argument macros and other
macros whose expansion changes syntax; PowerShell gaps above; `[[ (a || b) ]]`
grouping in Bash.

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

1. Pin grammar in `vendor/inventory.json` (`grammars`, `profiles.all`), add a
   fixture to `FIXTURES` in `scripts/runtime_probe.py`, then
   `python3 scripts/bootstrap.py --languages <key> --force`. Bundling it too means
   adding it to `profiles.common` and running `scripts/build_wheelhouse.py`
   (see [bootstrap.md](bootstrap.md)).
2. Write small sample exercising class, interface, function, constant, field, doc
   comment.
3. Dump tree: `python3 scripts/ts_query.py debug-ast sample.ext --depth=3`. Print
   node types with `name`/`parameters`/`type`/`body` fields.
4. Add `LangSpec` mapping each concept to node types observed.
5. Re-run `outline` on sample. Confirm every element appear once, right section,
   with doc comment.
6. Run it on a real project in that language: shallow clone, `map`, `outline` the
   densest file, `refs` and `trace` a function called from several files. Toy
   samples hide grammar gaps and error-recovery behaviour.
7. Set `unsupported` for commands with no meaning in the language.

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

- **[codegraph-rust](https://github.com/Jakedismo/codegraph-rust)**: Rust
  code-graph indexer. Source of the language-agnostic cyclomatic-complexity pass
  driven by shared decision-point node set. Repo ships no LICENSE file, so none
  of its source could be reused even if wanted.
- **[graphify](https://github.com/Graphify-Labs/graphify)** (Apache-2.0): Python
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
