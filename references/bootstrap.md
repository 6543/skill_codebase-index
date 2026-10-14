# Setup, offline use and packaging

## Contents

- [Routes](#routes)
- [What the bootstrap does](#what-the-bootstrap-does)
- [Flags](#flags)
- [The JSON report](#the-json-report)
- [Platform support](#platform-support)
- [NixOS](#nixos)
- [When something fails](#when-something-fails)
- [Updating pins (maintainers)](#updating-pins-maintainers)
- [Decisions and their reasons](#decisions-and-their-reasons)

## Routes

| Situation | Command |
|---|---|
| Any Linux/macOS/Windows with CPython 3.10+ | `python3 scripts/bootstrap.py` |
| No network at all | `python3 scripts/bootstrap.py --offline` |
| Only some languages | `python3 scripts/bootstrap.py --languages go,nix,fish` |
| NixOS / any Nix on x86_64 Linux | `nix run path/to/skill -- map .` |
| Check the bundle, install nothing | `python3 scripts/bootstrap.py --check --offline` |
| Path of the ready python, for scripts | `python3 scripts/bootstrap.py --print-python` |

All commands work unchanged in fish, bash and zsh. Nothing needs activating.
After a bootstrap, `python3 scripts/ts_query.py` switches into the environment by
itself, so the usual commands need no special interpreter.

## What the bootstrap does

1. Resolves the requested languages (`--languages`, else `--profile`, default
   `common`). Aliases such as `ts`, `c#`, `c++`, `pwsh` are accepted.
2. Asks the chosen interpreter what it is (CPython version, machine, libc) in a
   subprocess, and picks a bundled target if one fits.
3. Takes a lock on the home directory. A second bootstrap waits (up to 5 min) and
   then fails with `"kind": "busy"`; it never installs into the same place at the
   same time.
4. If a current environment exists and serves every requested language with the
   pinned runtime, it is reused. Otherwise a new **generation** is built beside it,
   keeping every language the old one had.
5. Bundled target: verifies each wheel's SHA-256 against `vendor/wheels.json`
   before pip sees it, then installs with `--no-index --require-hashes
   --only-binary=:all:`. No network is touched. Languages outside the bundle (or
   any language on an unbundled platform) come from PyPI at the pinned versions,
   unless `--offline`, which fails up front naming them.
6. Probes the new environment in a subprocess: imports every grammar, checks its
   Language ABI against the runtime's supported range, parses a small real
   snippet and expects a specific node. A native crash fails the probe and names
   the language being loaded; it does not kill the bootstrap.
7. Only then switches `current.json` to the new generation. Keeps the previous
   generation, deletes older ones. On any failure the new generation is removed
   and the working one stays current.

pip runs with no user config (`PIP_CONFIG_FILE=/dev/null`) and no inherited
`PIP_*` variables, so a corporate index or `find-links` setting cannot change
what gets installed.

Home directory: `--home`, else `$CODEBASE_INDEX_HOME`, else
`$XDG_CACHE_HOME/codebase-index` (`~/.cache/codebase-index`), on Windows
`%LOCALAPPDATA%\codebase-index`. Layout:

```
<home>/current.json      python path, languages, runtime, manifest digest
<home>/current           symlink to the generation (POSIX convenience only)
<home>/envs/<id>/        one virtual environment per generation
<home>/.lock
```

## Flags

| Flag | Meaning |
|---|---|
| `--profile common\|all` | `common`: the 18 bundled surfaces. `all`: adds Kotlin, Swift, Scala, Ruby, PHP, Lua, Zig, Julia, Svelte (PyPI) |
| `--languages a,b` | exactly these (plus the runtime); overrides `--profile` |
| `--offline` | never contact a package index; fail naming anything the bundle lacks |
| `--python PATH` | build the environment from this interpreter |
| `--home DIR` | where environments live |
| `--wheelhouse DIR` | alternative wheel directory (same manifest) |
| `--check` | verify bundle integrity and report coverage; installs nothing |
| `--force` | build a new generation even if the current one works |
| `--print-python` | on success print only the python path, else the JSON report |

Exit status 0 means every requested language is operational. A language the
bundle cannot serve under `--offline` is a failure for that request, never a
silent skip.

## The JSON report

```jsonc
{
  "ok": true,
  "action": "installed",            // installed | reused | check
  "method": "bundle",               // bundle | index | bundle+index
  "python": "/home/u/.cache/codebase-index/envs/20261014-154112-3141/bin/python",
  "environment": "...",
  "profile": "common",
  "languages": ["bash", "c", "..."],
  "runtime": "0.25.2",
  "target": "cp313-manylinux_x86_64",  // null: no bundle for this interpreter
  "interpreter": {"version": "3.13.16", "machine": "x86_64", "libc": "glibc", "...": "..."},
  "manifest_sha256": "...",
  "probe": {"languages": {"go": {"ok": true, "abi": 15, "file": "..."}}}
}
```

On failure: `"ok": false`, `"error"` with a specific reason, `"kind"` (`busy`,
`offline`, `usage`, `pip`, `probe`, `venv`, `python`), and for `--offline`
refusals a `"missing"` list.

## Platform support

| Platform | Offline bundle | Route |
|---|---|---|
| Linux x86_64 glibc ≥ 2.17, CPython 3.12 | yes | bundle |
| Linux x86_64 glibc ≥ 2.17, CPython 3.13 | yes | bundle |
| NixOS / Nix, x86_64-linux | yes (same wheels) | flake |
| Linux x86_64, CPython 3.10, 3.11, 3.14 | no | PyPI, pinned |
| Linux aarch64, musl, macOS, Windows | no | PyPI, pinned; Fish needs a C compiler (built from `packaging/fish`) |

"No" means not bundled, not unsupported: the same pinned versions install from
PyPI, then must pass the same probe. Tested: the two bundled CPython targets and
the Nix route. The PyPI rows are expected to work but have not been run. Bundles for more targets will be added only once
the whole common profile has been built and tested there.

Interpreters without `venv`/`ensurepip` (Debian's python3 without
`python3-venv`): the bootstrap stops with that instruction. A pip-less install
path is deliberately not provided: unzipping wheels by hand skips the checks pip
does (`.data` layouts, tags, metadata).

## NixOS

```fish
nix run path/to/skill -- map .
nix build path/to/skill        # result/bin/codebase-index, codebase-index-probe
nix develop path/to/skill      # python with every bundled grammar, ruff, nixfmt
nix flake check path/to/skill  # probe + test suite + nixfmt
```

The flake installs the **same wheel files** as the bundle (each checked against
its SHA-256 in `vendor/wheels.json`) into nixpkgs' CPython 3.13 and patches them
with `autoPatchelfHook`. So both routes run one tested set of binaries, and
building needs nothing beyond nixpkgs itself. nixpkgs is a `git+https` shallow
input, so it fetches without the GitHub API.

Why not nixpkgs' own grammar packages: nixpkgs builds them from different grammar
revisions than the tested pins, and its generic wrapper returns an integer
language pointer that py-tree-sitter has deprecated. Why not PyPI sdists: 13 of
the 24 pinned sdists are incomplete (missing `parser.h`, `scanner.c` or shared
scanner headers) and build modules that fail on import.

Offline Nix: the flake inputs and the CPython closure must already be in the
store. A `flake.lock` alone does not make an uncached machine offline-capable.

Only `x86_64-linux` is exposed. Elsewhere, use `bootstrap.py`.

## When something fails

| Report says | Do |
|---|---|
| `kind: busy` | another bootstrap runs on this home; wait, or use `--home` |
| `kind: offline`, `missing: [...]` | those languages are not in the bundle for this interpreter; drop `--offline`, or `--languages` without them |
| `SHA-256 mismatch` | a bundled wheel is corrupt or replaced; restore the skill files |
| `kind: venv` | install the interpreter's venv support, or pass another `--python` |
| `kind: probe`, `crashed: true` | the named grammar crashed the interpreter; report it with the `stderr` field |
| `kind: pip` | the last pip lines are in `error`; on an unbundled platform this is usually no network or no compiler (Fish) |

The previous environment keeps working in every case above.

## Updating pins (maintainers)

`vendor/inventory.json` is the single reviewed source. Everything else is
generated from it:

```fish
python3 -m venv /tmp/wh; and /tmp/wh/bin/pip install build auditwheel patchelf
env PATH=/tmp/wh/bin:$PATH /tmp/wh/bin/python scripts/build_wheelhouse.py
python3 scripts/bootstrap.py --force --offline
python3 -m unittest discover -s scripts/tests -t scripts
nix flake check
```

`build_wheelhouse.py` downloads exactly one binary wheel per pinned distribution
per target, builds the Fish wheel from `packaging/fish` (policy-checked with
auditwheel), copies each wheel's license files to `vendor/licenses/`, and writes
`vendor/wheels.json` and `requirements/locks/<target>.txt`. It fails if any target
would not cover the whole bundled profile. `bootstrap.py` refuses a manifest built
from a different inventory.

Grammar version numbers are not parser ABI versions: the probe checks the ABI
each Language reports against the runtime's supported range. A grammar bump that
needs a newer runtime fails the probe, not a query.

Fish: `packaging/fish` holds byte-for-byte copies of the generated sources of
`ram02z/tree-sitter-fish` at the revision in `SOURCE.json` (digests checked by the
test suite), wrapped in a limited-API binding (`cp310-abi3`) exposing the usual
`tree_sitter_fish.language()` capsule. The upstream corpus (127 cases) parses
identically through it.

## Decisions and their reasons

- **Runtime 0.25.2, not 0.26.0.** A direct parser diagnostic with 0.26.0 crashed
  with a segmentation fault on Linux CPython 3.12; 0.25.2 completed. 0.26.0 has
  worked elsewhere, so it is not declared broken. Upgrade only after reproducing
  and ruling out that crash. Every bundled grammar (ABI 14 and 15) loads in 0.25.2.
- **Bundle first, index second.** For a bundled target the bundle holds the very
  wheels PyPI would serve, with the same hashes, so contacting the index first
  only adds latency and failure modes.
- **Flat wheel directory.** Grammar wheels are `abi3` and serve both targets;
  storing them once keeps the bundle at about 4 MB. Each target lists its files.
- **Generations, not in-place upgrades.** A virtual environment's scripts embed
  its path, so moving one is unsafe; the new one is built and probed beside the
  old one, and the switch is one atomic file replace.
