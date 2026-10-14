"""Behavioural tests for ts_query.

Each test asserts something the tool could plausibly get wrong: a resolution
decision, an exclusion rule, a grammar quirk, or a claim the SKILL.md makes to
the reader. Tests that would only restate a declaration are deliberately absent.

Run: python3 -m unittest discover -s scripts/tests
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import ignores
import ts_query
from languages import ALL


class Case(unittest.TestCase):
    """Writes a throwaway repo, then runs commands against it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        ts_query._PARSE_CACHE.clear()
        ts_query._PARSE_ERRORS.clear()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, relative, body):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf8") as handle:
            handle.write(textwrap.dedent(body).lstrip("\n"))
        return path

    def run_cmd(self, command, *args, **flags):
        _, arity = ts_query.COMMANDS[command]
        opts = ts_query.default_opts(**flags)
        positional = list(args)
        while len(positional) < arity:
            positional.append(self.root)
        lines = []
        ts_query.run_command(command, positional[:arity], opts, lines.append)
        return "\n".join(str(line) for line in lines)


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


class RegistryTest(unittest.TestCase):
    def test_every_spec_names_a_loadable_grammar(self):
        """A typo in a LangSpec's module or entry point silently skips the language.

        Checked for every language whose grammar distribution is installed; the
        others (outside the bundled profile, say) are skipped, not passed.
        """
        from importlib.metadata import PackageNotFoundError, distribution

        import runtime_manifest

        skill = os.path.dirname(SCRIPTS)
        pins = runtime_manifest.load_manifest(skill)["languages"]
        for spec in ALL:
            with self.subTest(language=spec.key):
                try:
                    distribution(pins[spec.key]["distribution"])
                except PackageNotFoundError:
                    self.skipTest(f"{pins[spec.key]['distribution']} not installed")
                self.assertIsNotNone(ts_query.get_parser(spec), spec.module)

    def test_no_extension_is_claimed_by_two_languages(self):
        seen = {}
        for spec in ALL:
            for ext in spec.exts:
                self.assertNotIn(ext, seen, f"{ext} claimed by {seen.get(ext)}")
                seen[ext] = spec.key


# --------------------------------------------------------------------------
# structure: what grep cannot do
# --------------------------------------------------------------------------


class StructureTest(Case):
    def test_outline_attaches_doc_comments_to_their_declaration(self):
        path = self.write(
            "queue.go",
            """
            package queue

            // Queue hands tasks to exactly one worker.
            type Queue interface {
                // Poll blocks until a task is available.
                Poll(ctx Context) (*Task, error)
            }
            """,
        )
        out = self.run_cmd("outline", path)
        self.assertIn("hands tasks to exactly one worker", out)
        self.assertIn("Poll blocks until a task is available", out)

    def test_methods_disambiguates_by_receiver(self):
        """The whole point over grep: two types, one method name."""
        self.write(
            "fifo.go",
            """
            package queue

            type fifo struct{}
            type lifo struct{}

            func (q *fifo) Poll() {}
            func (q *lifo) Poll() {}
            """,
        )
        out = self.run_cmd("methods", "fifo")
        self.assertIn("(q *fifo) Poll", out)
        self.assertNotIn("lifo", out)

    def test_fields_keeps_struct_tags(self):
        self.write(
            "task.go",
            """
            package queue

            type Task struct {
                ID   int64  `json:"id" db:"task_id"`
                Name string `json:"name"`
            }
            """,
        )
        out = self.run_cmd("fields", "Task")
        self.assertIn('db:"task_id"', out)

    def test_impls_derives_implementors_from_the_method_set(self):
        self.write(
            "queue.go",
            """
            package queue

            type Queue interface {
                Poll() error
                Push() error
            }

            type fifo struct{}

            func (q *fifo) Poll() error { return nil }
            func (q *fifo) Push() error { return nil }
            func (q *fifo) Len() int    { return 0 }

            type partial struct{}

            func (p *partial) Poll() error { return nil }
            """,
        )
        out = self.run_cmd("impls", "Queue")
        self.assertIn("fifo", out)
        self.assertNotIn("partial", out)
        self.assertIn("Len", out)  # extra methods are reported

    def test_impls_states_that_it_matched_on_names_only(self):
        """A name-based cover presented as proof would be actively misleading."""
        self.write(
            "queue.go",
            """
            package queue
            type Queue interface { Poll() error }
            type fifo struct{}
            func (q *fifo) Poll() error { return nil }
            """,
        )
        self.assertIn("signatures", self.run_cmd("impls", "Queue"))

    def test_todos_attribute_each_marker_to_its_enclosing_declaration(self):
        self.write(
            "worker.go",
            """
            package worker

            func Run() {
                // TODO: retry on transient failure
                do()
            }
            """,
        )
        out = self.run_cmd("todos")
        self.assertIn("retry on transient failure", out)
        self.assertIn("Run", out)

    def test_python_docstring_is_read_as_the_doc_comment(self):
        path = self.write(
            "svc.py",
            '''
            class Service:
                def poll(self):
                    """Return the next task, or None."""
                    return None
            ''',
        )
        self.assertIn("Return the next task", self.run_cmd("outline", path))

    def test_rust_impl_block_methods_belong_to_their_type(self):
        self.write(
            "queue.rs",
            """
            struct Fifo {}

            impl Fifo {
                /// Poll removes the head.
                fn poll(&self) {}
            }
            """,
        )
        out = self.run_cmd("methods", "Fifo")
        self.assertIn("poll", out)
        self.assertIn("Poll removes the head", out)


