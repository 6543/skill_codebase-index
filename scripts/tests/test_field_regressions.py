"""Regressions found by running the skill on seventeen real repositories.

Each fixture is a minimal, independently written reproduction of one defect
from the field report (woodpecker, fd, flask, gson, MediatR, leveldb, lua,
disko, tide, bats-core, zod, graphile/starter, animate.css). The comment on
each class names the defect it pins.

Run: python3 -m unittest discover -s scripts/tests
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ts_query
from test_ts_query import SCRIPTS, Case  # noqa: E402


class JsonCase(Case):
    def run_json(self, command, *args, **flags):
        _, arity = ts_query.COMMANDS[command]
        opts = ts_query.default_opts(json=True, **flags)
        positional = list(args)
        while len(positional) < arity:
            positional.append(self.root)
        items = []
        ts_query.run_command(command, positional[:arity], opts, items.append)
        return items


# --------------------------------------------------------------------------
# D1: a call on a receiver of unknown type is not an edge
# --------------------------------------------------------------------------


class ReceiverTypeTest(Case):
    def test_method_on_a_stdlib_typed_parameter_is_not_linked_to_a_repo_method(self):
        """woodpecker: `ctx.Err()` on a context.Context became `Runtime.Err`."""
        self.write(
            "runtime/runtime.go",
            """
            package runtime

            import "context"

            type Runtime struct{}

            func (r *Runtime) Err() error { return nil }

            func (r *Runtime) Run(ctx context.Context) {
                if ctx.Err() != nil {
                    return
                }
                logger.Error().Err(nil)
            }
            """,
        )
        out = self.run_cmd("trace", "Run")
        self.assertNotIn("-> Runtime.Err", out)

    def test_refs_does_not_label_an_untyped_receiver_inferred(self):
        self.write(
            "a/runtime.go",
            "package a\ntype Runtime struct{}\nfunc (r *Runtime) Err() error { return nil }\n",
        )
        self.write(
            "b/use.go", "package b\nfunc Use(x Thing) { y := x.Make(); y.Err() }\n"
        )
        row = self.run_cmd("refs", "Err").splitlines()[0]
        self.assertNotIn("INFERRED", row)
        self.assertIn("UNRESOLVED", row)

    def test_method_call_cannot_target_a_free_function(self):
        """fd: `search_paths.is_empty()` (a Vec) became `filesystem::is_empty`."""
        self.write(
            "src/filesystem.rs", "pub fn is_empty(entry: &DirEntry) -> bool { true }\n"
        )
        self.write(
            "src/main.rs",
            "fn run(opts: &Opts) {\n    let paths = opts.search_paths();\n    if paths.is_empty() {}\n}\n",
        )
        out = self.run_cmd("trace", "run")
        self.assertNotIn("-> is_empty", out)

    def test_attribute_argument_is_not_a_callback_to_an_unrelated_function(self):
        """flask: `self.app` passed as an argument became a callback to cli.app."""
        self.write("pkg/cli.py", "def app():\n    pass\n")
        self.write(
            "pkg/ctx.py",
            """
            class AppContext:
                def push(self, ctx):
                    signal.send(self.app, wrapper=self.app.ensure_sync)
                    handle(ctx.request.blueprints)
            """,
        )
        self.write(
            "pkg/wrappers.py",
            "class Request:\n    def blueprints(self):\n        pass\n",
        )
        out = self.run_cmd("trace", "push")
        self.assertNotIn("-> app", out)
        self.assertNotIn("Request.blueprints", out)

    def test_a_typed_local_selects_the_method_of_its_type(self):
        self.write(
            "store.go",
            """
            package store
            type Store struct{}
            type Cache struct{}
            func (s *Store) Save() {}
            func (c *Cache) Save() {}
            func Use() {
                s := &Store{}
                s.Save()
            }
            """,
        )
        out = self.run_cmd("trace", "Use")
        self.assertIn("-> Store.Save", out)
        self.assertNotIn("Cache.Save", out)

    def test_an_annotated_python_parameter_selects_the_method_of_its_type(self):
        self.write("store.py", "class Store:\n    def save(self):\n        pass\n")
        self.write("cache.py", "class Cache:\n    def save(self):\n        pass\n")
        self.write("use.py", "def use(s: Store):\n    s.save()\n")
        out = self.run_cmd("trace", "use")
        self.assertIn("-> Store.save", out)

    def test_a_self_call_still_resolves_in_its_own_class(self):
        self.write(
            "svc.py",
            "class Svc:\n    def run(self):\n        self.helper()\n\n    def helper(self):\n        pass\n",
        )
        out = self.run_cmd("trace", "run")
        self.assertIn("-> Svc.helper", out)
        self.assertIn("EXTRACTED", out)

    def test_an_unknown_receiver_is_listed_as_unresolved_with_candidates(self):
        self.write(
            "a.go",
            "package a\ntype T struct{}\nfunc (t *T) Poll() {}\nfunc Use() { mystery().Poll() }\n",
        )
        out = self.run_cmd("trace", "Use")
        self.assertIn("unresolved", out)
        self.assertIn("receiver", out)


# --------------------------------------------------------------------------
# D2: a SQL statement the grammar rejects still yields its declaration
# --------------------------------------------------------------------------

PG_DUMP = """
CREATE FUNCTION app_private.tg__timestamps() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
begin
  NEW.created_at = (case when TG_OP = 'INSERT' then NOW() else OLD.created_at end);
  return NEW;
