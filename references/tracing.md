# Reading a trace without over-claiming

Call graph built from syntax is map of what *can* be reached, drawn from names
alone. Not record of what runs, in what order, with what data. This file about
gap between those two. Gap is where confident wrong answers come from.

## Contents

- [Three passes, in order](#three-passes-in-order)
- [What each label is worth](#what-each-label-is-worth)
- [Blind spots and how to close them](#blind-spots-and-how-to-close-them)
- [Two things a call graph never proves](#two-things-a-call-graph-never-proves)
- [When to stop and use a language server](#when-to-stop-and-use-a-language-server)

## Three passes, in order

Each pass slower and more certain than one before. Start cheap.

1. **Structure.** `map`, `outline`, `trace`. No build needed, whole repo in
   seconds. Narrow question from "this codebase" to "these six functions".
2. **Text.** `decl` for full body. `rg -n -C 10` for what structure cannot see:
   which field a value written to, what a comment say about ordering, what a
   route string is. Structure tell you where to read. This is the reading.
3. **Types.** `gopls`, `rust-analyzer`, `typescript-language-server`, `pyright`,
   `jdtls`, `clangd`. Only these can say which implementation a call reach. Use
   one whenever answer depend on that and stakes justify setup.

Stop at first pass that answer the question. Most never reach pass three.

## What each label is worth

| Label | Basis | Safe to state as |
|---|---|---|
| `EXTRACTED` | callee declared in the same file, call bare or on `this`/`self` | fact |
| `INFERRED` | one candidate survived ranking, in another file | "appears to call", worth one check |
| `reference` | names a table, attribute, custom property or link without calling it | "refers to", never "calls" |
| `AMBIGUOUS` | several survived (`refs` only) | a list, never a conclusion |
| `UNRESOLVED` / `receiver` | a method called on a value whose type is not written down (`ctx.Err()`, `y.Save()` after `y := x.Make()`), outside the repo (`ctx context.Context`), or a link to a missing file | "calls *some* `Err`"; the candidates are only names |
| `LOCAL` | a lambda parameter or local binding (Nix), not a declaration | "uses its argument" |
| `dispatch` | the name is an interface member | "one of these implementations" |
| `dynamic` | shell command name built at runtime (`$cmd`, `_item_$x`) | "calls something chosen at runtime"; read the code |
| absent | nothing matched | unknown, not "nothing calls it" |

A qualified call is judged by its receiver before its name. `this`/`self`/the
Go receiver means the enclosing type; an imported repo module or package means
its free functions; a type name means its static members; a local whose type is
written in the function (`s Store`, `s: Store`, `s := &Store{}`, `s = new
Store()`, `Store s`) or a field of a typed receiver (`o.inner.Poll()`) means
that type's methods. A type outside the repo (`context.Context`, `Vec`) draws no
edge. Anything else is a receiver of unknown type: no edge, the candidates are
listed. A method call never resolves to a free function in a language where that
is impossible (Go, Rust, Java, C#, Python, ...).

Last row of the table is the one that misleads. Empty `refs`, or leaf node in `trace`, means
no *syntactic* caller found. Framework routing, reflection, generated clients and
template bindings all produce callers leaving no matching name in source.

## Blind spots and how to close them

| Blind spot | How it shows up | What actually settles it |
|---|---|---|
| Interface dispatch | `dispatch` in the unresolved block | language server: implementations of the method |
| Receiver of unknown type | `receiver` in the unresolved block | the declaration of the variable, or a language server |
| Callback registration | a `callback` edge, or no caller at all | read the registrar; find where it invokes what it stored |
| Async work | `effects --kind=spawn` | read the join, the cancellation path, and who owns the error |
| Channel handoff | `effects --kind=channel` | buffer size, `select`, close, timeout, and who reads next |
| Wire boundary | `effects --kind=boundary` | pair the encoder with the stored field and the decoder's target type |
| Framework routing | a handler with zero callers | route table, annotations, generated code, config |
| Reflection and macros | callee is a string or generated | expanded source, runtime registry, build output |
| Template bindings | a Vue/Svelte handler with zero callers | the template markup, which is not parsed |
| State progression | assignments with no ordering | read the whole controlling function, error branches included |

`effects` is fast way to enumerate first five in unknown repo. Run it before
presenting a trace of anything concurrent.

## Two things a call graph never proves

**Order.** A calls B and A calls C says nothing about which runs first, or
whether either has finished. Confirm ordering in control flow, channel
operations, awaits, locks, or queue mutation. Never from edge order.

**Identity.** A value that crosses a serialization boundary is not the same value
on the other side; it is whatever the decoder was told to build. Confirm the
encoder, the stored field, the decoder, and the destination type separately. The
same applies across a persistence write and a later read.

## When to stop and use a language server

Reach for real type resolution when answer turn on which implementation runs,
when rename or signature change being planned, or when trace have more `dispatch`
entries than edges. Say plainly that structural pass could not settle it. Do not
present most plausible candidate as the answer.

## Prior art

Three-pass framing, confidence vocabulary and blind-spot checklist adopted from
sibling skill `trace-codebase-flow`, which build flat record index and trace over
it. Its authors credit [CodeGraph](https://github.com/Jakedismo/codegraph-rust)
for tiering analysis by cost and certainty, and
[Graphify](https://github.com/Graphify-Labs/graphify) for binding every relation
to source evidence and treating callbacks as indirect rather than ordinary calls.
Ideas reimplemented here on this skill's existing command set. No code copied.