# --------------------------------------------------------------------------
# reference resolution and its confidence labels
# --------------------------------------------------------------------------


class RefsTest(Case):
    def test_same_file_callee_is_labelled_extracted(self):
        self.write(
            "flow.go",
            """
            package flow

            func Start() { run() }
            func run()   {}
            """,
        )
        self.assertIn("EXTRACTED", self.run_cmd("refs", "run"))

    def test_two_plausible_declarations_are_labelled_ambiguous(self):
        # Methods: a Go method call cannot reach a free function.
        self.write("a/one.go", "package a\ntype A struct{}\nfunc (a *A) Poll() {}\n")
        self.write("b/two.go", "package b\ntype B struct{}\nfunc (b *B) Poll() {}\n")
        self.write("c/call.go", "package c\nfunc Use() { x.Poll() }\n")
        out = self.run_cmd("refs", "Poll")
        self.assertIn("AMBIGUOUS", out)
        self.assertIn("more", out)

    def test_a_wrapper_method_does_not_resolve_to_itself(self):
        """`p.q.Poll()` inside Poll dispatches on another receiver, not on self."""
        self.write(
            "wrap.go",
            """
            package queue

            type outer struct{ inner inner }
            type inner struct{}

            func (o *outer) Poll() { o.inner.Poll() }
            func (i *inner) Poll() {}
            """,
        )
        out = self.run_cmd("refs", "Poll")
        self.assertIn("-> inner.Poll", out)


# --------------------------------------------------------------------------
# call graph
# --------------------------------------------------------------------------


