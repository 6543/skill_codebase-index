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
| `EXTRACTED` | callee declared in the same file | fact |
| `INFERRED` | one candidate survived ranking | "appears to call", worth one check |
| `AMBIGUOUS` | several survived (`refs` only) | a list, never a conclusion |
| `dispatch` | the name is an interface member | "one of these implementations" |
| absent | nothing matched | unknown, not "nothing calls it" |

Last row is the one that misleads. Empty `refs`, or leaf node in `trace`, means
no *syntactic* caller found. Framework routing, reflection, generated clients and
template bindings all produce callers leaving no matching name in source.

## Blind spots and how to close them

| Blind spot | How it shows up | What actually settles it |
|---|---|---|
| Interface dispatch | `dispatch` in the unresolved block | language server: implementations of the method |
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
