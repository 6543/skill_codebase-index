"""Behaviour of the languages added for the common profile.

SQL, PowerShell, Fish, HTML and CSS each break an assumption the original
adapters could rely on: names are schema-qualified, case-insensitive, or not
names at all; a "call" is a table read or a stylesheet link; one file holds
several languages. Each test pins one of those decisions.

Run: python3 -m unittest discover -s scripts/tests
"""

import builtins
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_ts_query import Case  # noqa: E402

import ts_query
from languages import ALIASES, BY_KEY, canonical_key

# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------

SCHEMA = """
-- Orders placed by customers.
CREATE TABLE app.orders (
    id BIGINT PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(id),
    total NUMERIC(10, 2) NOT NULL
);

CREATE TABLE customers (id BIGINT PRIMARY KEY, name TEXT);

CREATE VIEW recent_orders AS
SELECT o.id, c.name FROM app.orders o JOIN customers c ON c.id = o.customer_id;

CREATE INDEX idx_orders_customer ON app.orders (customer_id);

-- Order total for one customer.
CREATE FUNCTION order_total(cid BIGINT) RETURNS NUMERIC AS $$
  SELECT sum(total) FROM app.orders WHERE customer_id = cid;
$$ LANGUAGE sql;
"""

QUERIES = """
INSERT INTO app.orders (id, customer_id, total) VALUES (1, 2, 3.5);
SELECT order_total(2) FROM recent_orders;
UPDATE customers SET name = 'x' WHERE id = 1;
"""


class SqlTest(Case):
    def setUp(self):
        super().setUp()
        self.schema = self.write("db/schema.sql", SCHEMA)
        self.queries = self.write("db/queries.sql", QUERIES)

    def test_outline_shows_schema_objects_with_qualified_names_and_columns(self):
        out = self.run_cmd("outline", self.schema)
        self.assertIn("table app.orders", out)
        self.assertIn("Orders placed by customers", out)
        self.assertIn("customer_id BIGINT REFERENCES customers(id)", out)
        self.assertIn("view recent_orders", out)
        self.assertIn("order_total(cid BIGINT)", out)
        self.assertIn("idx_orders_customer", out)

    def test_table_references_are_found_and_labelled_as_references(self):
        out = self.run_cmd("refs", "orders")
        # view body, function body, index, insert -- every read or write
        self.assertIn("queries.sql:1", out)
        self.assertIn("app.orders", out)
        self.assertIn("reference", out)
        self.assertNotIn("orders(...)", out)

    def test_the_declaration_itself_is_not_a_reference(self):
        out = self.run_cmd("refs", "orders")
        self.assertNotIn("schema.sql:2:", out)

    def test_function_invocation_is_a_call(self):
        out = self.run_cmd("refs", "order_total")
        self.assertIn("queries.sql:2", out)
        self.assertIn("order_total(...)", out)

    def test_decl_accepts_bare_and_schema_qualified_names(self):
        for name in ("orders", "app.orders"):
            with self.subTest(name=name):
                out = self.run_cmd("decl", name)
                self.assertIn("schema.sql:2", out)

    def test_quoted_identifiers_match_unquoted_queries(self):
        path = self.write(
            "quoted.sql",
            'CREATE TABLE "Audit Log" (a int);\nSELECT * FROM "Audit Log";\n',
        )
        self.assertIn("quoted.sql:1", self.run_cmd("decl", "Audit Log", path))
        self.assertIn("quoted.sql:2", self.run_cmd("refs", "Audit Log", path))

    def test_fields_lists_columns(self):
        out = self.run_cmd("fields", "orders")
        self.assertIn("total NUMERIC(10, 2) NOT NULL", out)

    def test_trace_from_a_view_reaches_the_tables_it_reads(self):
        out = self.run_cmd("trace", "recent_orders")
        self.assertIn("orders", out)
        self.assertIn("customers", out)
        self.assertIn("reference", out)

    def test_interface_question_is_not_applicable_rather_than_empty(self):
        out = self.run_cmd("iface", "orders", self.schema)
        self.assertTrue(out.startswith("`iface` is not applicable to sql"), out)


# --------------------------------------------------------------------------
# PowerShell
# --------------------------------------------------------------------------

MODULE = """
<#
.SYNOPSIS
Fetch a thing by name.
#>
function Get-Thing {
    param([string]$Name)
    Get-ChildItem -Path $Name | ConvertTo-Json
}

# Doubles pipeline input.
filter Double { $_ * 2 }

class Worker {
    [string]$Name
    Worker([string]$n) { $this.Name = $n }
    [void] Run() { Get-Thing -Name $this.Name; $this.Helper() }
    [void] Helper() { }
}

enum Color { Red; Green }
"""