class TraceTest(Case):
    def test_follows_callees_across_files_to_the_requested_depth(self):
        self.write("api/handler.go", "package api\nfunc Handle() { store.Save() }\n")
        self.write("store/save.go", "package store\nfunc Save() { encode() }\n")
        self.write("store/enc.go", "package store\nfunc encode() {}\n")

        deep = self.run_cmd("trace", "Handle", depth=3)
        self.assertIn("Save", deep)
        self.assertIn("encode", deep)

        shallow = self.run_cmd("trace", "Handle", depth=1)
        self.assertIn("Save", shallow)
        self.assertNotIn("encode", shallow)

    def test_follows_callers_in_the_other_direction(self):
        self.write("cmd/main.go", "package main\nfunc run() { engine.Reconcile() }\n")
        self.write("engine/loop.go", "package engine\nfunc Reconcile() {}\n")
        out = self.run_cmd("trace", "Reconcile", callers=True)
        self.assertIn("<- run", out)

    def test_interface_dispatch_produces_no_edge_and_is_reported_as_unresolved(self):
        """The case the whole confidence scheme exists for."""
        self.write(
            "queue/iface.go",
            """
            package queue
            type Queue interface { Poll() error }
            func Next(q Queue) { q.Poll() }
            """,
        )
        self.write(
            "queue/fifo.go",
            "package queue\ntype fifo struct{}\nfunc (q *fifo) Poll() error { return nil }\n",
        )
        self.write(
            "queue/lifo.go",
            "package queue\ntype lifo struct{}\nfunc (q *lifo) Poll() error { return nil }\n",
        )
        out = self.run_cmd("trace", "Next")
        self.assertIn("unresolved", out)
        self.assertIn("fifo.Poll", out)
        self.assertIn("lifo.Poll", out)
        self.assertNotIn("-> fifo.Poll  (", out)  # no edge was invented

    def test_a_handler_registered_as_a_callback_is_reachable(self):
        """Framework routing leaves no call edge; passing the name is the only clue."""
        self.write(
            "router/routes.go",
            'package router\nfunc Register(r Router) { r.POST("/hook", api.PostHook) }\n',
        )
        self.write("api/hook.go", "package api\nfunc PostHook(c Context) {}\n")
        out = self.run_cmd("trace", "PostHook", callers=True)
        self.assertIn("Register", out)
        self.assertIn("callback", out)

    def test_a_variable_sharing_a_type_name_is_not_a_callback(self):
        """`New(client)` passes a value, even when a `client` type exists."""
        self.write(
            "svc.go",
            """
            package svc

            type client struct{}

            func New(c *client) {}
            func Setup(client *client) { New(client) }
            """,
        )
        out = self.run_cmd("trace", "Setup")
        self.assertNotIn("callback", out)

    def test_a_call_into_an_imported_package_is_not_matched_to_a_local_name(self):
        """`rand.New` is not the repo's own `New`, however well the tail matches."""
        self.write(
            "util/random.go",
            """
            package util

            import "math/rand"

            func Seed() { rand.New(rand.NewSource(1)) }
            """,
        )
        self.write("providers/aws.go", "package aws\nfunc New() {}\n")
        out = self.run_cmd("trace", "Seed")
        self.assertNotIn("aws.go", out)

    def test_a_call_into_a_relative_import_is_still_followed(self):
        self.write("pkg/queue.ts", "export function push() {}\n")
        self.write(
            "pkg/api.ts",
            'import * as queue from "./queue";\nexport function handle() { queue.push(); }\n',
        )
        self.assertIn("push", self.run_cmd("trace", "handle"))

    def test_unknown_name_says_so_rather_than_printing_an_empty_graph(self):
        self.write("a.go", "package a\nfunc Real() {}\n")
        self.assertIn("no declaration", self.run_cmd("trace", "Imaginary"))

    def test_seeding_prefers_exact_matches_over_substrings(self):
        """`Poll` must not drag in every method of a `Poller`."""
        self.write(
            "p.go",
            """
            package p
            func Poll() {}
            type Poller struct{}
            func (p *Poller) Drain() {}
            """,
        )
        out = self.run_cmd("trace", "Poll")
        self.assertIn("Poll", out)
        self.assertNotIn("Drain", out)


# --------------------------------------------------------------------------
# effects
# --------------------------------------------------------------------------


class EffectsTest(Case):
    def test_reports_goroutines_and_channel_handoffs(self):
        self.write(
            "worker.go",
            """
            package worker

            func process(w Worker, t *Task) *Task {
                go w.run(t)
                w.queue <- t
                return <-w.queue
            }
            """,
        )
        out = self.run_cmd("effects")
        self.assertIn("goroutine", out)
        self.assertIn("send to w.queue", out)
        self.assertIn("receive from w.queue", out)

    def test_reports_serialization_boundaries(self):
        self.write(
            "wire.go",
            """
            package wire

            func store(t Task, raw []byte) {
                data, _ := json.Marshal(t)
                _ = json.Unmarshal(raw, &t)
                use(data)
            }
            """,
        )
        out = self.run_cmd("effects", self.root, kind="boundary")
        self.assertIn("json.Marshal", out)
        self.assertIn("json.Unmarshal", out)

    def test_reports_await_in_typescript(self):
        self.write(
            "svc.ts",
            """
            export async function save(task: Task) {
              await store.write(task);
            }
            """,
        )
        self.assertIn("await", self.run_cmd("effects"))

    def test_kind_filter_excludes_everything_else(self):
        self.write(
            "mix.go",
            """
            package mix

            func run(w Worker, t Task) {
                go w.start()
                data, _ := json.Marshal(t)
                use(data)
            }
            """,
        )
        out = self.run_cmd("effects", self.root, kind="spawn")
        self.assertIn("goroutine", out)
        self.assertNotIn("json.Marshal", out)


