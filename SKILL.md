---
name: codebase-index
description: Structural code search, call-graph tracing and indexing using tree-sitter, covering Go, TypeScript, JavaScript, Vue, Svelte, Rust, Python, Java, Kotlin, Swift, Scala, C#, Ruby, PHP, C, C++, Lua, Zig, Julia, Nix, and Bash. Use this INSTEAD of grep to explore or understand an unfamiliar codebase: getting oriented in a new repo, following a code path in either direction, finding what implements an interface, listing a type's methods or fields, reading a declaration with its doc comment, auditing TODOs, spotting circular imports, finding async handoffs and serialization boundaries, or ranking functions by complexity. Trigger it on "how does X work in this repo", "where is Y handled", "what happens when Z runs", "what calls this", "map this codebase", "what implements Z", "show me the API surface", "trace the flow from A to B", or a request to plan a feature against existing code, even if tree-sitter is never mentioned. Grep matches lines; this matches declarations and the edges between them.
---

# Codebase Index

Grep answers "which lines contain this text". Most questions about unfamiliar
code are not that. They are "what does this type own", "what does this
interface require", "why was this written this way", "where does control go
next" — questions about structure, which live in the syntax tree rather than in
the text.

`scripts/ts_query.py` parses source with tree-sitter and queries the tree. It
returns whole declarations with their doc comments attached, so a single call
usually replaces several rounds of grep-then-read-with-context.

## Setup

The tool needs `tree_sitter` plus one grammar package per language. Check what
is present before doing anything else:

```bash
python3 scripts/ts_query.py langs
```

Any missing grammar is printed with the exact `pip install` line to fix it.
Missing grammars are not fatal — files in those languages are skipped and a note
goes to stderr. Install only the languages the repo actually contains; `map`
tells you which those are.

## Workflow for exploring an unfamiliar repo

Work outside in. Each step narrows the next.

**1. Map it.** Language mix, size, and where the declarations are concentrated:

```bash
python3 scripts/ts_query.py map /path/to/repo
```

The "densest files" list is the useful part — files with the most declarations
are usually the API surface (`store.go`, `api/index.ts`, `interface.go`) rather
than the busiest logic. Read those first.

**2. Outline the files that matter.** Module header, imports, types with their
members, constants, and free functions, each with its doc comment:

```bash
python3 scripts/ts_query.py outline path/to/file.go
```

**3. Follow the thread.** Pick the command that matches the question:

| Question | Command |
|---|---|
| What does this declaration actually say? | `decl <Name> <root>` |
| Who calls this? | `refs <Name> <root>` |
| What does this contract require? | `iface <Name> <root>` |
| What does this type own? | `methods <Type> <root>` |
| What data does this hold, with tags? | `fields <Type> <root>` |
| What are the enum values / constants? | `consts <root>` |
| What did the authors flag as unfinished? | `todos <root>` |
| What satisfies this interface? | `impls <Iface> <root>` |
| What happens when this runs, several hops out? | `trace <Name> <root>` |
| What reaches this, several hops back? | `trace <Name> <root> --callers` |
| Where does the thread of control get dropped? | `effects <root>` |
| Which functions are hardest to follow? | `complexity <root>` |
| Are there circular imports? | `cycles <root>` |

`<root>` is a file or a directory; directories are walked recursively.

**4. When one hop is not enough, trace.** `refs` answers "who calls this";
`trace` follows the answer onward and prints the result as a tree:

```bash
python3 scripts/ts_query.py trace Reconcile /path/to/repo --depth=2
python3 scripts/ts_query.py trace Reconcile /path/to/repo --callers
```

Every edge carries a confidence label and the `file:line` it was read from.
Edges that syntax alone cannot settle are not drawn: they are listed underneath,
with their candidates, under `unresolved`. That block is often the most
informative part of the output, because it is where the interfaces are.

**5. Check what the graph cannot carry.** A call graph shows that A reaches B.
It does not show that B runs on another goroutine, that the value arrived
through a channel, or that it was a different type before it crossed a JSON
boundary:

```bash
python3 scripts/ts_query.py effects /path/to/repo --kind=boundary
```

Kinds are `spawn` (goroutine, await, defer, thread pool), `channel` (Go sends
and receives), `boundary` (encode/decode pairs), and `callback` (a function
handed to something that will call it later). Read
[references/tracing.md](references/tracing.md) before drawing conclusions from a
trace of anything asynchronous.

## Why these beat the grep equivalent

- `decl` returns the **whole declaration**, bounded by the syntax tree, plus the
  doc comment above it. `grep -C 20` guesses at the boundary and cuts long
  functions in half.
- `methods` disambiguates by **receiver**. Grepping a common method name like
  `Poll` across a repo returns every type's version plus the generated mocks;
  `methods fifo` returns exactly one type's set.
- `iface` lists what a contract requires **with each method's doc comment**.
  This is where design rationale usually lives, and grep has no way to associate
  a comment with the thing it documents.
