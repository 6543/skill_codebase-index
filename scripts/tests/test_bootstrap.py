"""Bootstrap behaviour: offline install, integrity, isolation, reuse.

The integration tests build real virtual environments from the bundled wheels
with no package index, so they need a CPython that has a bundle (3.12 or 3.13
on Linux x86_64 glibc) and the venv module; elsewhere they are skipped.

Run: python3 -m unittest discover -s scripts/tests
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)

import bootstrap  # noqa: E402
import runtime_manifest  # noqa: E402
import runtime_probe  # noqa: E402
from languages import ALIASES, ALL, BY_KEY  # noqa: E402

MANIFEST = runtime_manifest.load_manifest(SKILL)
WHEELS = os.path.join(SKILL, "vendor", "wheels")


def bundled_interpreter():
    """A CPython with a bundled target, or None."""
    candidates = [
        sys.executable,
        shutil.which("python3.12"),
        shutil.which("python3.13"),
    ]
    for python in filter(None, candidates):
        try:
            if runtime_manifest.select_target(MANIFEST, bootstrap.describe(python)):
                return python
        except bootstrap.BootstrapError:
            continue
    return None


# The Nix sandbox (flake check) has no pip route to test; it sets this.
PYTHON = (
    None if os.environ.get("CODEBASE_INDEX_SKIP_VENV_TESTS") else bundled_interpreter()
)
OFFLINE = {
    # Any accidental index access fails fast instead of succeeding quietly.
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "HTTP_PROXY": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
    "http_proxy": "http://127.0.0.1:9",
}


def run_bootstrap(*args, env=None):
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "bootstrap.py"), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **OFFLINE, **(env or {})},
        check=False,
    )
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = {"raw": result.stdout}
    return result.returncode, report, result.stderr


# --------------------------------------------------------------------------
# registry contracts: the lists that must not drift apart
# --------------------------------------------------------------------------


class InventoryContractTest(unittest.TestCase):
    def test_every_language_has_a_grammar_pin_and_a_probe_fixture(self):
        for spec in ALL:
            with self.subTest(language=spec.key):
                self.assertIn(spec.key, MANIFEST["languages"])
                self.assertIn(spec.key, runtime_probe.FIXTURES)
                self.assertEqual(MANIFEST["languages"][spec.key]["module"], spec.module)

    def test_every_bundled_target_covers_the_whole_bundled_profile(self):
        profile = MANIFEST["profiles"][MANIFEST["bundled_profile"]]
        for target in MANIFEST["targets"]:
            with self.subTest(target=target):
                files = runtime_manifest.wheels_for(MANIFEST, target, profile)
                runtime_manifest.verify_wheels(WHEELS, MANIFEST, files)

    def test_aliases_point_at_registered_languages(self):
        for alias, key in ALIASES.items():
            self.assertIn(key, BY_KEY, alias)

    def test_fish_sources_match_their_recorded_digests(self):
        base = os.path.join(SKILL, "packaging", "fish")
        with open(os.path.join(base, "SOURCE.json")) as handle:
            source = json.load(handle)
        for relative, digest in source["files"].items():
            with self.subTest(file=relative):
                self.assertEqual(
                    runtime_manifest.sha256_file(os.path.join(base, relative)), digest
                )

    def test_every_shipped_path_is_importable_by_the_skill_uploader(self):
        # The skill importer rejects archive paths with characters outside this
        # set ("Zip file contains path with invalid characters"). A PEP 440
        # local version such as 0.1.0+gabc puts '+' into a wheel filename.
        for root, dirs, files in os.walk(SKILL):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in dirs + files:
                with self.subTest(path=os.path.relpath(os.path.join(root, name), SKILL)):
                    self.assertRegex(name, r"^[A-Za-z0-9._-]+$")

    def test_manifest_built_from_another_inventory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "vendor"))
            shutil.copy(
                os.path.join(SKILL, "vendor", "wheels.json"),
                os.path.join(tmp, "vendor"),
            )
            with open(os.path.join(SKILL, "vendor", "inventory.json")) as handle:
                inventory = json.load(handle)
            inventory["runtime"]["version"] = "9.9.9"
            with open(os.path.join(tmp, "vendor", "inventory.json"), "w") as handle:
                json.dump(inventory, handle)
            with self.assertRaisesRegex(runtime_manifest.ManifestError, "different"):
                runtime_manifest.load_manifest(tmp)


# --------------------------------------------------------------------------
# target selection and bundle integrity
# --------------------------------------------------------------------------


class TargetSelectionTest(unittest.TestCase):
    BASE = {
        "implementation": "cpython",
        "version": "3.12.4",
        "machine": "x86_64",
        "libc": "glibc",
        "libc_version": "2.31",
    }

    def test_matching_interpreter_gets_its_bundle(self):
        self.assertEqual(
            runtime_manifest.select_target(MANIFEST, self.BASE),
            "cp312-manylinux_x86_64",
        )

    def test_interpreters_without_a_bundle_are_not_matched(self):
        for change in (
            {"version": "3.11.9"},
            {"version": "3.14.0"},
            {"machine": "aarch64"},
            {"libc": "musl", "libc_version": ""},
            {"libc_version": "2.12"},
            {"implementation": "pypy"},
        ):
            with self.subTest(change=change):
                self.assertIsNone(
                    runtime_manifest.select_target(MANIFEST, {**self.BASE, **change})
                )


class IntegrityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.house = os.path.join(self._tmp.name, "wheels")
        shutil.copytree(WHEELS, self.house)
        self.files = runtime_manifest.wheels_for(
            MANIFEST, "cp313-manylinux_x86_64", ["go", "sql"]
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_intact_bundle_verifies(self):
        runtime_manifest.verify_wheels(self.house, MANIFEST, self.files)

    def test_corrupt_wheel_is_rejected_by_name(self):
        victim = next(f for f in self.files if "tree_sitter_go" in f)
        path = os.path.join(self.house, victim)
        with open(path, "r+b") as handle:
            handle.seek(100)
            byte = handle.read(1)
            handle.seek(100)
            handle.write(bytes([byte[0] ^ 0xFF]))
        with self.assertRaisesRegex(
            runtime_manifest.ManifestError, "tree_sitter_go.*SHA-256"
        ):
            runtime_manifest.verify_wheels(self.house, MANIFEST, self.files)

    def test_missing_wheel_is_rejected_by_name(self):
        victim = next(f for f in self.files if "tree_sitter_sql" in f)
        os.remove(os.path.join(self.house, victim))
        with self.assertRaisesRegex(
            runtime_manifest.ManifestError, "tree_sitter_sql.*missing"
        ):
            runtime_manifest.verify_wheels(self.house, MANIFEST, self.files)

    def test_unsafe_or_unlisted_file_names_are_rejected(self):
        for name in (
            "../evil-1.0-py3-none-any.whl",
            "notawheel.zip",
            "x-1.0-py3-none-any.whl",
        ):
            with self.subTest(name=name):
                with self.assertRaises(runtime_manifest.ManifestError):
                    runtime_manifest.verify_wheels(self.house, MANIFEST, [name])

    def test_language_outside_the_bundle_is_named(self):
        with self.assertRaisesRegex(runtime_manifest.ManifestError, "kotlin"):
            runtime_manifest.wheels_for(
                MANIFEST, "cp313-manylinux_x86_64", ["go", "kotlin"]
            )


# --------------------------------------------------------------------------
# probe isolation
# --------------------------------------------------------------------------


class ProbeCrashTest(unittest.TestCase):
    def test_a_native_crash_is_a_failed_probe_naming_the_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "python")
            with open(fake, "w") as handle:
                handle.write(
                    textwrap.dedent(
                        """\
                        #!/bin/sh
                        echo '{"start": "go"}'
                        echo '{"start": "sql"}'
                        kill -SEGV $$
                        """
                    )
                )
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            report = bootstrap.probe(fake, ["go", "sql"])
        self.assertFalse(report["ok"])
        self.assertTrue(report["crashed"])
        self.assertIn("while loading sql", report["error"])


# --------------------------------------------------------------------------
# end to end, offline
# --------------------------------------------------------------------------


@unittest.skipUnless(PYTHON, "no CPython with a bundled target available")
class OfflineBootstrapTest(unittest.TestCase):
    """One real offline install shared by the tests that inspect it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.home = os.path.join(cls._tmp.name, "home")
        cls.rc, cls.report, cls.stderr = run_bootstrap(
            "--home", cls.home, "--offline", "--python", PYTHON
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_common_profile_installs_with_no_index(self):
        self.assertEqual(self.rc, 0, self.report.get("error") or self.stderr)
        self.assertEqual(self.report["method"], "bundle")
        self.assertEqual(
            sorted(self.report["probe"]["languages"]),
            sorted(MANIFEST["profiles"]["common"]),
        )
        self.assertTrue(
            all(v["ok"] for v in self.report["probe"]["languages"].values())
        )

    def test_environment_lives_under_the_home_not_the_skill(self):
        self.assertTrue(self.report["environment"].startswith(self.home))
        with open(os.path.join(self.home, "current.json")) as handle:
            self.assertEqual(json.load(handle)["python"], self.report["python"])

    def test_queries_run_in_the_installed_environment(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "a.fish"), "w") as handle:
                handle.write("function greet\n    echo hi\nend\ngreet\n")
            result = subprocess.run(
                [
                    self.report["python"],
                    os.path.join(SCRIPTS, "ts_query.py"),
                    "refs",
                    "greet",
                    repo,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertIn("a.fish:4", result.stdout)

    def test_a_healthy_environment_is_reused(self):
        rc, report, _ = run_bootstrap(
            "--home", self.home, "--offline", "--python", PYTHON
        )
        self.assertEqual(rc, 0)
        self.assertEqual(report["action"], "reused")
        self.assertEqual(report["python"], self.report["python"])

    def test_plain_python_switches_into_the_environment(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env["CODEBASE_INDEX_HOME"] = self.home
        bare = tempfile.mkdtemp()
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", "--without-pip", bare], check=True
            )
            result = subprocess.run(
                [
                    os.path.join(bare, "bin", "python"),
                    os.path.join(SCRIPTS, "ts_query.py"),
                    "langs",
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        finally:
            shutil.rmtree(bare)
        self.assertIn("fish", result.stdout)
        self.assertNotIn("not installed in this Python", result.stdout + result.stderr)

    def test_failed_rebuild_keeps_the_working_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            house = os.path.join(tmp, "wheels")
            shutil.copytree(WHEELS, house)
            victim = next(
                f for f in os.listdir(house) if f.startswith("tree_sitter_go")
            )
            with open(os.path.join(house, victim), "ab") as handle:
                handle.write(b"x")
            rc, report, _ = run_bootstrap(
                "--home",
                self.home,
                "--offline",
                "--force",
                "--wheelhouse",
                house,
                "--python",
                PYTHON,
            )
        self.assertEqual(rc, 1)
        self.assertIn("SHA-256", report["error"])
        with open(os.path.join(self.home, "current.json")) as handle:
            self.assertEqual(json.load(handle)["python"], self.report["python"])
        self.assertTrue(os.path.exists(self.report["python"]))
        self.assertEqual(len(os.listdir(os.path.join(self.home, "envs"))), 1)

    def test_concurrent_bootstrap_is_refused_not_interleaved(self):
        with bootstrap.HomeLock(self.home, wait=1):
            rc, report, _ = run_bootstrap(
                "--home",
                self.home,
                "--offline",
                "--force",
                "--lock-wait",
                "1",
                "--python",
                PYTHON,
            )
        self.assertEqual(rc, 1)
        self.assertEqual(report["kind"], "busy")


@unittest.skipUnless(PYTHON, "no CPython with a bundled target available")
class OfflineRefusalTest(unittest.TestCase):
    def test_offline_request_outside_the_bundle_fails_before_creating_anything(self):
        with tempfile.TemporaryDirectory() as home:
            rc, report, _ = run_bootstrap(
                "--home",
                home,
                "--offline",
                "--languages",
                "go,kotlin",
                "--python",
                PYTHON,
            )
            self.assertEqual(rc, 1)
            self.assertEqual(report["missing"], ["kotlin"])
            self.assertFalse(os.path.exists(os.path.join(home, "envs")))

    def test_unknown_language_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as home:
            rc, report, _ = run_bootstrap("--home", home, "--languages", "cobol")
        self.assertEqual(rc, 1)
        self.assertIn("cobol", report["error"])

    def test_aliases_are_accepted(self):
        with tempfile.TemporaryDirectory() as home:
            rc, report, _ = run_bootstrap(
                "--home",
                home,
                "--check",
                "--offline",
                "--languages",
                "c#,ts,pwsh",
                "--python",
                PYTHON,
            )
        self.assertEqual(rc, 0, report)
        self.assertEqual(report["languages"], ["c_sharp", "powershell", "typescript"])


if __name__ == "__main__":
    unittest.main()
