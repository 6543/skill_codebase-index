---
name: "codebase-index"
description: 'Structural code search, call-graph tracing and indexing with tree-sitter for Go, TypeScript, JavaScript, Vue, Svelte, Rust, Python, Java, Kotlin, Swift, Scala, C#, C, C++, Ruby, PHP, Lua, Zig, Julia, Nix, Bash, Fish, PowerShell, SQL, HTML and CSS, with an offline setup. Use this INSTEAD of grep to explore an unfamiliar codebase: getting oriented in a new repo, following a code path either way, finding what implements an interface, listing a type''s methods or fields, reading a declaration with its doc comment, auditing TODOs, import cycles, async handoffs, serialization boundaries, complexity. Trigger it on "how does X work in this repo", "where is Y handled", "what calls this", "map this codebase", "what implements Z", "trace the flow from A to B", "which tables does this touch", or planning a feature against existing code, even if tree-sitter is never mentioned. Grep matches lines; this matches declarations and the edges between them.'
---

# Codebase Index

Grep answer "which lines contain this text". Most codebase questions not that.
They are "what this type own", "what this interface require", "why written this
way", "where control go next". Structure questions. Structure live in syntax
tree, not in text.

`scripts/ts_query.py` parse source with tree-sitter, query tree. Return whole
declarations, doc comments attached. One call replace many rounds of
grep-then-read-with-context.

## Setup

One command. Works offline: skill bundle pinned, hash-checked wheels for CPython
3.12 and 3.13 on Linux x86_64 (glibc). Other platforms get same pinned versions
from PyPI.

```bash
python3 scripts/bootstrap.py
```

Build isolated environment under user cache dir (never in skill, never in repo
being read), probe every grammar by parsing real snippet, print one JSON report.
Already set up: reused in about a second. After that, plain
`python3 scripts/ts_query.py ...` switch into that environment by itself, so
commands below work unchanged.

Default profile `common` = Go, Rust, JavaScript, TypeScript, TSX, Vue, Nix,
Python, Java, SQL, Bash, Fish, C, C#, C++, PowerShell, HTML, CSS, PHP. Kotlin,
Swift, Scala, Ruby, Lua, Zig, Julia, Svelte: `--profile all` (needs PyPI). One
language: `--languages go,nix` (aliases ok: `ts`, `c#`, `pwsh`). No network
allowed: `--offline` (fail naming what bundle lack, never half-install). NixOS:
`nix run path/to/skill -- map .`. Flags, report fields, recovery:
[references/bootstrap.md](references/bootstrap.md).

Do not `pip install` grammars into global Python. PEP 668 systems refuse, Nix
cannot, and unpinned grammar is how parser ABI mismatch get in.

Check what is installed: `python3 scripts/ts_query.py langs`. Missing grammar not
fatal: those files skipped, note to stderr.

## Workflow for unfamiliar repo

Work outside in. Each step narrow next.

**1. Map it.** Language mix, size, where declarations cluster:

```bash
python3 scripts/ts_query.py map /path/to/repo
```

"Densest files" list is useful part. Most declarations usually mean API surface
(`store.go`, `api/index.ts`, `interface.go`), not busiest logic. Read those first.

**2. Outline files that matter.** Module header, imports, types with members,
constants, free functions. Each with doc comment:

```bash
python3 scripts/ts_query.py outline path/to/file.go
```

**3. Follow thread.** Pick command matching question:

| Question | Command |
|---|---|
| What this declaration actually say? | `decl <Name> <root>` |
| Who call this? | `refs <Name> <root>` |
| What this contract require? | `iface <Name> <root>` |
| What this type own? | `methods <Type> <root>` |
| What data this hold, with tags? | `fields <Type> <root>` |
| What enum values / constants? | `consts <root>` |
| Which tables does this SQL read? | `trace <view-or-function> <root>` |
| What links here / what does page load? | `refs <file-or-#id> <root>`, `outline page.html` |
| What authors flag unfinished? | `todos <root>` |
| What satisfy this interface? | `impls <Iface> <root>` |
| What happen when this run, several hops out? | `trace <Name> <root>` |
| What reach this, several hops back? | `trace <Name> <root> --callers` |
| Where thread of control get dropped? | `effects <root>` |
| Which functions hardest to follow? | `complexity <root>` |
| Circular imports? | `cycles <root>` |

`<root>` = file or directory. Directories walked recursively.