- `refs` keeps the **full qualifier** (`p.q.Poll` vs `s.scheduler.Poll`) and
  names the enclosing declaration, so a call list doubles as a call graph sketch.
- `todos` attributes each marker to the declaration containing it, which turns a
  flat list into a map of known debt.
- `impls` derives implementors from method sets — there is no textual pattern
  for "this type satisfies that interface" in Go or Rust.
- `trace` keeps following. Reading a call chain by hand costs one `refs` and one
  file read per hop, and the hop that gets skipped is usually the one that
  mattered.
- `effects` finds the handoffs that break a trace: a goroutine, a channel, an
  encoder, a handler passed to a router. None of these has a textual signature
  worth grepping for, and every one of them is a place a confident-looking trace
  quietly stops being true.

Test files, generated mocks, and files whose header marks them as machine-
written are excluded by default because they dominate results and rarely answer
the question. Add `--tests` or `--mocks` when they are what you want. Credential
files (`.env*`, `*.pem`, `*.key`, `id_rsa`, `credentials.json`) are never read at
all, and files over 2 MB are skipped as data rather than source.

## Confidence labels

`refs` and `trace` cannot resolve types, so instead of silently guessing they
label every edge with how much the answer can be trusted:

| Label | Meaning |
|---|---|
| `EXTRACTED` | the callee is declared in this same file; safe to rely on |
| `INFERRED` | exactly one plausible declaration survived ranking |
| `AMBIGUOUS` | several remain; `refs` shows its best guess with a count of the rest |

`trace` is stricter than `refs`: where several candidates survive, it draws no
edge at all and lists the call under `unresolved` with every candidate. A call
naming an interface member is separated out as `dispatch`, because that is not
an ambiguity to be broken by better ranking — the target is chosen at runtime
and is not written down anywhere in the file.

Ranking uses two positional signals, not type information: production code is
assumed not to call test fixtures, and a nearer declaration in the directory
tree beats a distant one. A qualified call such as `p.q.Poll` inside a method
also named `Poll` excludes that enclosing method, since it is dispatching on a
different receiver. A qualified call whose head names an imported package that
resolves outside the repo (`rand.New`) is dropped rather than matched against a
same-named local declaration.

Report the label alongside the finding. An `AMBIGUOUS` result presented as
fact is worse than no result.

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

`.gitignore` is honoured by default, including negation rules and per-directory
files, so generated and vendored code stays out of results without needing a
hardcoded exclusion list. Lockfiles are always skipped.

## Session state

Parse results are cached in memory for the lifetime of a single command, which
keeps repo-wide commands from re-parsing the same files. Nothing is written to
disk. This is a helper you run against the code as it is right now, not an index
to build and keep in sync — so there is no staleness to reason about, and no
cache to invalidate after an edit.

## Reporting findings

When tracing a code path, cite `file:line` for each hop so the user can jump
straight there, and quote the doc comment when it explains a decision — that is
usually the part worth surfacing. Say plainly when something could not be
resolved rather than inferring it; see the limits below.

## Limits worth stating out loud

This is syntactic analysis, not semantic. It does not resolve types, so:

- `refs` matches the trailing identifier of a call. It shows `p.q.Poll` but
  cannot prove `p.q` is a `*fifo`. Same-named methods on different types collide.
- `trace` is built from those same edges, so its reach is bounded by them. A
  reflected call, a call through a generated client, a handler resolved from a
  string route table, and an interface with several implementors all end the
  chain. They end it *visibly*, in the `unresolved` block, rather than silently
   — but an empty `unresolved` block is not proof that nothing was missed.
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
  reader must follow, which correlates with but is not the same as difficulty:
  a long flat switch scores high and is easy to read.
- Single-file components (`.vue`, `.svelte`) are parsed by extracting every
  `<script>` block and parsing each as TypeScript. Line numbers are offset back
  to the real file, but template markup is not analysed — so a handler bound
  only in the template has no visible caller.
- Kotlin's grammar represents interfaces as `class_declaration`, so `outline`
  does not label them separately. Dart and Elixir are not supported: their
  grammars do not expose declaration names in a usable form.
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

Grammars vary, so verify before writing a spec rather than guessing node names:

```bash
python3 scripts/ts_query.py debug-ast sample.ext --depth=3
```

Then add a `LangSpec` to `scripts/languages.py` listing the node types that
carry each concept. Most grammars expose the same field names (`name`,
`parameters`, `body`, `type`, `return_type`), which is why one generic extractor
covers all of them; set `name_hook` only where a grammar deviates, as C and C++
do by burying the function name in the declarator chain.

See `references/languages.md` for per-language coverage and known quirks, and
`references/tracing.md` for how to read a trace without over-claiming.

## Tests

`python3 -m unittest discover -s scripts/tests -t scripts` builds throwaway
repos and asserts the behaviours this document promises: receiver
disambiguation, the confidence labels, refusal to resolve interface dispatch,
credential exclusion, and the Nix and single-file-component quirks. Run it after
changing a `LangSpec` — a wrong node type usually produces plausible output
rather than an error.