SCRIPT = """
using module ./Things.psm1
Import-Module ActiveDirectory
. "$PSScriptRoot/common.ps1"
& ./run.ps1 -x 1
$Global:Limit = 10
get-thing -name foo
Start-Job -ScriptBlock { Get-Thing -Name bar }
"""


class PowerShellTest(Case):
    def setUp(self):
        super().setUp()
        self.module = self.write("Things.psm1", MODULE)
        self.script = self.write("main.ps1", SCRIPT)

    def test_outline_lists_functions_filters_classes_and_help(self):
        out = self.run_cmd("outline", self.module)
        self.assertIn("Get-Thing", out)
        self.assertIn("Fetch a thing by name", out)
        self.assertIn("Double", out)
        self.assertIn("class_statement Worker", out)
        self.assertIn("[string]$Name", out)
        self.assertIn("enum_statement Color", out)

    def test_calls_match_declarations_case_insensitively(self):
        out = self.run_cmd("refs", "Get-Thing")
        self.assertIn("main.ps1:6", out)  # get-thing -name foo
        self.assertIn("main.ps1:7", out)  # inside the Start-Job script block
        self.assertIn("Things.psm1:16", out)  # inside Worker.Run
        rows = out.split("\n\n")[0]  # the legend below names every label
        self.assertNotIn("UNRESOLVED", rows)

    def test_member_invocation_is_a_call(self):
        out = self.run_cmd("refs", "Helper")
        self.assertIn("Things.psm1:16", out)

    def test_methods_of_a_class(self):
        out = self.run_cmd("methods", "Worker")
        self.assertIn("Run", out)
        self.assertIn("Helper", out)

    def test_imports_are_module_loads_and_dot_sources_only(self):
        out = self.run_cmd("outline", self.script)
        imports = out.split("imports")[1].split("\n\n")[0]
        self.assertIn("using module ./Things.psm1", imports)
        self.assertIn("Import-Module ActiveDirectory", imports)
        self.assertIn("common.ps1", imports)
        self.assertNotIn("run.ps1", imports)  # `&` runs a script, it does not load it

    def test_jobs_and_serialisation_are_effects(self):
        out = self.run_cmd("effects")
        self.assertIn("[spawn] Start-Job", out)
        self.assertIn("[boundary] ConvertTo-Json", out)

    def test_module_manifest_reports_its_top_level_keys_only(self):
        path = self.write(
            "Things.psd1",
            """
            @{
                ModuleVersion = '1.0.0'
                PrivateData = @{ PSData = @{ Tags = @('x') } }
            }
            """,
        )
        out = self.run_cmd("outline", path)
        self.assertIn("ModuleVersion", out)
        self.assertIn("PrivateData", out)
        self.assertNotIn("\n  3: PSData", out)

    def test_interface_question_is_not_applicable(self):
        self.assertIn("not applicable", self.run_cmd("iface", "Worker", self.module))


# --------------------------------------------------------------------------
# Fish
# --------------------------------------------------------------------------

FISH_LIB = """
# Greets someone.
function greet --description 'say hello' --argument-names who
    set -l upper (string upper $who)
    echo hello $upper
end

function on_prompt --on-event fish_prompt
    greet prompt
end

set -gx EDITOR vim
"""

FISH_MAIN = """
source ./lib.fish
. ./other.fish
set -l x (greet bob | string upper)
test -n "$x"; and greet; or true
"""


class FishTest(Case):
    def setUp(self):
        super().setUp()
        self.lib = self.write("lib.fish", FISH_LIB)
        self.main = self.write("main.fish", FISH_MAIN)

    def test_outline_uses_description_and_shows_event_handlers(self):
        out = self.run_cmd("outline", self.lib)
        self.assertIn("Greets someone.", out)
        self.assertIn("say hello", out)
        self.assertIn("--on-event fish_prompt", out)

    def test_global_set_is_a_constant_but_a_function_local_is_not(self):
        out = self.run_cmd("outline", self.lib)
        self.assertIn("set -gx EDITOR vim", out)
        self.assertNotIn("set -l upper", out.split("const/var")[1])

    def test_refs_find_calls_in_substitutions_pipes_and_conditionals(self):
        out = self.run_cmd("refs", "greet")
        self.assertIn("main.fish:3", out)
        self.assertIn("main.fish:4", out)
        self.assertIn("lib.fish:8", out)
        self.assertNotIn("lib.fish:2:", out)  # the definition is not a call

    def test_sourced_files_are_imports_and_form_cycles(self):
        self.write("other.fish", "source ./main.fish\n")
        out = self.run_cmd("outline", self.main)
        self.assertIn("source ./lib.fish", out)
        self.assertIn(". ./other.fish", out)
        self.assertIn("other.fish -> main.fish", self.run_cmd("cycles"))

    def test_fish_is_not_parsed_as_bash(self):
        self.run_cmd("outline", self.lib)
        self.assertNotIn(self.lib, ts_query._PARSE_ERRORS)
        self.assertEqual(ts_query.spec_for(self.lib).key, "fish")

    def test_and_or_count_as_branches(self):
        path = self.write(
            "branchy.fish",
            "function b\n    test -n x; and echo a; or echo b\nend\n"
            "function flat\n    echo a\nend\n",
        )
        out = self.run_cmd("complexity", path)
        ranked = [ln.split()[-1] for ln in out.splitlines() if "branchy.fish:" in ln]
        self.assertEqual(ranked, ["b", "flat"])