Question with no meaning for a language (interfaces in CSS, goroutines in SQL)
answer `not applicable`, never empty "nothing found". Mixed directory: command
run on languages it fit, skipped ones named at end.

**4. One hop not enough → trace.** `refs` answer "who call this". `trace` follow
answer onward, print tree:

```bash
python3 scripts/ts_query.py trace Reconcile /path/to/repo --depth=2
python3 scripts/ts_query.py trace Reconcile /path/to/repo --callers
```

Every edge carry confidence label plus `file:line` it came from. Edges syntax
cannot settle are not drawn. They listed below, with candidates, under
`unresolved`. That block often most informative part: interfaces live there.

**5. Check what graph cannot carry.** Call graph show A reach B. Not show B run
on other goroutine, value arrive through channel, value was different type
before crossing JSON boundary:

```bash
python3 scripts/ts_query.py effects /path/to/repo --kind=boundary
```

Kinds: `spawn` (goroutine, await, defer, thread pool), `channel` (Go send and
receive), `boundary` (encode/decode pair), `callback` (function handed to
something that call it later). Read [references/tracing.md](references/tracing.md)
before concluding anything from trace of async code.

## Why these beat grep equivalent

- `decl` return **whole declaration**, bounded by syntax tree, plus doc comment
  above it. `grep -C 20` guess boundary, cut long functions in half.
- `methods` disambiguate by **receiver**. Grep common method name like `Poll`
  return every type's version plus generated mocks. `methods fifo` return one
  type's set.
- `iface` list contract requirements **with each method's doc comment**. Design
  rationale live there. Grep cannot tie comment to thing it document.
- `refs` keep **full qualifier** (`p.q.Poll` vs `s.scheduler.Poll`) and name
  enclosing declaration. Call list double as call-graph sketch.
- `todos` attribute each marker to containing declaration. Flat list become map
  of known debt.
