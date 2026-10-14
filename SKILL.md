---
name: codebase-index
description: Structural code search and indexing using tree-sitter parsers, covering Go, TypeScript, JavaScript, Vue 3, Svelte, Rust, Python, Java, Kotlin, Swift, Scala, C#, Ruby, PHP, C, C++, Lua, Zig, Julia, Nix, and Bash. Use this INSTEAD of grep whenever exploring, mapping, or understanding an unfamiliar codebase — getting oriented in a new repo, tracing a code path across files, finding what implements an interface, listing a type's methods or fields, reading a declaration together with its doc comment, auditing TODOs, spotting circular imports, or finding the most complex functions. Trigger it whenever the user says things like "how does X work in this repo", "where is Y handled", "map this codebase", "what implements Z", "show me the API surface", "index this repo", "trace the flow from A to B", or asks you to plan a feature against existing code, even if they never say "tree-sitter". Grep matches lines; this matches declarations. Prefer it for any question about code structure rather than a literal string.
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
| Which functions are hardest to follow? | `complexity <root>` |
| Are there circular imports? | `cycles <root>` |

`<root>` is a file or a directory; directories are walked recursively.

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

Test files and generated mocks are excluded by default because they dominate
results and rarely answer the question. Add `--tests` or `--mocks` when they are
what you want.

## Confidence labels

`refs` cannot resolve types, so instead of silently guessing it labels every
call site with how much the answer can be trusted:

| Label | Meaning |
|---|---|
| `EXTRACTED` | the callee is declared in this same file; safe to rely on |
| `INFERRED` | exactly one plausible declaration survived ranking |
| `AMBIGUOUS` | several remain; the best guess is shown with a count of the rest |

Ranking uses two positional signals, not type information: production code is
assumed not to call test fixtures, and a nearer declaration in the directory
tree beats a distant one. A qualified call such as `p.q.Poll` inside a method
also named `Poll` excludes that enclosing method, since it is dispatching on a
different receiver.

Report the label alongside the finding. An `AMBIGUOUS` result presented as
fact is worse than no result.

## Filters

```
--tests          include test files
--mocks          include generated mocks
--lang=<key>     restrict to one language (see `langs`)
--json           machine-readable output, for piping into further analysis
--no-gitignore   index everything, ignoring the repo's .gitignore
--min=<n>        complexity: only report functions scoring at or above n
--max-len=<n>    cycles: longest cycle to search for (default 6)
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
- `impls` compares **method names only**, not signatures, and does not expand
  embedded or inherited members. Treat it as a strong hint to verify, not proof.
- `cycles` resolves import targets to files by path and module name. A cycle
  routed through a re-exporting barrel file or a build-tool path alias can be
  missed, so a reported cycle is solid evidence but an empty result is not proof
  of absence.
- `complexity` counts branch points structurally. It measures how many paths a
  reader must follow, which correlates with but is not the same as difficulty:
  a long flat switch scores high and is easy to read.
- Single-file components (`.vue`, `.svelte`) are parsed by extracting the
  `<script>` block and parsing it as TypeScript. Line numbers are offset back to
  the real file, but template markup is not analysed.
- Kotlin's grammar represents interfaces as `class_declaration`, so `outline`
  does not label them separately. Dart and Elixir are not supported: their
  grammars do not expose declaration names in a usable form.
- Nix has no functions, classes or interfaces in the C sense, so `iface`,
  `methods`, `fields` and `impls` do not apply. `outline`, `decl`, `refs`,
  `consts`, `todos`, `complexity` and `cycles` all work, and for Nix `refs`
  reports attribute references as well as applications, since a purely
  functional language draws no line between using a value and calling it. Dart and Elixir are not supported: their
  grammars do not expose declaration names in a usable form.

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

See `references/languages.md` for per-language coverage and known quirks.