# --------------------------------------------------------------------------
# what gets indexed, and what must not
# --------------------------------------------------------------------------


class ExclusionTest(Case):
    def test_credentials_never_enter_the_index(self):
        self.write("app.go", "package app\nfunc Main() {}\n")
        self.write("credentials.json", '{"token": "must-not-be-read"}\n')
        self.write(".env.production", "SECRET=must-not-be-read\n")
        self.write("server.key", "-----BEGIN PRIVATE KEY-----\n")
        files = list(ts_query.iter_files(self.root, ts_query.default_opts()))
        for path in files:
            self.assertNotIn("credentials", path)
            self.assertNotIn(".env", path)
            self.assertNotIn(".key", path)

    def test_generated_code_is_recognised_by_its_header_not_its_name(self):
        """mockery and protoc output rarely matches the `mock_` filename pattern."""
        self.write("real.go", "package app\nfunc Real() {}\n")
        self.write(
            "store.gen.go",
            "// Code generated by sqlc. DO NOT EDIT.\npackage app\nfunc Generated() {}\n",
        )
        default = self.run_cmd("trace", "Generated")
        self.assertIn("no declaration", default)
        self.assertNotIn(
            "no declaration", self.run_cmd("trace", "Generated", mocks=True)
        )

    def test_tests_are_excluded_until_asked_for(self):
        self.write("app.go", "package app\nfunc Main() {}\n")
        self.write("app_test.go", "package app\nfunc TestMain() {}\n")
        self.assertIn("no declaration", self.run_cmd("trace", "TestMain"))
        self.assertNotIn(
            "no declaration", self.run_cmd("trace", "TestMain", tests=True)
        )

    def test_oversized_files_are_skipped(self):
        self.write("small.go", "package app\nfunc Small() {}\n")
        self.write("big.go", "package app\n" + "// pad\n" * 2000 + "func Big() {}\n")
        opts = ts_query.default_opts(max_bytes=200)
        kept = [os.path.basename(p) for p in ts_query.iter_files(self.root, opts)]
        self.assertIn("small.go", kept)
        self.assertNotIn("big.go", kept)

    def test_gitignore_is_honoured_including_negations(self):
        self.write(".gitignore", "build/\n*.tmp.go\n!keep.tmp.go\n")
        self.write("app.go", "package app\nfunc Main() {}\n")
        self.write("build/out.go", "package build\nfunc Out() {}\n")
        self.write("drop.tmp.go", "package app\nfunc Drop() {}\n")
        self.write("keep.tmp.go", "package app\nfunc Keep() {}\n")
        kept = {
            os.path.basename(p)
            for p in ignores.walk(self.root).__iter__().__next__()[1]
        }
        found = {
            os.path.basename(p)
            for p in ts_query.iter_files(self.root, ts_query.default_opts())
        }
        self.assertIn("app.go", found)
        self.assertIn("keep.tmp.go", found)
        self.assertNotIn("drop.tmp.go", found)
        self.assertNotIn("out.go", found)
        self.assertTrue(kept)  # walk() yielded the root listing

    def test_no_gitignore_flag_indexes_everything(self):
        self.write(".gitignore", "hidden.go\n")
        self.write("hidden.go", "package app\nfunc Hidden() {}\n")
        opts = ts_query.default_opts(no_gitignore=True)
        found = {os.path.basename(p) for p in ts_query.iter_files(self.root, opts)}
        self.assertIn("hidden.go", found)


# --------------------------------------------------------------------------
# single-file components
# --------------------------------------------------------------------------


