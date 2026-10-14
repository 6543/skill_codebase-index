# codebase-index-tree-sitter-fish

Python binding for the Fish grammar
[ram02z/tree-sitter-fish](https://github.com/ram02z/tree-sitter-fish), which
ships generated C sources but no Python package. Import name is the usual
`tree_sitter_fish`; `tree_sitter_fish.language()` returns the capsule expected
by `tree_sitter.Language`.

`src/` is a byte-for-byte copy of the upstream generated parser at the revision
recorded in `SOURCE.json`; no grammar generation happens at build time. Building
needs only a C compiler and setuptools. The wheel is tagged `cp310-abi3`
because the extension uses only the limited API.

Update: copy `src/parser.c`, `src/scanner.c`, `src/node-types.json`,
`src/tree_sitter/parser.h` and `LICENSE` from a new upstream revision, record the
revision and new SHA-256 digests in `SOURCE.json`, bump the `+g<rev>` label in
`pyproject.toml`, and rebuild with `scripts/build_wheelhouse.py`. The skill's
test suite fails while `SOURCE.json` and the copied files disagree.

License: Unlicense (upstream grammar and this wrapper).
