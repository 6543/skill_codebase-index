#!/usr/bin/env python3
"""Probe a Python environment for codebase-index. Run it with the target python.

    python runtime_probe.py --describe            interpreter facts, as JSON
    python runtime_probe.py --languages go,sql    import, ABI and parse check

Run as a subprocess: a grammar can crash the interpreter natively, and the
caller must survive that. Each language prints a "start" line, flushed, before
it is touched, so a crash names the language that caused it. The last line is
the JSON result.
"""

import json
import os
import platform
import sys
import sysconfig

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A tiny real program per language and a node type it must produce. Parsing
# without an error is not enough: a grammar for the wrong language can parse
# anything into ERROR-free nonsense, but it will not produce these nodes.
FIXTURES = {
    "bash": (b"f() { echo hi; }\n", "function_definition"),
    "c": (b"int f(void) { return 0; }\n", "function_definition"),
    "c_sharp": (b"class A { void F() {} }\n", "method_declaration"),
    "cpp": (b"namespace n { class A {}; }\n", "namespace_definition"),
    "css": (b".a { color: red; }\n", "rule_set"),
    "fish": (b"function f\n    echo hi\nend\n", "function_definition"),
    "go": (b"package p\nfunc F() {}\n", "function_declaration"),
    "html": (b"<p id=a>x</p>\n", "element"),
    "java": (b"class A { void f() {} }\n", "method_declaration"),
    "javascript": (b"function f() {}\n", "function_declaration"),
    "julia": (b"function f() end\n", "function_definition"),
    "kotlin": (b"fun f() {}\n", "function_declaration"),
    "lua": (b"function f() end\n", "function_declaration"),
    "nix": (b"{ a = 1; }\n", "binding"),
    "php": (b"<?php function f() {}\n", "function_definition"),
    "powershell": (b"function Get-X { 1 }\n", "function_statement"),
    "python": (b"def f():\n    pass\n", "function_definition"),
    "ruby": (b"def f; end\n", "method"),
    "rust": (b"fn f() {}\n", "function_item"),
    "scala": (b"object A { def f = 1 }\n", "function_definition"),
    "sql": (b"CREATE TABLE t (a int);\n", "create_table"),
    "svelte": (b"interface I { x: number }\n", "interface_declaration"),
    "swift": (b"func f() {}\n", "function_declaration"),
    "tsx": (b"const a = <div />;\n", "jsx_self_closing_element"),
    "typescript": (b"interface I { x: number }\n", "interface_declaration"),
    "vue": (b"interface I { x: number }\n", "interface_declaration"),
    "zig": (b"fn f() void {}\n", "function_declaration"),
}


def describe_interpreter():
    libc, libc_version = platform.libc_ver()
    return {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "in_venv": sys.prefix != sys.base_prefix,
        "implementation": sys.implementation.name,
        "version": platform.python_version(),
        "abi": sysconfig.get_config_var("SOABI") or "",
        "machine": platform.machine().lower().replace("amd64", "x86_64"),
        "system": platform.system().lower(),
        "libc": libc or ("musl" if os.path.exists("/lib/ld-musl-x86_64.so.1") else ""),
        "libc_version": libc_version,
    }


def _has_node(node, wanted):
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type == wanted:
            return True
        stack.extend(cur.children)
    return False


def probe_language(key, tree_sitter):
    from languages import BY_KEY  # local: the registry is part of this skill

    spec = BY_KEY[key]
    result = {"language": key, "module": spec.module, "entry": spec.entry}
    try:
        module = __import__(spec.module)
        language = tree_sitter.Language(getattr(module, spec.entry)())
    except Exception as error:  # an import or entry-point failure is the answer
        result.update(ok=False, error=f"{type(error).__name__}: {error}")
        return result
    abi = getattr(language, "abi_version", None) or getattr(language, "version", None)
    low = getattr(tree_sitter, "MIN_COMPATIBLE_LANGUAGE_VERSION", 0)
    high = getattr(tree_sitter, "LANGUAGE_VERSION", 99)
    result["abi"] = abi
    result["file"] = getattr(module, "__file__", "")
    if abi is not None and not low <= abi <= high:
        result.update(
            ok=False, error=f"grammar ABI {abi} outside runtime range {low}..{high}"
        )
        return result
    source, expected = FIXTURES[key]
    tree = tree_sitter.Parser(language).parse(source)
    if tree.root_node.has_error:
        result.update(
            ok=False, error=f"fixture did not parse cleanly: {tree.root_node}"
        )
    elif not _has_node(tree.root_node, expected):
        result.update(ok=False, error=f"fixture produced no {expected} node")
    else:
        result["ok"] = True
    return result


def probe(languages):
    out = {"interpreter": describe_interpreter(), "languages": {}}
    try:
        import tree_sitter
    except ImportError as error:
        out.update(ok=False, error=f"tree_sitter not importable: {error}")
        return out
    from importlib.metadata import PackageNotFoundError, version

    try:
        out["runtime"] = version("tree-sitter")
    except PackageNotFoundError:
        out["runtime"] = getattr(tree_sitter, "__version__", "unknown")
    for key in languages:
        print(json.dumps({"start": key}), flush=True)
        out["languages"][key] = probe_language(key, tree_sitter)
    out["ok"] = all(item["ok"] for item in out["languages"].values())
    return out


def main(argv):
    if "--describe" in argv:
        print(json.dumps(describe_interpreter()))
        return 0
    languages = []
    for arg in argv:
        if arg.startswith("--languages="):
            languages = [k for k in arg.split("=", 1)[1].split(",") if k]
    unknown = sorted(set(languages) - set(FIXTURES))
    if unknown:
        print(json.dumps({"ok": False, "error": f"no probe for: {', '.join(unknown)}"}))
        return 2
    result = probe(languages)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