# --------------------------------------------------------------------------
# HTML and CSS
# --------------------------------------------------------------------------

PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>Dashboard</title>
  <link rel="stylesheet" href="css/site.css">
  <script src="js/app.js"></script>
</head>
<body>
  <nav id="top-nav"><a href="#settings">Settings</a></nav>
  <section id="settings"><h1>Settings</h1></section>
  <script>
    // Starts the page.
    function boot() { render(); }
    function render() {}
    boot();
  </script>
  <style>.card { color: red; }</style>
</body>
</html>
"""

SITE_CSS = """
@import "theme.css";

/* Design tokens. */
:root {
  --brand: #336699;
}

.card, .panel > h2 {
  color: var(--brand);
  background: url(img/bg.png);
}

@media (max-width: 600px) {
  .card { padding: 2px; }
}

@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

#top-nav a:hover { animation: fade-in 1s; }
"""


class PhpTest(Case):
    """PHP joined the bundled profile: method calls keep their receiver."""

    def setUp(self):
        super().setUp()
        self.write(
            "src/Contracts/RouteInterface.php",
            """<?php
            namespace App\\Contracts;

            interface RouteInterface
            {
                /** Run the route. */
                public function run(): string;
            }
            """,
        )
        self.write(
            "src/Routing/Route.php",
            """<?php
            namespace App\\Routing;

            use App\\Contracts\\RouteInterface;

            class Route implements RouteInterface
            {
                private Store $store;

                public function run(): string
                {
                    $this->prepare();
                    $this->store->save();
                    $other->save();
                    return self::make();
                }

                public function prepare(): void {}

                public static function make(): string { return ""; }
            }
            """,
        )
        self.write(
            "src/Routing/Store.php",
            "<?php\nnamespace App\\Routing;\n\nclass Store { public function save(): void {} }\n",
        )
        self.write(
            "src/Cache.php",
            "<?php\nnamespace App;\n\nclass Cache { public function save(): void {} }\n",
        )

    def test_this_call_is_extracted(self):
        out = self.run_cmd("trace", "run")
        self.assertIn("-> Route.prepare", out)
        self.assertIn("EXTRACTED", out)

    def test_typed_property_selects_its_class(self):
        out = self.run_cmd("trace", "run")
        self.assertIn("-> Store.save", out)
        self.assertNotIn("-> Cache.save", out)

    def test_untyped_variable_receiver_draws_no_edge(self):
        out = self.run_cmd("refs", "save")
        row = [ln for ln in out.splitlines() if "$other->save" in ln][0]
        self.assertNotIn("INFERRED", row)
        self.assertNotIn("EXTRACTED", row)

    def test_self_static_call_resolves_in_class(self):
        self.assertIn("-> Route.make", self.run_cmd("trace", "run"))

    def test_impls_uses_the_implements_clause(self):
        out = self.run_cmd("impls", "RouteInterface")
        self.assertIn("Route", out)
        self.assertIn("declared", out)

    def test_namespace_use_resolves_to_the_psr4_file(self):
        self.write(
            "src/Contracts/Loop.php",
            "<?php\nnamespace App\\Contracts;\n\nuse App\\Routing\\Back;\n\ninterface Loop {}\n",
        )
        self.write(
            "src/Routing/Back.php",
            "<?php\nnamespace App\\Routing;\n\nuse App\\Contracts\\Loop;\n\nclass Back {}\n",
        )
        self.assertIn("import cycle(s)", self.run_cmd("cycles"))


class HtmlTest(Case):
    def setUp(self):
        super().setUp()
        self.page = self.write("index.html", PAGE)
        self.css = self.write("css/site.css", SITE_CSS)

    def test_outline_is_a_document_outline(self):
        out = self.run_cmd("outline", self.page)
        self.assertIn("title: Dashboard", out)
        self.assertIn("#top-nav", out)
        self.assertIn("h1 Settings", out)
        self.assertIn("css/site.css", out)
        self.assertIn("js/app.js", out)
        self.assertIn("inline <script>", out)
        self.assertNotIn("funcs", out.split("inline <script>")[0])

    def test_inline_script_functions_keep_their_real_line_numbers(self):
        out = self.run_cmd("decl", "boot", self.page)
        self.assertIn("index.html:13", out)
        self.assertIn("Starts the page.", out)
        refs = self.run_cmd("refs", "render", self.page)
        self.assertIn("index.html:13", refs)
        self.assertIn("EXTRACTED", refs)

    def test_fragment_links_reference_element_ids(self):
        out = self.run_cmd("refs", "settings", self.page)
        self.assertIn("index.html:9", out)
        self.assertIn("index.html:10", out)  # resolved to the section

    def test_decl_finds_an_element_by_id(self):
        out = self.run_cmd("decl", "top-nav")
        self.assertIn('<nav id="top-nav">', out)

    def test_stylesheet_is_referenced_from_html_by_file_name(self):
        out = self.run_cmd("refs", "site.css")
        self.assertIn("index.html:5", out)

    def test_code_questions_are_not_applicable_to_markup(self):
        plain = self.write("plain.html", "<html><body><p>hi</p></body></html>\n")
        out = self.run_cmd("complexity", plain)
        self.assertIn("not applicable", out)
        self.assertNotIn("no functions found", out)

    def test_inline_script_still_answers_code_questions(self):
        out = self.run_cmd("complexity", self.page)
        self.assertIn("boot", out)
        self.assertIn("html", out.split("not applicable")[1])

    def test_css_imports_and_urls_are_references(self):
        out = self.run_cmd("refs", "bg.png")
        self.assertIn("site.css:10", out)


class CssTest(Case):
    def setUp(self):
        super().setUp()
        self.css = self.write("site.css", SITE_CSS)

    def test_outline_lists_imports_tokens_rules_and_keyframes(self):
        out = self.run_cmd("outline", self.css)
        self.assertIn("theme.css", out)
        self.assertIn("--brand", out)
        self.assertIn("Design tokens.", out)
        self.assertIn(".card, .panel > h2", out)
        self.assertIn("@media (max-width: 600px)", out)
        self.assertIn("@keyframes fade-in", out)

    def test_custom_property_references_are_var_calls(self):
        out = self.run_cmd("refs", "--brand")
        self.assertIn("site.css:9: var(--brand)", out)
        self.assertNotIn("site.css:5:", out)  # the definition is not a use
        self.assertIn("[rule_set .card, .panel > h2]", out)

    def test_keyframes_are_referenced_by_animation(self):
        out = self.run_cmd("refs", "fade-in")
        self.assertIn("site.css:22", out)

    def test_decl_matches_one_selector_of_a_selector_list(self):
        out = self.run_cmd("decl", ".panel > h2")
        self.assertIn("site.css:8", out)

    def test_fields_lists_a_rules_declarations(self):
        out = self.run_cmd("fields", ".card")
        self.assertIn("color: var(--brand)", out)

    def test_import_loops_are_cycles(self):
        self.write("theme.css", '@import "site.css";\n')
        self.assertIn("site.css", self.run_cmd("cycles"))


class VueTemplateTest(Case):
    def test_outline_says_the_template_was_not_analysed(self):
        path = self.write(
            "C.vue",
            "<template><div/></template>\n<script>\nexport default {}\n</script>\n",
        )
        self.assertIn("template not analysed", self.run_cmd("outline", path))


# --------------------------------------------------------------------------
# file detection and names
# --------------------------------------------------------------------------


class DetectionTest(Case):
    def test_extensionless_scripts_use_their_shebang(self):
        cases = {
            "bin/deploy": ("#!/usr/bin/env fish\necho hi\n", "fish"),
            "bin/build": ("#!/bin/bash\necho hi\n", "bash"),
            "bin/tool": (
                "#!/usr/bin/env -S pwsh -NoProfile\nWrite-Host hi\n",
                "powershell",
            ),
        }
        for rel, (body, key) in cases.items():
            with self.subTest(rel=rel):
                spec = ts_query.spec_for(self.write(rel, body))
                self.assertIsNotNone(spec)
                self.assertEqual(spec.key, key)

    def test_extensionless_file_without_shebang_is_ignored(self):
        self.assertIsNone(ts_query.spec_for(self.write("LICENSE", "MIT\n")))

    def test_sensitive_files_are_never_opened_to_sniff_a_shebang(self):
        self.write("id_rsa", "#!/bin/bash\necho secret\n")
        self.write("ok", "#!/bin/bash\necho fine\n")
        opened = []
        real_open = builtins.open

        def spy(path, *args, **kwargs):
            opened.append(os.fspath(path))
            return real_open(path, *args, **kwargs)

        builtins.open = spy
        try:
            files = list(ts_query.iter_files(self.root, ts_query.default_opts()))
        finally:
            builtins.open = real_open
        self.assertTrue(any(p.endswith("ok") for p in files))
        self.assertFalse(any(p.endswith("id_rsa") for p in opened))

    def test_header_with_cpp_constructs_is_parsed_as_cpp(self):
        cpp = self.write(
            "a.h", "namespace app {\nclass Queue { public: void Poll(); };\n}\n"
        )
        c = self.write(
            "b.h", '#ifdef __cplusplus\nextern "C" {\n#endif\nint poll(void);\n'
        )
        self.assertEqual(ts_query.spec_for(cpp).key, "cpp")
        self.assertEqual(ts_query.spec_for(c).key, "c")

    def test_language_aliases_resolve(self):
        for alias, key in (
            ("c#", "c_sharp"),
            ("C++", "cpp"),
            ("pwsh", "powershell"),
            ("ts", "typescript"),
        ):
            with self.subTest(alias=alias):
                self.assertEqual(canonical_key(alias), key)
        self.assertIsNone(canonical_key("cobol"))
        for key in ALIASES.values():
            self.assertIn(key, BY_KEY)

    def test_lang_filter_accepts_an_alias(self):
        self.write("a.cs", "class A { void M() {} }\n")
        self.write("b.go", "package b\nfunc B() {}\n")
        out = self.run_cmd("map", lang=ts_query.resolve_lang("c#"))
        self.assertIn("c_sharp", out)
        self.assertNotIn("go ", out)


class NotApplicableTest(Case):
    def test_directory_with_only_inapplicable_languages_says_so(self):
        self.write("a.css", ".a { color: red; }\n")
        self.write("b.sql", "CREATE TABLE t (a int);\n")
        out = self.run_cmd("effects")
        self.assertIn("not applicable", out)

    def test_mixed_directory_runs_and_notes_the_skipped_languages(self):
        self.write("a.css", ".a { color: red; }\n")
        self.write("b.go", "package b\nfunc B() { go B() }\n")
        out = self.run_cmd("effects")
        self.assertIn("goroutine", out)
        self.assertIn("css", out.split("not applicable")[1])

    def test_json_result_is_marked_not_applicable(self):
        path = self.write("a.css", ".a { color: red; }\n")
        lines = []
        handler = ts_query.run_command
        handler("iface", ["X", path], ts_query.default_opts(json=True), lines.append)
        self.assertEqual(lines[0]["not_applicable"], "iface")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# regressions in existing adapters, found while validating the common profile
# --------------------------------------------------------------------------


class DeclaratorNameTest(Case):
    def test_out_of_line_cpp_definition_is_named_after_the_function(self):
        path = self.write(
            "a.cpp", "void app::run(int n) {}\nint Box<int>::get() { return 1; }\n"
        )
        out = self.run_cmd("outline", path)
        self.assertIn("run(int n)", out)
        self.assertNotIn("app(int n)", out)
        self.assertIn("a.cpp:1", self.run_cmd("decl", "run"))

    def test_function_pointer_declarators_keep_their_name_and_own_params(self):
        path = self.write(
            "a.c",
            "typedef int (*handler_fn)(int);\n"
            "int (*pick(void))(int) { return 0; }\n"
            "void reg(int (*cb)(int)) {}\n",
        )
        out = self.run_cmd("outline", path)
        self.assertIn("type_definition handler_fn", out)
        self.assertIn("pick(void)", out)
        self.assertIn("reg(int (*cb)(int))", out)


class RustImplOutlineTest(Case):
    def test_impl_block_methods_appear_in_the_outline(self):
        path = self.write(
            "a.rs",
            "pub trait Queue { fn poll(&self); }\npub struct Fifo;\n"
            "impl Queue for Fifo { fn poll(&self) {} }\nimpl Fifo { pub fn new() -> Self { Fifo } }\n",
        )
        out = self.run_cmd("outline", path)
        self.assertIn("impl Queue for Fifo", out)
        self.assertIn("fn new() -> Self", out)


class SqlErrorIsolationTest(Case):
    """One statement the grammar rejects must not swallow the rest of the file."""

    def test_tables_after_an_unsupported_statement_are_still_found(self):
        path = self.write(
            "schema.sql",
            """
            CREATE TABLE a (id int);
            CREATE PROCEDURE dbo.p AS BEGIN SELECT 1; END;
            GO
            CREATE TABLE b (id int);
            CREATE FUNCTION f() RETURNS int AS $$ SELECT 1; $$ LANGUAGE sql;
            CREATE TABLE "c;d" (note text DEFAULT 'x;y');
            """,
        )
        out = self.run_cmd("outline", path)
        for table in ("table a", "table b", 'table "c;d"'):
            self.assertIn(table, out)
        self.assertIn("f()", out)
        self.assertIn(path, ts_query._PARSE_ERRORS)

    def test_rejected_statement_keeps_later_line_numbers(self):
        path = self.write(
            "s.sql",
            "CREATE TABLE a (id int);\nFOO BAR BAZ;\n\nCREATE TABLE b (id int);\n",
        )
        self.assertIn("s.sql:4", self.run_cmd("decl", "b", path))

    def test_statement_splitter_respects_quotes_comments_and_dollar_bodies(self):
        src = (
            b"SELECT ';' -- ; not here\n;"
            b"/* ; */ SELECT $fn$ a; b $fn$;\n"
            b"SELECT 1\n/\n"
            b"SELECT 2\ngo\n"
            b"SELECT 3"
        )
        spans = ts_query._sql_statement_spans(src)
        texts = [src[a:b].strip() for a, b in spans]
        self.assertEqual(len(texts), 5, texts)
        self.assertTrue(texts[1].endswith(b"$fn$;"))


class CliNameTest(Case):
    """CSS custom properties start with `--`, like a flag."""

    def _cli(self, *args):
        import subprocess
        import sys

        return subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(ts_query.__file__), "ts_query.py"),
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_custom_property_name_is_a_positional_argument(self):
        path = self.write(
            "a.css", ":root { --brand: red; }\n.a { color: var(--brand); }\n"
        )
        result = self._cli("refs", "--brand", path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("a.css:2", result.stdout)

    def test_double_dash_ends_option_parsing(self):
        path = self.write("a.css", ":root { --json: red; }\n")
        result = self._cli("decl", "--", "--json", path)
        self.assertIn("a.css:1", result.stdout)

    def test_unknown_flag_like_word_before_command_is_still_rejected_cleanly(self):
        result = self._cli("--nonsense")
        self.assertNotIn("Traceback", result.stderr)


class ConfidenceLabelTest(Case):
    """EXTRACTED is documented as same-file only."""

    def test_unique_declaration_in_another_file_is_inferred(self):
        self.write("lib.go", "package p\nfunc Helper() {}\n")
        self.write("main.go", "package p\nfunc Main() { Helper() }\n")
        refs = self.run_cmd("refs", "Helper")
        self.assertIn("INFERRED", refs)
        self.assertNotIn("EXTRACTED", refs.split("\n\n")[0])
        self.assertIn("[call, INFERRED]", self.run_cmd("trace", "Main"))

    def test_unique_declaration_in_the_same_file_is_extracted(self):
        self.write("main.go", "package p\nfunc Helper() {}\nfunc Main() { Helper() }\n")
        self.assertIn("[call, EXTRACTED]", self.run_cmd("trace", "Main"))


class SqlTriggerCallTest(Case):
    def test_trigger_executing_a_function_is_a_call_edge(self):
        self.write(
            "s.sql",
            "CREATE FUNCTION touch() RETURNS trigger AS $$ BEGIN RETURN NEW; END $$ LANGUAGE plpgsql;\n"
            "CREATE TRIGGER t BEFORE UPDATE ON actor FOR EACH ROW EXECUTE FUNCTION touch();\n",
        )
        refs = self.run_cmd("refs", "touch")
        self.assertIn("s.sql:2: touch(...)", refs)
        self.assertIn("[call, EXTRACTED]", self.run_cmd("trace", "t"))


class CHeaderTest(Case):
    HEADER = """
    struct node;
    typedef struct {
        int kind;
        struct node *next;
    } item;
    /* Frees an item. */
    void item_free(item *it);
    extern int item_count;
    int item_size(const struct node *n);
    """

    def test_bodiless_struct_usage_is_not_a_type_declaration(self):
        path = self.write("item.h", self.HEADER)
        out = self.run_cmd("outline", path)
        self.assertNotIn("struct_specifier node", out)
        self.assertIn("no declaration named 'node'", self.run_cmd("decl", "node"))

    def test_typedef_of_anonymous_struct_shows_its_fields(self):
        path = self.write("item.h", self.HEADER)
        out = self.run_cmd("outline", path)
        self.assertIn("type_definition item", out)
        self.assertIn("int kind", out)
        self.assertNotIn("<anonymous>", out)
        self.assertIn("struct node *next", self.run_cmd("fields", "item"))

    def test_prototypes_are_listed_apart_from_variables(self):
        path = self.write("item.h", self.HEADER)
        out = self.run_cmd("outline", path)
        prototypes = out.split("prototypes")[1]
        self.assertIn("item_free(item *it)", prototypes)
        self.assertIn("Frees an item.", prototypes)
        self.assertIn(
            "extern int item_count", out.split("const/var")[1].split("prototypes")[0]
        )


class HtmlLinkKindTest(Case):
    def test_link_kind_comes_from_rel(self):
        path = self.write(
            "i.html",
            '<link rel="stylesheet" href="a.css"><link rel="icon" href="f.ico">'
            '<link rel="manifest" href="m.json">\n',
        )
        out = self.run_cmd("outline", path)
        self.assertIn("stylesheet a.css", out)
        self.assertIn("icon f.ico", out)
        self.assertIn("manifest m.json", out)


class TailwindAtRuleTest(Case):
    def test_unknown_statement_at_rules_do_not_corrupt_the_next_rule(self):
        path = self.write(
            "app.css",
            '@import "tailwindcss";\n@plugin "@tailwindcss/typography";\n'
            '@reference "./base.css";\n@custom-variant dark (&:where(.dark, .dark *));\n'
            ":root { --brand: red; }\n.card { color: var(--brand); }\n",
        )
        out = self.run_cmd("outline", path)
        self.assertIn("  5: :root", out)
        self.assertIn("  6: .card", out)
        self.assertIn('@plugin "@tailwindcss/typography"', out)
        self.assertNotIn(path, ts_query._PARSE_ERRORS)


class CSignatureTest(Case):
    def test_return_type_keeps_qualifiers_and_pointers(self):
        path = self.write(
            "a.c",
            "const char *name(int k) { return 0; }\nstatic char **list(void) { return 0; }\n"
            "struct s { union { int a; float b; } u; };\n",
        )
        out = self.run_cmd("outline", path)
        self.assertIn("name(int k) -> const char *", out)
        self.assertIn("list(void) -> char **", out)
        self.assertNotIn("union_specifier", out)


class PythonRelativeImportTest(Case):
    """`from .x import y` must resolve inside the repo, not mark `y` external."""

    def setUp(self):
        super().setUp()
        self.write("pkg/__init__.py", "")
        self.write("pkg/globals.py", "current_app = None\n")
        self.write("pkg/util.py", "def fmt(v):\n    return v\n")
        self.write(
            "pkg/provider.py",
            "class A:\n    def response(self):\n        pass\n\n"
            "class B:\n    def response(self):\n        pass\n",
        )
        self.write(
            "pkg/api.py",
            "from .globals import current_app\nfrom . import util\nimport os.path\n\n"
            "def jsonify():\n    return current_app.json.response()\n\n"
            "def show(v):\n    return util.fmt(v)\n\n"
            "def where():\n    return os.path.join('a')\n",
        )

    def test_call_through_a_relatively_imported_name_is_followed(self):
        out = self.run_cmd("trace", "show")
        self.assertIn("-> fmt", out)

    def test_ambiguous_call_through_it_is_reported_not_dropped(self):
        out = self.run_cmd("trace", "jsonify")
        self.assertIn("current_app.json.response", out.split("unresolved")[1])

    def test_relative_imports_resolve_for_cycles(self):
        self.write(
            "pkg/util.py", "from .api import show\n\ndef fmt(v):\n    return v\n"
        )
        self.assertIn("api.py", self.run_cmd("cycles"))


class SignatureLengthTest(Case):
    def test_a_misparsed_return_type_is_not_printed_whole(self):
        path = self.write(
            "a.cpp",
            "FMT_EXPORT class dynamic_arg_list {\n"
            + "".join(f"  int field_{i};\n" for i in range(40))
            + "};\n",
        )
        out = self.run_cmd("outline", path)
        self.assertLess(max(len(line) for line in out.splitlines()), 200)


class QualifiedReceiverTest(Case):
    """Same file is evidence for `this.x()`, not for `other.x()`."""

    def setUp(self):
        super().setUp()
        self.write(
            "app.js",
            "class App {\n  render() {}\n  tryRender(view) { view.render(); }\n"
            "  again() { this.render(); }\n}\n",
        )
        self.write("view.js", "class View {\n  render() {}\n}\n")

    def test_call_on_another_object_is_not_pinned_to_the_same_file(self):
        refs = self.run_cmd("refs", "render")
        line = next(ln for ln in refs.splitlines() if "app.js:3" in ln)
        self.assertIn("AMBIGUOUS", line)

    def test_call_on_this_still_prefers_the_same_file(self):
        refs = self.run_cmd("refs", "render")
        line = next(ln for ln in refs.splitlines() if "app.js:4" in ln)
        self.assertIn("EXTRACTED", line)

    def test_go_method_receiver_counts_as_self(self):
        self.write(
            "q.go",
            "package q\ntype fifo struct{}\nfunc (q *fifo) Poll() { q.helper() }\n"
            "func (q *fifo) helper() {}\n",
        )
        self.write(
            "other.go", "package q\ntype lifo struct{}\nfunc (l *lifo) helper() {}\n"
        )
        refs = self.run_cmd("refs", "helper")
        self.assertIn(
            "EXTRACTED", next(ln for ln in refs.splitlines() if "q.go:3" in ln)
        )


# --------------------------------------------------------------------------
# found by the eval runs
# --------------------------------------------------------------------------


class TestFileDetectionTest(unittest.TestCase):
    def test_conventions_across_languages_are_tests(self):
        for name in (
            "Mock.Tests.ps1",
            "queue_test.go",
            "test_api.py",
            "app.spec.ts",
            "Button.test.tsx",
            "JsonParserTest.java",
            "StoreTests.cs",
            "util.test.fish",
        ):
            with self.subTest(name=name):
                self.assertTrue(ts_query.is_test(f"/r/{name}"))

    def test_names_merely_containing_test_are_not(self):
        for name in (
            "latest_version.py",
            "contest_rules.go",
            "Attestation.java",
            "manifest.ts",
        ):
            with self.subTest(name=name):
                self.assertFalse(ts_query.is_test(f"/r/{name}"))


class OutlineDirectoryTest(Case):
    def test_outline_of_a_directory_points_to_map(self):
        self.write("a.go", "package a\n")
        out = self.run_cmd("outline", self.root)
        self.assertIn("use `map`", out)


class DynamicCallTest(Case):
    def test_fish_call_with_a_computed_name_is_listed_as_unresolved(self):
        self.write(
            "p.fish",
            "function render\n    for item in a b\n        _item_$item\n    end\nend\n"
            "function _item_a\nend\n",
        )
        out = self.run_cmd("trace", "render")
        self.assertIn("_item_$item", out.split("unresolved")[1])
        self.assertIn("name computed at runtime", out)

    def test_bash_variable_command_is_listed_as_unresolved(self):
        self.write("r.sh", 'run() {\n  "$handler" arg\n}\n')
        self.assertIn("$handler", self.run_cmd("trace", "run").split("unresolved")[1])

    def test_powershell_call_operator_on_a_variable_is_listed(self):
        self.write("r.ps1", "function Run {\n    & $SafeCommands['Write-Host'] hi\n}\n")
        out = self.run_cmd("trace", "Run")
        self.assertIn("$SafeCommands['Write-Host']", out.split("unresolved")[1])


class PlpgsqlBodyTest(Case):
    FN = """
    CREATE TABLE rental (id int, inventory_id int, return_date date);
    CREATE TABLE inventory (inventory_id int);
    CREATE FUNCTION in_stock(p integer) RETURNS boolean
        LANGUAGE plpgsql
        AS $$
    DECLARE
        v_rentals INTEGER;
        v_out     INTEGER;
    BEGIN
        SELECT count(*) INTO v_rentals FROM rental WHERE inventory_id = p;
        IF v_rentals = 0 THEN
          RETURN TRUE;
        END IF;
        SELECT COUNT(id) INTO v_out
        FROM inventory LEFT JOIN rental USING(inventory_id)
        WHERE inventory.inventory_id = p AND rental.return_date IS NULL;
        PERFORM touch(p);
        IF v_out > 0 THEN
          RETURN FALSE;
        ELSE
          RETURN TRUE;
        END IF;
    END $$;
    """

    def test_trace_reaches_tables_read_inside_control_flow(self):
        self.write("s.sql", self.FN)
        out = self.run_cmd("trace", "in_stock")
        self.assertIn("-> rental", out)
        self.assertIn("-> inventory", out)

    def test_perform_counts_as_a_call(self):
        self.write(
            "s.sql",
            self.FN
            + "CREATE FUNCTION touch(p int) RETURNS void AS $$ SELECT 1 $$ LANGUAGE sql;\n",
        )
        self.assertIn("touch(...)", self.run_cmd("refs", "touch"))

    def test_decl_shows_the_body_as_written(self):
        self.write("s.sql", self.FN)
        out = self.run_cmd("decl", "in_stock")
        self.assertIn("IF v_rentals = 0 THEN", out)
        self.assertIn("PERFORM touch(p);", out)


class SqlScaleTest(Case):
    def test_a_large_dump_is_outlined_quickly(self):
        import time

        rows = "".join(f"INSERT INTO t VALUES ({i}, 'x;y');\n" for i in range(20000))
        path = self.write(
            "dump.sql", "CREATE TABLE t (a int, b text);\n" + rows + "SET x = ;\n"
        )
        started = time.monotonic()
        out = self.run_cmd("outline", path)
        self.assertLess(time.monotonic() - started, 20)
        self.assertIn("table t", out)


class MapCountsFilesTest(Case):
    def test_a_file_split_into_regions_counts_once(self):
        self.write(
            "a.sql", "CREATE TABLE a (id int);\nFOO BAR;\nCREATE TABLE b (id int);\n"
        )
        self.write(
            "C.vue",
            "<script>\nexport default {}\n</script>\n<script setup>\nconst x = 1\n</script>\n",
        )
        out = self.run_cmd("map", json=True)
        stats = out if isinstance(out, dict) else __import__("ast").literal_eval(out)
        self.assertEqual(stats["sql"]["files"], 1)
        self.assertEqual(stats["vue"]["files"], 1)
        self.assertEqual(
            stats["sql"]["densest"].count(os.path.join(self.root, "a.sql")), 1
        )
