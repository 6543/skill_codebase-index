---
name: codebase-index
description: Structural code search, call-graph tracing and indexing using tree-sitter, covering Go, TypeScript, JavaScript, Vue, Svelte, Rust, Python, Java, Kotlin, Swift, Scala, C#, Ruby, PHP, C, C++, Lua, Zig, Julia, Nix, and Bash. Use this INSTEAD of grep to explore or understand an unfamiliar codebase: getting oriented in a new repo, following a code path in either direction, finding what implements an interface, listing a type's methods or fields, reading a declaration with its doc comment, auditing TODOs, spotting circular imports, finding async handoffs and serialization boundaries, or ranking functions by complexity. Trigger it on "how does X work in this repo", "where is Y handled", "what happens when Z runs", "what calls this", "map this codebase", "what implements Z", "show me the API surface", "trace the flow from A to B", or a request to plan a feature against existing code, even if tree-sitter is never mentioned. Grep matches lines; this matches declarations and the edges between them.
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

Need `tree_sitter` plus one grammar package per language. Check first:

```bash
python3 scripts/ts_query.py langs
```

Missing grammar print exact `pip install` line. Missing not fatal: those files
skipped, note to stderr. Install only languages repo contain. `map` tell which.

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
| What authors flag unfinished? | `todos <root>` |
| What satisfy this interface? | `impls <Iface> <root>` |
| What happen when this run, several hops out? | `trace <Name> <root>` |
| What reach this, several hops back? | `trace <Name> <root> --callers` |
| Where thread of control get dropped? | `effects <root>` |
| Which functions hardest to follow? | `complexity <root>` |
| Circular imports? | `cycles <root>` |

`<root>` = file or directory. Directories walked recursively.

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
- `impls` derive implementors from method sets. No textual pattern exist for
  "this type satisfy that interface" in Go or Rust.
- `trace` keep following. Hand-walking a chain cost one `refs` plus one file read
  per hop, and skipped hop usually the one that mattered.
- `effects` find handoffs that break a trace: goroutine, channel, encoder,
  handler passed to router. None have textual signature worth grepping. Each one
  is place where confident-looking trace quietly stop being true.

Test files, generated mocks, files with machine-written header: excluded by
default. They dominate results, rarely answer question. Add `--tests` or
`--mocks` when wanted. Credential files (`.env*`, `*.pem`, `*.key`, `id_rsa`,
`credentials.json`) never read at all. Files over 2 MB skipped as data, not source.

## Confidence labels

`refs` and `trace` cannot resolve types. Instead of guessing silently, they label
every edge with how much the answer can be trusted:

| Label | Meaning |
|---|---|
| `EXTRACTED` | the callee is declared in this same file; safe to rely on |
| `INFERRED` | exactly one plausible declaration survived ranking |
| `AMBIGUOUS` | several remain; `refs` shows its best guess with a count of the rest |

`trace` is stricter than `refs`: where several candidates survive, it draws no
edge at all and lists the call under `unresolved` with every candidate. A call
naming an interface member is separated out as `dispatch`, because that is not an
ambiguity to be broken by better ranking — the target is chosen at runtime and is
not written down anywhere in the file.

Ranking uses two positional signals, not type information: production code is
assumed not to call test fixtures, and a nearer declaration in the directory tree
beats a distant one. A qualified call such as `p.q.Poll` inside a method also
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
--lang=<key>     restrict to one language (see `langs`)
--json           machine-readable output, for piping into further analysis
--no-gitignore   index everything, ignoring the repo's .gitignore
--min=<n>        complexity: only report functions scoring at or above n
--max-len=<n>    cycles: longest cycle to search for (default 6)
--depth=<n>      trace: hops to follow (default 3)
--callers        trace: follow inbound edges; --both for either direction
--kind=<k>       effects: spawn, channel, boundary or callback
--max-bytes=<n>  skip files larger than n bytes (default 2000000)
```

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

- `refs` matches the trailing identifier of a call. It shows `p.q.Poll` but
  cannot prove `p.q` is a `*fifo`. Same-named methods on different types collide.
- `trace` is built from those same edges, so its reach is bounded by them. A
  reflected call, a call through a generated client, a handler resolved from a
  string route table, and an interface with several implementors all end the
  chain. They end it *visibly*, in the `unresolved` block, rather than silently —
  but an empty `unresolved` block is not proof that nothing was missed.
- `effects` detects channels in Go only, and reports a callback whenever an
  argument names a function declared in this repo, so a local variable that
  shadows a function name shows up as one.
- `impls` compares **method names only**, not signatures, and does not expand
  embedded or inherited members. Treat it as a strong hint to verify, not proof.
- `cycles` resolves import targets to files by path and module name. A cycle
  routed through a re-exporting barrel file or a build-tool path alias can be
  missed, so a reported cycle is solid evidence but an empty result is not proof
  of absence.
- `complexity` counts branch points structurally. It measures how many paths a
  reader must follow, which correlates with but is not the same as difficulty: a
  long flat switch scores high and is easy to read.
- Single-file components (`.vue`, `.svelte`) are parsed by extracting every
  `<script>` block and parsing each as TypeScript. Line numbers are offset back to
  the real file, but template markup is not analysed — so a handler bound only in
  the template has no visible caller.
- Kotlin's grammar represents interfaces as `class_declaration`, so `outline` does
  not label them separately. Dart and Elixir are not supported: their grammars do
  not expose declaration names in a usable form.
- Nix has no functions, classes or interfaces in the C sense, so `iface`,
  `methods`, `fields` and `impls` do not apply. `outline`, `decl`, `refs`,
  `trace`, `consts`, `todos`, `complexity` and `cycles` all work, and for Nix
  `refs` reports attribute references as well as applications, since a purely
  functional language draws no line between using a value and calling it.
  Laziness means the graph shows what refers to what, never what runs when.

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
labels, refusal to resolve interface dispatch, credential exclusion, Nix and
single-file-component quirks. Run after changing a `LangSpec`. Wrong node type
usually produce plausible output, not error.