class SfcTest(Case):
    def test_both_script_blocks_of_a_vue_component_are_parsed(self):
        """Vue 3 routinely pairs `<script setup>` with a plain options `<script>`."""
        path = self.write(
            "Panel.vue",
            """
            <template><button @click="run">Run</button></template>

            <script lang="ts">
            export const panelName = "panel";
            </script>

            <script setup lang="ts">
            const run = () => emit("run");
            </script>
            """,
        )
        out = self.run_cmd("outline", path)
        self.assertIn("panelName", out)
        self.assertIn("run", out)

    def test_line_numbers_are_offset_back_to_the_real_file(self):
        path = self.write(
            "Panel.vue",
            """
            <template><div /></template>

            <script setup lang="ts">
            function handleClick() {}
            </script>
            """,
        )
        out = self.run_cmd("outline", path)
        with open(path, encoding="utf8") as handle:
            real = next(
                index
                for index, line in enumerate(handle.read().split("\n"), 1)
                if "handleClick" in line
            )
        self.assertIn(f"{real}:", out)


# --------------------------------------------------------------------------
# Nix, which fits none of the assumptions the other specs make
# --------------------------------------------------------------------------


class NixTest(Case):
    def test_import_is_distinguished_from_an_ordinary_application(self):
        """`import ./x.nix` is a call; without the hook every call is an import."""
        path = self.write(
            "default.nix",
            """
            { pkgs }:
            let
              common = import ./common.nix;
              built = pkgs.buildGoModule { pname = "app"; };
            in built
            """,
        )
        out = self.run_cmd("outline", path)
        imports = out.split("declarations")[0]
        self.assertIn("./common.nix", imports)
        self.assertNotIn("buildGoModule", imports)

    def test_hyphenated_attribute_names_are_not_truncated(self):
        self.write(
            "tests.nix",
            """
            { mysql-replication = 1; mysql = 2; }
            """,
        )
        self.assertEqual(
            "mysql-replication", ts_query.split_qualifier("mysql-replication")
        )

    def test_a_trailing_comma_in_a_formal_set_is_not_a_parse_error(self):
        """Valid Nix that the grammar flags as a missing identifier."""
        path = self.write(
            "module.nix",
            """
            {
              pkgs,
              lib,
            }:
            { name = "app"; }
            """,
        )
        self.run_cmd("outline", path)
        self.assertEqual(set(), ts_query._PARSE_ERRORS)

    def test_a_genuine_syntax_error_is_reported(self):
        path = self.write("broken.go", "package app\nfunc Broken( {\n")
        self.run_cmd("outline", path)
        self.assertIn(path, ts_query._PARSE_ERRORS)


# --------------------------------------------------------------------------
# Go: syntax newer than the grammar
# --------------------------------------------------------------------------


class GoNewExprTest(Case):
    """Go 1.26 lets `new` take an expression; tree_sitter_go 0.25 only takes a type.

    First seen on woodpecker's pipeline/backend/docker/docker.go, where
    `Timeout: new(int(e.config.stopTimeout))` marked the whole file as partial.
    """

    BODY = """
        package app

        type Box struct{ N *int }

        func seed() int { return 1 }

        // Stop mirrors the woodpecker call: new(<conversion>) inside a
        // composite literal that is itself an argument in an if header.
        func Stop(timeout int64) error {
        \tif _, err := run(Box{
        \t\tN: new(int(timeout)),
        \t}); err != nil {
        \t\treturn err
        \t}
        \treturn nil
        }

        func Mixed() {
        \t_ = new(seed())
        \t_ = new(42)
        \t_ = new("s")
        \t_ = new(-seed())
        \t_ = new(Box{N: nil})
        \t_ = new([]int)
        \t_ = new(map[string]int)
        \t_ = new(Box)
        \t_ = make([]int, 3)
        }
        """

    def test_new_with_an_expression_operand_is_not_a_parse_error(self):
        path = self.write("app.go", self.BODY)
        self.run_cmd("outline", path)
        self.assertEqual(set(), ts_query._PARSE_ERRORS)

    def test_calls_inside_new_are_still_found(self):
        """The point of repairing the parse: the operand is real code."""
        self.write("app.go", self.BODY)
        refs = self.run_cmd("refs", "seed")
        self.assertIn("app.go:19: seed(", refs)
        self.assertIn("app.go:22: seed(", refs)

    def test_new_keeps_its_name_in_output(self):
        """The workaround must not leak into what the reader sees."""
        path = self.write("app.go", self.BODY)
        refs = self.run_cmd("refs", "new")
        self.assertEqual(9, refs.count(": new("), refs)
        decl = self.run_cmd("decl", "Stop", path)
        self.assertIn("N: new(int(timeout)),", decl)
        outline = self.run_cmd("outline", path)
        self.assertNotIn(ts_query._GO_NEW_MASK.decode("utf8"), refs + decl + outline)

    def test_a_genuine_error_beside_new_is_still_reported(self):
        path = self.write(
            "broken.go",
            "package app\nfunc A() { _ = new(1) }\nfunc Broken( {\n",
        )
        self.run_cmd("outline", path)
        self.assertIn(path, ts_query._PARSE_ERRORS)