- `impls` read the `implements`/`extends`/base clause where language has one
  (Java, C#, Kotlin, Swift, Scala, PHP, Rust `impl Trait for`, C++ bases),
  following the declared hierarchy. Go, and structural matches in TS/Python,
  come from method sets: no textual pattern exist for "this type satisfy that
  interface" in Go.
- `trace` keep following. Hand-walking a chain cost one `refs` plus one file read
  per hop, and skipped hop usually the one that mattered.
- `effects` find handoffs that break a trace: goroutine, channel, encoder,
  handler passed to router. None have textual signature worth grepping. Each one
  is place where confident-looking trace quietly stop being true.

Test files (`*_test.go`, `test_*.py`, `*.test.ts`, `*.spec.*`, `*.Tests.ps1`,
`FooTest.java`, `FooTests.cs`, anything under `test/`, `tests/`, `__tests__/`,
`spec/`, `testdata/`, `fixtures/`, `e2e/`), generated mocks, files with
machine-written header, vendored dirs (`vendor/`, `third_party/`, `.yarn/`,
`bower_components/`) and minified files: excluded by default. They dominate
results, rarely answer question. `map` prints how many each rule left out. Add
`--tests`, `--mocks` or `--vendored` when wanted. Credential files (`.env*`, `*.pem`, `*.key`, `id_rsa`,
`credentials.json`) never read at all. Files over 2 MB skipped as data, not source.

## Confidence labels

`refs` and `trace` cannot resolve types. Instead of guessing silently, they label
every edge with how much the answer can be trusted:

| Label | Meaning |
|---|---|
| `EXTRACTED` | the callee is declared in this same file, and the call is bare or on `this`/`self` (or the Go receiver); safe to rely on |
| `INFERRED` | exactly one plausible declaration survived ranking, in another file |
| `AMBIGUOUS` | several remain; `refs` shows its best guess with a count of the rest |
| `UNRESOLVED` | no target readable off syntax: receiver of unknown type or outside the repo, or link to a missing file |
| `LOCAL` | a lambda parameter or local binding (Nix), not a declaration |

`refs` also labels a hit `reference` when it names something without calling it:
a SQL table read, a Nix attribute select, a CSS `var()`, an HTML link. A trigger's
`EXECUTE FUNCTION f()` counts as a call.

`trace` is stricter than `refs`: where several candidates survive, it draws no
edge at all and lists the call under `unresolved` with every candidate. A shell
command whose name is built at runtime (`"$handler"`, `_item_$x`, `& $cmd`) is
listed there as `dynamic`. A call
naming an interface member is separated out as `dispatch`, because that is not an
ambiguity to be broken by better ranking: the target is chosen at runtime and is
not written down anywhere in the file.

A qualified call judged by receiver first, name second. `this`/`self`/Go
receiver: enclosing type. Imported repo module or Go package: its functions.
Type name: its static members. Local or parameter whose type written in the
function (`s Store`, `s: Store`, `s := &Store{}`, `new Store()`, `Store s`), or
field of typed receiver (`o.inner.Poll()`): that type's methods. Type outside
repo (`ctx context.Context`, `Vec`): no edge. Anything else: receiver of unknown
type, no edge, candidates listed as `receiver` under `unresolved`. A method call
never resolves to free function where language forbids it.

Ranking uses positional signals, not type information: production code is
assumed not to call test fixtures, a nearer declaration in the directory tree
beats a distant one, and a same-file declaration wins only for a bare call or a
call on `this`/`self`. `view.render()` names some other object, so a `render` in
the same file is no evidence for it and the call stays `AMBIGUOUS`. A qualified call such as `p.q.Poll` inside a method also
named `Poll` excludes that enclosing method, since it is dispatching on a
different receiver. A qualified call whose head names an imported package that
resolves outside the repo (`rand.New`) is dropped rather than matched against a
same-named local declaration.

Report the label alongside the finding. An `AMBIGUOUS` result presented as fact
is worse than no result.

## Filters

```
--tests          include test files
--mocks          include generated mocks and header-marked generated code
--lang=<key>     restrict to one language (see `langs`; aliases like ts, c#, pwsh)
--json           structured output: one record per row (refs, decl, methods,
                 fields, consts, todos, iface) or one object (map, outline,
                 trace, impls, cycles)
--vendored       include vendored dirs and minified files
--no-gitignore   index everything, ignoring the repo's .gitignore
--min=<n>        complexity: only report functions scoring at or above n
--max-len=<n>    cycles: longest cycle to search for (default 6)
--depth=<n>      trace: hops to follow (default 3)
--callers        trace: follow inbound edges; --both for either direction
--kind=<k>       effects: spawn, channel, boundary or callback
--max-bytes=<n>  skip files larger than n bytes (default 2000000)
--               end of options: `decl -- --json` for a name that looks like a flag
```

Names starting with `--` that are not flags (CSS `--brand`) are names, no `--`
needed. Extensionless scripts recognised by shebang (`#!/usr/bin/env fish`).

`.gitignore` honoured by default: negation rules, per-directory files. Generated
and vendored code stay out with no hardcoded exclusion list. Lockfiles always
skipped.

## Session state

Parse results cached in memory for lifetime of one command, so repo-wide commands
do not re-parse same file. Nothing written to disk. This is helper run against
code as it is right now, not index to build and sync. No staleness to reason
about, no cache to invalidate after edit.

## Reporting findings

Cite `file:line` per hop so user jump straight there. Quote doc comment when it
explain a decision: usually the part worth surfacing. Say plainly when something
could not be resolved instead of inferring. See limits below.

## Limits worth stating out loud

Syntactic analysis, not semantic. It does not resolve types, so:

- `refs` matches the trailing identifier of a call, then judges the receiver
  (see Confidence labels). It types a receiver only from what is written in
  the enclosing function or a field declaration; `x := build()` stays unknown.
- `trace` is built from those same edges, so its reach is bounded by them. A
  reflected call, a call through a generated client, a handler resolved from a
  string route table, and an interface with several implementors all end the
  chain. They end it *visibly*, in the `unresolved` block, rather than silently;
  but an empty `unresolved` block is not proof that nothing was missed.
- `effects` detects channels in Go only, and reports a callback whenever an
  argument names a function declared in this repo, so a local variable that
  shadows a function name shows up as one.
- `impls` with a declared clause is syntax, not guess. A structural match (Go,
  TS/Python without clause) compares **method names only**, not signatures, and
  does not expand embedded members: strong hint to verify, not proof.
- `cycles` resolves import targets to files: relative paths, Go module paths
  via `go.mod`, Rust `crate::`/`super::`, C `#include`, CommonJS `require`,
  re-exports, dotted module paths by suffix, `~/`/`@/` aliases. Python imports
  under `if TYPE_CHECKING:` or inside functions do not run at import time and
  are counted apart. A language with no resolved edge (C# namespaces) is named:
  no cycle there is no evidence. Reported cycle solid; empty result not proof.
- `complexity` counts branch points structurally. It measures how many paths a
  reader must follow, which correlates with but is not the same as difficulty: a
  long flat switch scores high and is easy to read.
- Single-file components (`.vue`, `.svelte`) are parsed by extracting every
  `<script>` block and parsing each as TypeScript. Line numbers are offset back to
  the real file, but template markup is not analysed (`outline` says so), so a
  handler bound only in the template has no visible caller.
- HTML pages are outlined as documents (title, landmarks, ids, headings, links,
  scripts). Inline `<script>` and `<style>` are parsed as JavaScript/TypeScript and
  CSS at their real line numbers. Template syntax (Jinja, EJS) is not HTML and
  marks the file partial.
- SQL is parsed one statement at a time, so a statement in a dialect the grammar
  does not know (T-SQL procedures, Oracle PL/SQL, Postgres `PARTITION OF`) costs
  only itself. PostgreSQL (incl. `pg_dump`) and MySQL parse best; rejected
  function headers and PL/pgSQL are masked and retried, and a routine whose body
  still fails keeps its declaration with the body unread (stderr lists them). A
  table reference is syntax, not proof the statement runs.
- C and C++ are parsed without the preprocessor. Export/attribute macros
  (`LUA_API`, `class LEVELDB_EXPORT DB`, `GUARDED_BY(mu)`) are blanked; other
  macro-heavy code (`FMT_BEGIN_NAMESPACE`, X-macros) parses partially and
  declarations around it are usually still found. `methods` includes
  out-of-class `Type::method` definitions.
- TypeScript `<in T>`/`<out T>`, CSS `6.5% {` and Bash `${x//&/..}` are not in
  their grammars; they are masked on error and parsed again.
- PowerShell's grammar rejects some common forms (`$x = {}`, `Get-Module A, B`,
  `param (`), present at upstream HEAD too. Error recovery still finds about 96%
  of functions. Names match case-insensitively, as PowerShell does.
- Kotlin's grammar represents interfaces as `class_declaration`, so `outline` does
  not label them separately. Dart and Elixir are not supported: their grammars do
  not expose declaration names in a usable form.
- Go's grammar (`tree_sitter_go` 0.25.0, newest release) predates Go 1.26
  `new(expr)`. A Go file that fails to parse is parsed again with the rejected
  `new` calls read as ordinary calls, so `new(int(x))` no longer marks the file
  partial and calls inside the operand are found. Any other syntax newer than the
  grammar still lands in the stderr `parse errors` note: locate it with
  `debug-ast`, look for the `ERROR` node.
- Nix has no functions, classes or interfaces in the C sense, so `iface`,
  `methods`, `fields` and `impls` do not apply. `outline`, `decl`, `refs`,
  `trace`, `consts`, `todos`, `complexity` and `cycles` all work, and for Nix
  `refs` reports attribute references as well as applications, since a purely
  functional language draws no line between using a value and calling it.
  Laziness means the graph shows what refers to what, never what runs when.
  Names follow lexical scope: a lambda argument is `LOCAL`, a `let` name resolves
  to its binding. `import ./x`, `callPackage ./p.nix`, `imports = [ ./m ]` are
  file edges for `cycles` and `refs <file>`; `./${x}.nix` is listed unresolved.

When a question genuinely needs type resolution, say so and reach for the
language's own tooling (`gopls`, `rust-analyzer`, `tsc`, an LSP) instead of
guessing.

## Adding a language

Grammars vary. Verify before writing spec, do not guess node names:

```bash
python3 scripts/ts_query.py debug-ast sample.ext --depth=3
```

Then add `LangSpec` to `scripts/languages.py` listing node types carrying each
concept. Most grammars expose same field names (`name`, `parameters`, `body`,
`type`, `return_type`), so one generic extractor cover all. Set `name_hook` only
where grammar deviate, as C and C++ do by burying function name in declarator
chain.

Per-language coverage and quirks: `references/languages.md`. How to read a trace
without over-claiming: `references/tracing.md`.

## Tests

`python3 -m unittest discover -s scripts/tests -t scripts` build throwaway repos,
assert behaviours this document promise: receiver disambiguation, confidence
labels, refusal to resolve interface dispatch, credential exclusion, Nix,
single-file-component, SQL, HTML/CSS quirks, and an offline bootstrap from the
bundle. Run with bootstrapped python. Run after changing a `LangSpec`. Wrong node
type usually produce plausible output, not error. `nix flake check` run same suite
plus a probe of every bundled grammar.
