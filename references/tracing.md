# Reading a trace without over-claiming

A call graph built from syntax is a map of what *can* be reached, drawn from
names alone. It is not a record of what runs, in what order, or with what data.
This file is about the gap between those two things, because that gap is where
confident wrong answers come from.

## Contents

- [Three passes, in order](#three-passes-in-order)
- [What each label is worth](#what-each-label-is-worth)
- [Blind spots and how to close them](#blind-spots-and-how-to-close-them)
- [Two things a call graph never proves](#two-things-a-call-graph-never-proves)
- [When to stop and use a language server](#when-to-stop-and-use-a-language-server)

## Three passes, in order

Each pass is slower and more certain than the one before it. Start cheap.

1. **Structure.** `map`, `outline`, `trace`. No build required, whole repo in
   seconds. Narrows the question from "this codebase" to "these six functions".
2. **Text.** `decl` for the full body, `rg -n -C 10` for the parts structure
   cannot see: which field a value was written to, what a comment says about
   ordering, what a route string is. Structure tells you where to read; this is
   the reading.
3. **Types.** `gopls`, `rust-analyzer`, `typescript-language-server`, `pyright`,
   `jdtls`, `clangd`. Only these can say which implementation a call reaches.
   Use one whenever the answer depends on that and the stakes justify the setup.

Stop at the first pass that actually answers the question. Most do not reach
pass three.

## What each label is worth

| Label | Basis | Safe to state as |
|---|---|---|
| `EXTRACTED` | callee declared in the same file | fact |
| `INFERRED` | one candidate survived ranking | "appears to call", worth one check |
| `AMBIGUOUS` | several survived (`refs` only) | a list, never a conclusion |
| `dispatch` | the name is an interface member | "one of these implementations" |
| absent | nothing matched | unknown, not "nothing calls it" |

The last row is the one that misleads. An empty `refs` or a leaf node in `trace`
means no *syntactic* caller was found. Framework routing, reflection, generated
clients, and template bindings all produce callers that leave no matching name
anywhere in the source.

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

`effects` is the fast way to enumerate the first five in a repo you do not know.
Run it before presenting a trace of anything concurrent.

## Two things a call graph never proves

**Order.** That A calls B and A calls C says nothing about which runs first, or
whether either has finished. Confirm ordering in control flow, channel
operations, awaits, locks, or queue mutation — never from edge order.

**Identity.** A value that crosses a serialization boundary is not the same
value on the other side; it is whatever the decoder was told to build. Confirm
the encoder, the stored field, the decoder, and the destination type separately.
The same applies across a persistence write and a later read.

## When to stop and use a language server

Reach for real type resolution when the answer turns on which implementation
runs, when a rename or signature change is being planned, or when the trace has
more `dispatch` entries than edges. Say plainly that the structural pass could
not settle it rather than presenting the most plausible candidate as the answer.

## Prior art

The three-pass framing, the confidence vocabulary, and the blind-spot checklist
were adopted from a sibling skill, `trace-codebase-flow`, which builds a flat
record index and traces over it. Its authors in turn credit
[CodeGraph](https://github.com/Jakedismo/codegraph-rust) for tiering analysis by
cost and certainty, and [Graphify](https://github.com/Graphify-Labs/graphify) for
binding every relation to source evidence and treating callbacks as indirect
rather than ordinary calls. The ideas were reimplemented here on top of this
skill's existing command set; no code was copied.