class InstallHintTest(Case):
    """Global pip is refused on PEP 668 Pythons and impossible on Nix."""

    def test_hint_points_at_the_bootstrap_not_global_pip(self):
        hint = ts_query.install_hint(["nix", "go"])
        self.assertIn("bootstrap.py --languages go,nix", hint)
        self.assertNotIn("--break-system-packages", hint)

    def test_langs_prints_the_hint_for_a_missing_grammar(self):
        real = ts_query.get_parser
        ts_query.get_parser = lambda spec: None if spec.key == "go" else real(spec)
        try:
            lines = []
            ts_query.cmd_langs(None, ts_query.default_opts(), lines.append)
            out = "\n".join(lines)
        finally:
            ts_query.get_parser = real
        self.assertIn("--languages go", out)


# --------------------------------------------------------------------------
# whole-repo analyses
# --------------------------------------------------------------------------


class RepoTest(Case):
    def test_cycles_finds_a_two_file_import_loop(self):
        self.write(
            "pkg/a.ts", 'import { b } from "./b";\nexport const a = () => b();\n'
        )
        self.write(
            "pkg/b.ts", 'import { a } from "./a";\nexport const b = () => a();\n'
        )
        out = self.run_cmd("cycles")
        self.assertIn("cycle", out)
        self.assertIn("a.ts", out)

    def test_cycles_says_an_empty_result_is_weak_evidence(self):
        self.write("pkg/a.ts", "export const a = 1;\n")
        self.assertIn("no import cycles", self.run_cmd("cycles"))

    def test_complexity_ranks_the_branchier_function_first(self):
        self.write(
            "calc.go",
            """
            package calc

            func flat() int { return 1 }

            func branchy(n int) int {
                if n > 0 {
                    for i := 0; i < n; i++ {
                        if i%2 == 0 && i > 4 {
                            return i
                        }
                    }
                }
                switch n {
                case 1:
                    return 1
                }
                return 0
            }
            """,
        )
        out = self.run_cmd("complexity")
        self.assertLess(out.index("branchy"), out.index("flat"))

    def test_map_reports_the_language_mix(self):
        self.write("a.go", "package a\nfunc A() {}\n")
        self.write("b.py", "def b():\n    pass\n")
        out = self.run_cmd("map")
        self.assertIn("go", out)
        self.assertIn("python", out)
        self.assertIn("densest", out)


# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------


class CliTest(Case):
    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "ts_query.py"), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_every_registered_command_appears_in_the_help_text(self):
        """The docstring is the only interface most callers ever read."""
        help_text = self._cli().stdout
        for command in ts_query.COMMANDS:
            self.assertIn(command, help_text)

    def test_json_output_is_parseable(self):
        import json

        self.write(
            "calc.go",
            "package calc\nfunc f(n int) int { if n > 0 { return 1 }\nreturn 0 }\n",
        )
        result = self._cli("complexity", self.root, "--json")
        self.assertTrue(json.loads(result.stdout))

    def test_missing_arguments_print_usage_rather_than_a_traceback(self):
        result = self._cli("methods")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("methods", result.stdout)


if __name__ == "__main__":
    unittest.main()