end;
$$;

CREATE FUNCTION app_private.assert_valid_password(new_password text) RETURNS void
    LANGUAGE plpgsql
    AS $$
begin
  if length(new_password) < 8 then
    raise exception 'Password is too weak' using errcode = 'WEAKP';
  end if;
end;
$$;

CREATE TABLE app_public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    username public.citext NOT NULL,
    CONSTRAINT users_username_check CHECK ((username OPERATOR(public.~) '^[a-z]+$'::public.citext))
);

CREATE TRIGGER _100_timestamps BEFORE INSERT OR UPDATE ON app_public.users
    FOR EACH ROW EXECUTE FUNCTION app_private.tg__timestamps();
"""


class SqlRecoveryTest(Case):
    def setUp(self):
        super().setUp()
        self.path = self.write("schema.sql", PG_DUMP)

    def test_pg_dump_functions_and_tables_survive_unsupported_syntax(self):
        out = self.run_cmd("outline", self.path)
        self.assertIn("tg__timestamps", out)
        self.assertIn("assert_valid_password", out)
        self.assertIn("app_public.users", out)

    def test_decl_prints_the_original_text_not_the_masked_text(self):
        out = self.run_cmd("decl", "tg__timestamps")
        self.assertIn("SET search_path TO", out)
        self.assertIn("NEW.created_at", out)

    def test_trigger_reaches_its_recovered_function(self):
        out = self.run_cmd("trace", "_100_timestamps")
        self.assertIn("tg__timestamps", out)


# --------------------------------------------------------------------------
# D3: Nix names follow lexical scope
# --------------------------------------------------------------------------


class NixScopeTest(Case):
    def test_select_on_a_lambda_parameter_is_not_resolved_to_a_later_binding(self):
        """disko: `lib.mkOption` resolved EXTRACTED to `lib = lib // {...}`."""
        self.write(
            "lib/default.nix",
            """
            { lib, ... }:
            {
              mkMountOption = { config, ... }: lib.mkOption { internal = true; };
              spoof = {
                lib = lib // { mkOption = option: option; };
              };
            }
            """,
        )
        out = self.run_cmd("trace", "mkMountOption")
        # Two declarations on one line (`mkMountOption` and `internal`) must
        # not collide in the graph.
        self.assertIn("mkMountOption  (", out)
        self.assertNotIn("-> lib ", out)
        self.assertNotIn("-> mkOption", out)

    def test_lambda_argument_is_not_resolved_to_another_files_binding(self):
        self.write("module.nix", "{ diskoLib, ... }:\n{ x = diskoLib.toplevel; }\n")
        self.write(
            "doc.nix",
            "let\n  diskoLib = import ./lib.nix;\nin\n{ y = diskoLib.toplevel; }\n",
        )
        self.write("lib.nix", "{ toplevel = 1; }\n")
        out = self.run_cmd("refs", "diskoLib")
        module_lines = [
            ln
            for ln in out.splitlines()
            if ln.startswith(os.path.join(self.root, "module.nix"))
        ]
        for line in module_lines:
            self.assertNotIn("doc.nix", line)

    def test_a_let_binding_is_the_target_of_a_bare_reference(self):
        self.write(
            "a.nix",
            "let\n  helper = x: x;\nin\n{\n  value = helper 1;\n  other = { helper = 2; };\n}\n",
        )
        out = self.run_cmd("trace", "value")
        self.assertIn("-> helper  (", out)
        self.assertIn(":2)", out)  # the let binding, not the attribute on line 6


# --------------------------------------------------------------------------
# D4: Nix path references
# --------------------------------------------------------------------------


class NixPathTest(Case):
    def setUp(self):
        super().setUp()
        self.write(
            "flake/default.nix",
            """
            { pkgs, name, modulesPath, ... }:
            {
              imports = [ ./mod.nix ./sub ];
              pkg = pkgs.callPackage ./pkg.nix { };
              lib = import ./lib { };
              computed = import "${modulesPath}/x.nix";
              data = ./files/${name}.nix;
            }
            """,
        )
        self.write("flake/mod.nix", "{ ... }: { back = import ./default.nix; }\n")
        self.write("flake/sub/default.nix", "{ }\n")
        self.write("flake/pkg.nix", "{ }\n")
        self.write("flake/lib/default.nix", "{ }\n")

    def test_imports_lists_callpackage_and_import_are_cycle_edges(self):
        out = self.run_cmd("cycles")
        self.assertIn("default.nix -> flake/mod.nix", out.replace(os.sep, "/"))

    def test_refs_to_a_file_lists_who_imports_it_including_directory_imports(self):
        out = self.run_cmd("refs", "sub/default.nix")
        self.assertIn("./sub", out)
        self.assertIn("imports", out)

    def test_callpackage_is_labelled(self):
        self.assertIn("callPackage", self.run_cmd("refs", "pkg.nix"))

    def test_computed_paths_are_reported_as_unresolved(self):
        out = self.run_cmd("cycles")
        self.assertIn("could not be resolved", out)


# --------------------------------------------------------------------------
# D5: Python decl keeps decorators and prints the docstring once
# --------------------------------------------------------------------------


class PythonDeclTest(Case):
    def test_decorator_kept_and_docstring_printed_once(self):
        self.write(
            "scaffold.py",
            '''
            class Scaffold:
                @setupmethod
                def route(self, rule):
                    """Decorate a view function."""
                    return rule
            ''',
        )
        out = self.run_cmd("decl", "route")
        self.assertIn("@setupmethod", out)
        self.assertEqual(out.count("Decorate a view function"), 1)


# --------------------------------------------------------------------------
# D6: import cycles
# --------------------------------------------------------------------------


class CyclesTest(Case):
    def test_go_stdlib_import_is_not_matched_to_a_repo_directory(self):
        self.write("go.mod", "module example.com/m\n\ngo 1.22\n")
        self.write(
            "cli/exec/exec.go",
            'package exec\nimport "example.com/m/pipeline/local"\nfunc Run() { local.Clone() }\n',
        )
        self.write(
            "pipeline/local/clone.go",
            'package local\nimport (\n\t"os/exec"\n\t"golang.org/x/oauth2/bitbucket"\n)\nfunc Clone() { exec.Command("git") }\n',
        )
        self.write("server/bitbucket/bitbucket.go", "package bitbucket\nfunc B() {}\n")
        out = self.run_cmd("cycles")
        self.assertIn("no import cycles", out)

    def test_go_module_imports_resolve_to_their_package(self):
        self.write("go.mod", "module example.com/m\n")
        self.write(
            "a/a.go", 'package a\nimport "example.com/m/b"\nfunc A() { b.B() }\n'
        )
        self.write(
            "b/b.go", 'package b\nimport "example.com/m/a"\nfunc B() { a.A() }\n'
        )
        self.assertIn("1 import cycle", self.run_cmd("cycles"))

    def test_python_type_checking_imports_are_not_runtime_cycles(self):
        self.write("pkg/__init__.py", "")
        self.write("pkg/app.py", "from .cli import main\n")
        self.write(
            "pkg/cli.py",
            "import typing as t\nif t.TYPE_CHECKING:\n    from .app import Flask\n\ndef main():\n    from .app import later\n",
        )
        out = self.run_cmd("cycles")
        self.assertIn("no import cycles", out)
        self.assertIn("TYPE_CHECKING", out)

    def test_rust_crate_paths_resolve(self):
        self.write("src/main.rs", "mod a;\nmod b;\nfn main() {}\n")
        self.write("src/a.rs", "use crate::b::B;\npub struct A;\n")
        self.write("src/b.rs", "use crate::a::A;\npub struct B;\n")
        self.assertIn("1 import cycle(s)", self.run_cmd("cycles"))

    def test_commonjs_require_is_an_import(self):
        self.write("lib/a.js", "const b = require('./b');\nmodule.exports = {};\n")
        self.write("lib/b.js", "const a = require('./a');\nmodule.exports = {};\n")
        self.assertIn("1 import cycle(s)", self.run_cmd("cycles"))

    def test_c_quoted_includes_resolve(self):
        self.write("src/a.h", '#include "b.h"\nint a(void);\n')
        self.write("src/b.h", '#include "a.h"\nint b(void);\n')
        self.assertIn("1 import cycle(s)", self.run_cmd("cycles"))

    def resolved(self, importer, target, spec_key):
        opts = ts_query.default_opts()
        by_module, by_stem = ts_query._module_index(self.root, opts)
        hit = ts_query.resolve_import(
            target,
            os.path.join(self.root, importer),
            self.root,
            by_module,
            by_stem,
            ts_query.BY_KEY[spec_key],
        )
        return os.path.relpath(hit, self.root).replace(os.sep, "/") if hit else None

    def test_external_package_is_not_matched_to_a_local_file_of_that_name(self):
        """flask: `werkzeug.wrappers` became src/flask/wrappers.py; vitesse:
        npm `pinia` became src/modules/pinia.ts."""
        self.write("src/flask/wrappers.py", "x = 1\n")
        self.write("src/flask/helpers.py", "y = 1\n")
        self.write("src/modules/pinia.ts", "export const x = 1\n")
        self.write("src/main.ts", "export const m = 1\n")
        self.assertIsNone(
            self.resolved("src/flask/helpers.py", "werkzeug/wrappers", "python")
        )
        self.assertEqual(
            self.resolved("src/flask/helpers.py", "flask/wrappers", "python"),
            "src/flask/wrappers.py",
        )
        self.assertIsNone(self.resolved("src/main.ts", "pinia", "typescript"))

    def test_esm_js_suffix_and_root_alias_resolve_to_typescript_sources(self):
        self.write("src/core/index.ts", "export const c = 1\n")
        self.write("src/types.ts", "export type T = 1\n")
        self.write("src/classic/schemas.ts", "export const s = 1\n")
        self.assertEqual(
            self.resolved("src/classic/schemas.ts", "../core/index.js", "typescript"),
            "src/core/index.ts",
        )
        self.assertEqual(
            self.resolved("src/classic/schemas.ts", "~/types", "typescript"),
            "src/types.ts",
        )

    def test_language_with_no_resolved_edges_is_named(self):
        self.write("A.cs", "using Foo.Bar;\nclass A {}\n")
        self.write("B.cs", "using Foo.Baz;\nclass B {}\n")
        out = self.run_cmd("cycles")
        self.assertIn("c_sharp", out)
        self.assertIn("no evidence", out)


# --------------------------------------------------------------------------
# D7 / D8: implementors from declared supertypes; inherited interfaces
# --------------------------------------------------------------------------


class ImplsDeclaredTest(Case):
    def test_java_anonymous_class_members_do_not_leak_into_the_enclosing_class(self):
        self.write(
            "TypeAdapterFactory.java",
            "public interface TypeAdapterFactory { <T> Object create(Object gson); }\n",
        )
        self.write(
            "ArrayTypeAdapter.java",
            """
            public class ArrayTypeAdapter {
              public static final TypeAdapterFactory FACTORY = new TypeAdapterFactory() {
                public <T> Object create(Object gson) { return null; }
              };
              public Object read() { return null; }
            }
            """,
        )
        self.write(
            "Builder.java",
            "public class Builder { public Object create() { return null; } }\n",
        )
        self.write(
            "Real.java",
            "public class Real implements TypeAdapterFactory { public <T> Object create(Object g) { return null; } }\n",
        )
        self.write("Sub.java", "public class Sub extends Real {}\n")
        methods = self.run_cmd("methods", "ArrayTypeAdapter")
        self.assertNotIn("create", methods)
        out = self.run_cmd("impls", "TypeAdapterFactory")
        self.assertIn("Real", out)
        self.assertIn("Sub", out)  # through the declared hierarchy
        self.assertNotIn("Builder", out)
        self.assertNotIn("ArrayTypeAdapter", out)

    def test_csharp_generic_base_list_is_authoritative(self):
        self.write(
            "IRequestHandler.cs",
            "public interface IRequestHandler<TRequest, TResponse> { TResponse Handle(TRequest r); }\n",
        )
        self.write(
            "Real.cs",
            "public class Real : Base, IRequestHandler<Ping, Pong> { public Pong Handle(Ping p) { return null; } }\n",
        )
        self.write(
            "NotHandler.cs", "public class NotHandler { public void Handle() {} }\n"
        )
        out = self.run_cmd("impls", "IRequestHandler")
        self.assertIn("Real", out)
        self.assertNotIn("NotHandler", out)

    def test_iface_names_inherited_interfaces_and_expands_them(self):
        self.write(
            "IMediator.cs", "public interface IMediator : ISender, IPublisher { }\n"
        )
        self.write("ISender.cs", "public interface ISender { Task Send(object r); }\n")
        out = self.run_cmd("iface", "IMediator")
        self.assertIn("ISender", out)
        self.assertIn("IPublisher", out)
        self.assertIn("Send", out)


# --------------------------------------------------------------------------
# D9 / D11: grammar gaps recovered by masking
# --------------------------------------------------------------------------


class GrammarGapTest(Case):
    def assert_clean(self, relative, body):
        path = self.write(relative, body)
        ts_query.parse(path)
        self.assertNotIn(path, ts_query._PARSE_ERRORS)
        return path

    def test_typescript_variance_annotations(self):
        self.assert_clean(
            "a.ts",
            "export interface Check<in T = never> { x: T }\nexport interface Out<out O, in out I> { o: O }\n",
        )
        out = self.run_cmd("decl", "Check")
        self.assertIn("export interface Check<in T = never>", out)

    def test_c_api_macro_before_a_typedef_return_type(self):
        self.assert_clean(
            "lapi.c",
            "LUA_API lua_CFunction lua_atpanic (lua_State *L, lua_CFunction panicf) {\n  return panicf;\n}\n",
        )
        # The blanked macro is still part of the declaration as written.
        self.assertIn(
            "LUA_API lua_CFunction lua_atpanic", self.run_cmd("decl", "lua_atpanic")
        )

    def test_css_fractional_keyframe_selector(self):
        self.assert_clean(
            "a.css",
            "@keyframes headShake {\n  0% { transform: none; }\n  6.5% { transform: translateX(-6px); }\n  100% { transform: none; }\n}\n",
        )

    def test_bash_pattern_substitution_with_ampersand_and_angle(self):
        self.assert_clean(
            "x.bash",
            'esc() {\n  local output=${1//&/\\&amp;}\n  output=${output//</\\&lt;}\n  printf "%s" "$output"\n}\n',
        )

    def test_cpp_thread_safety_annotations(self):
        self.assert_clean(
            "db_impl.h",
            "class DBImpl {\n public:\n  void CompactMemTable() EXCLUSIVE_LOCKS_REQUIRED(mutex_);\n  int* const mem GUARDED_BY(mu);\n};\n",
        )

    def test_cpp_export_macro_class_is_a_class(self):
        """leveldb: `class LEVELDB_EXPORT DB {` vanished."""
        self.write(
            "include/db.h",
            """
            namespace leveldb {
            class LEVELDB_EXPORT DB {
             public:
              virtual ~DB();
              virtual Status Get(int key) = 0;
            };
            LEVELDB_EXPORT Status DestroyDB(const std::string& name);
            }
            """,
        )
        outline = self.run_cmd("outline", os.path.join(self.root, "include/db.h"))
        self.assertIn("class_specifier DB", outline)
        self.assertNotIn("-> LEVELDB_EXPORT", outline)
        self.assertIn("Get", self.run_cmd("iface", "DB"))


# --------------------------------------------------------------------------
# D10: Fish command after a continued variable override
# --------------------------------------------------------------------------


class FishOverrideTest(Case):
    def test_command_after_continued_overrides_is_found(self):
        self.write("functions/item.fish", "function _tide_print_item\nend\n")
        self.write(
            "functions/status.fish",
            "function _tide_item_status\n"
            "    contains character $x || tide_bg=$failure \\\n"
            "        tide_color=$color _tide_print_item status 1\n"
            "end\n",
        )
        out = self.run_cmd("refs", "_tide_print_item")
        self.assertIn("status.fish:3", out)


# --------------------------------------------------------------------------
# D12: C++ out-of-class method definitions belong to their class
# --------------------------------------------------------------------------


class CppMethodsTest(Case):
    def setUp(self):
        super().setUp()
        self.write(
            "db.h",
            "class DB {\n public:\n  virtual Status Get(int k) = 0;\n  virtual Status Put(int k) = 0;\n};\n",
        )
        self.write(
            "db_impl.h",
            '#include "db.h"\nclass DBImpl : public DB {\n public:\n  Status Get(int k) override;\n  Status Put(int k) override;\n  void Helper();\n};\n',
        )
        self.write(
            "db_impl.cc",
            '#include "db_impl.h"\nStatus DBImpl::Get(int k) { return s; }\nStatus DBImpl::Put(int k) { Helper(); return s; }\nDBImpl::~DBImpl() {}\n',
        )

    def test_methods_include_out_of_class_definitions(self):
        out = self.run_cmd("methods", "DBImpl")
        for name in ("Get", "Put", "~DBImpl", "Helper"):
            self.assertIn(name, out)
        self.assertIn("out of class", out)

    def test_impls_finds_the_derived_class(self):
        self.assertIn("DBImpl", self.run_cmd("impls", "DB"))


# --------------------------------------------------------------------------
# D13: polish
# --------------------------------------------------------------------------


class PolishTest(Case):
    def test_rust_test_module_imports_are_not_file_imports(self):
        path = self.write(
            "lib.rs",
            "use std::fs;\n\nfn f() {}\n\n#[cfg(test)]\nmod tests {\n    use std::path::Path;\n}\n",
        )
        out = self.run_cmd("outline", path)
        self.assertIn("imports (1)", out)
        self.assertNotIn("std::path::Path", out)

    def test_long_constant_values_are_truncated(self):
        # A real file around it, so it is not taken for a minified one.
        body = "".join(f"fn f{i}() {{ let x = {i}; }}\n" for i in range(200))
        self.write("main.rs", body + 'const COLORS: &str = "' + "x=0;" * 1500 + '";\n')
        out = self.run_cmd("consts")
        self.assertLess(len(out), 600)
        self.assertIn("chars", out)

    def test_ambient_constant_in_declare_global_is_a_declaration(self):
        self.write(
            "auto-imports.d.ts",
            "declare global {\n  const useCounter: typeof import('@vueuse/core').useCounter\n}\nexport {}\n",
        )
        self.assertNotIn("no declaration", self.run_cmd("decl", "useCounter"))


# --------------------------------------------------------------------------
# D14: what the default scan includes
# --------------------------------------------------------------------------


class ExclusionDirTest(Case):
    def files(self, **flags):
        opts = ts_query.default_opts(**flags)
        return {
            os.path.relpath(p, self.root).replace(os.sep, "/")
            for p in ts_query.iter_files(self.root, opts)
        }

    def setUp(self):
        super().setUp()
        self.write("src/main.rs", "fn main() {}\n")
        self.write("tests/tests.rs", "fn t() {}\n")
        self.write("gson/src/test/java/TestTypes.java", "class TestTypes {}\n")
        self.write("test/app.use.js", "function f() {}\n")
        self.write(".yarn/plugins/plugin.cjs", "function g() {}\n")
        self.write("dist2/app.min.js", "function h() {}\n")
        self.write("lib/bundle.js", "var a=1;" * 200 + "\n")

    def test_test_directories_are_excluded_by_default(self):
        found = self.files()
        self.assertIn("src/main.rs", found)
        for path in (
            "tests/tests.rs",
            "gson/src/test/java/TestTypes.java",
            "test/app.use.js",
        ):
            self.assertNotIn(path, found)
        self.assertIn("tests/tests.rs", self.files(tests=True))

    def test_vendored_and_minified_files_are_excluded_until_asked_for(self):
        found = self.files()
        for path in (".yarn/plugins/plugin.cjs", "dist2/app.min.js", "lib/bundle.js"):
            self.assertNotIn(path, found)
        self.assertIn(".yarn/plugins/plugin.cjs", self.files(vendored=True))

    def test_map_reports_what_was_excluded(self):
        out = self.run_cmd("map")
        self.assertIn("excluded", out)
        self.assertIn("test", out)
        self.assertIn("vendored", out)

    def test_cli_accepts_vendored_flag(self):
        opts, _ = ts_query.parse_args(["--vendored", "map", "."])
        self.assertTrue(opts["vendored"])


# --------------------------------------------------------------------------
# D15: --json is structured
# --------------------------------------------------------------------------


class StructuredJsonTest(JsonCase):
    def setUp(self):
        super().setUp()
        self.write(
            "flow.go",
            "package flow\n\n// Start begins.\nfunc Start() { run() }\nfunc run() {}\n\ntype Q interface { Poll() }\ntype F struct{}\nfunc (f *F) Poll() {}\n",
        )

    def test_refs(self):
        rows = self.run_json("refs", "run")
        self.assertIsInstance(rows[0], dict)
        self.assertEqual(rows[0]["label"], "EXTRACTED")
        self.assertTrue(rows[0]["target"]["file"].endswith("flow.go"))
        self.assertEqual(rows[0]["enclosing"], "function_declaration Start")

    def test_trace(self):
        result = self.run_json("trace", "Start")[0]
        self.assertEqual(result["edges"][0]["to"]["name"], "run")

    def test_decl(self):
        row = self.run_json("decl", "Start")[0]
        self.assertIn("func Start()", row["source"])
        self.assertEqual(row["doc"], ["// Start begins."])

    def test_outline(self):
        result = self.run_json("outline", os.path.join(self.root, "flow.go"))[0]
        names = [item["name"] for item in result["funcs"]]
        self.assertIn("Start", names)

    def test_impls(self):
        result = self.run_json("impls", "Q")[0]
        self.assertEqual([i["name"] for i in result["implementors"]], ["F"])

    def test_cli_prints_valid_json_objects(self):
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(SCRIPTS, "ts_query.py"),
                "refs",
                "run",
                self.root,
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIsInstance(json.loads(proc.stdout)[0], dict)


# --------------------------------------------------------------------------
# D16: consistent paths and a complete legend
# --------------------------------------------------------------------------


class PathConsistencyTest(Case):
    def test_paths_keep_the_form_the_root_was_given_in(self):
        self.write("pkg/a.go", "package pkg\nfunc A() { B() }\n")
        self.write("pkg/b.go", "package pkg\nfunc B() {}\nfunc Broken( {\n")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "ts_query.py"), "refs", "B", "pkg"],
            capture_output=True,
            text=True,
            cwd=self.root,
            check=True,
        )
        self.assertIn("pkg/a.go:2", proc.stdout.replace(os.sep, "/"))
        self.assertNotIn(self.root, proc.stdout)
        self.assertNotIn(self.root, proc.stderr)
        self.assertIn("pkg/b.go", proc.stderr.replace(os.sep, "/"))

    def test_legend_names_every_label_it_can_print(self):
        self.write("index.html", '<link rel="stylesheet" href="css/missing.css">\n')
        out = self.run_cmd("refs", "css/missing.css")
        self.assertIn("UNRESOLVED", out)
        self.assertIn("UNRESOLVED =", out)


if __name__ == "__main__":
    unittest.main()
