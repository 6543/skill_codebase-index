#!/usr/bin/env python3
"""Set up an isolated Python environment for codebase-index. Stdlib only.

    python3 scripts/bootstrap.py                     common profile, bundle first
    python3 scripts/bootstrap.py --offline           never touch a package index
    python3 scripts/bootstrap.py --languages go,nix  just these (+ the runtime)
    python3 scripts/bootstrap.py --check             verify the bundle, install nothing
    python3 scripts/bootstrap.py --print-python      ensure, then print only the python path

The environment lives under a home directory (--home, $CODEBASE_INDEX_HOME, or
the user cache dir), never inside the skill or the repository being read. Each
install is a new generation beside the previous one; `current.json` switches
to it only after every requested grammar has been imported and has parsed a
real snippet, so a failed or interrupted run leaves the working one in place.

For a bundled target (CPython 3.12/3.13, Linux x86_64 glibc) every wheel comes
from vendor/wheels, hash-checked, with no network access at all. Languages or
platforms the bundle does not cover are installed from PyPI at the pinned
versions, unless --offline, in which case they are reported as missing.

stdout is one JSON report. Progress goes to stderr. Exit 0 only when every
requested language is operational.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from languages import canonical_key  # noqa: E402
from runtime_manifest import (  # noqa: E402
    ManifestError,
    load_manifest,
    lock_lines,
    select_target,
    sha256_file,
    verify_wheels,
    wheels_for,
)

PROBE = os.path.join(HERE, "runtime_probe.py")
LOCK_WAIT_SECONDS = 300
INDEX_TIMEOUT_SECONDS = 20


class BootstrapError(Exception):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def log(message):
    print(f"[bootstrap] {message}", file=sys.stderr, flush=True)


def default_home():
    if os.environ.get("CODEBASE_INDEX_HOME"):
        return os.environ["CODEBASE_INDEX_HOME"]
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "codebase-index")


def venv_python(env_dir):
    if os.name == "nt":
        return os.path.join(env_dir, "Scripts", "python.exe")
    return os.path.join(env_dir, "bin", "python")


def clean_env():
    """Environment for pip: no user config, no inherited index or find-links."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "PYTHON"))}
    env.update(
        PIP_CONFIG_FILE=os.devnull,
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_NO_INPUT="1",
        PYTHONNOUSERSITE="1",
    )
    return env


# --------------------------------------------------------------------------
# locking: one bootstrap per home at a time
# --------------------------------------------------------------------------


class HomeLock:
    def __init__(self, home, wait):
        self.path = os.path.join(home, ".lock")
        self.wait = wait
        self.handle = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.handle = open(self.path, "a+")
        deadline = time.monotonic() + self.wait
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    raise BootstrapError(
                        f"another bootstrap is running for {os.path.dirname(self.path)}; "
                        f"waited {self.wait}s",
                        kind="busy",
                    ) from None
                time.sleep(0.5)

    def __exit__(self, *_):
        self.handle.close()  # closing releases flock / msvcrt locks


# --------------------------------------------------------------------------
# probing, always in a subprocess
# --------------------------------------------------------------------------


def describe(python):
    result = subprocess.run(
        [python, "-I", PROBE, "--describe"],
        capture_output=True,
        text=True,
        timeout=60,
        env=clean_env(),
        check=False,
    )
    if result.returncode != 0:
        raise BootstrapError(
            f"cannot run {python}: {result.stderr.strip()[-300:]}", kind="python"
        )
    return json.loads(result.stdout)


def probe(python, languages):
    """-> probe report. A crashed interpreter is a failed probe, not a crash here."""
    try:
        result = subprocess.run(
            [python, "-I", PROBE, f"--languages={','.join(languages)}"],
            capture_output=True,
            text=True,
            timeout=300,
            env=clean_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "probe timed out after 300s"}
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    try:
        report = json.loads(lines[-1])
        if "start" in report:
            raise ValueError
        return report
    except (IndexError, ValueError):
        started = [json.loads(ln)["start"] for ln in lines if ln.startswith('{"start"')]
        culprit = started[-1] if started else "the runtime import"
        return {
            "ok": False,
            "crashed": True,
            "error": f"probe process died (exit {result.returncode}) while loading {culprit}",
            "stderr": result.stderr.strip()[-500:],
        }


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


def read_state(home):
    try:
        with open(os.path.join(home, "current.json")) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None
    return state if os.path.exists(state.get("python", "")) else None


def write_state(home, state):
    tmp = os.path.join(home, f".current.{os.getpid()}.json")
    with open(tmp, "w") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp, os.path.join(home, "current.json"))
    if os.name != "nt":
        link, tmp_link = (
            os.path.join(home, "current"),
            os.path.join(home, f".current.{os.getpid()}"),
        )
        try:
            os.symlink(os.path.relpath(state["environment"], home), tmp_link)
            os.replace(tmp_link, link)
        except OSError:
            pass  # convenience only; current.json is authoritative


def prune(home, keep):
    """Remove generations other than the ones in `keep` (current and previous)."""
    envs = os.path.join(home, "envs")
    for name in os.listdir(envs) if os.path.isdir(envs) else ():
        path = os.path.join(envs, name)
        if os.path.realpath(path) not in {os.path.realpath(k) for k in keep if k}:
            shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------
# installation
# --------------------------------------------------------------------------


def pip(python, args, what):
    log(f"pip: {what}")
    result = subprocess.run(
        [python, "-m", "pip", "install", "--no-deps", "--only-binary=:all:", *args],
        capture_output=True,
        text=True,
        env=clean_env(),
        check=False,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-6:]
        raise BootstrapError(f"pip failed ({what}): " + " | ".join(tail), kind="pip")


def install_from_bundle(python, manifest, target, languages, wheelhouse):
    files = wheels_for(manifest, target, languages)
    verify_wheels(wheelhouse, manifest, files)  # before pip ever reads them
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lock:
        lock.write("\n".join(lock_lines(manifest, files)) + "\n")
    try:
        pip(
            python,
            [
                "--no-index",
                "--find-links",
                wheelhouse,
                "--require-hashes",
                "-r",
                lock.name,
            ],
            f"{len(files)} bundled wheels for {target}",
        )
    finally:
        os.unlink(lock.name)


def install_from_index(python, manifest, languages, with_runtime):
    pins, local = [], []
    if with_runtime:
        runtime = manifest["runtime"]
        pins.append(f"{runtime['distribution']}=={runtime['version']}")
    for key in languages:
        entry = manifest["languages"][key]
        local_dir = _local_source(entry["distribution"])
        if local_dir:
            local.append(local_dir)
        else:
            pins.append(f"{entry['distribution']}=={entry['version']}")
    pins = sorted(set(pins))
    if pins:
        pip(
            python,
            ["--timeout", str(INDEX_TIMEOUT_SECONDS), "--retries", "1", *pins],
            f"{len(pins)} pinned wheels from the package index",
        )
    for path in sorted(set(local)):
        # A local wrapper has no index release: build it from its sources.
        log(f"pip: building {os.path.basename(path)} from source (needs a C compiler)")
        result = subprocess.run(
            [python, "-m", "pip", "install", "--no-deps", path],
            capture_output=True,
            text=True,
            env=clean_env(),
            check=False,
        )
        if result.returncode != 0:
            raise BootstrapError(
                f"building {path} failed: " + result.stderr.strip()[-400:], kind="build"
            )


def _local_source(distribution):
    from runtime_manifest import load_inventory

    entry = load_inventory(SKILL)["grammars"].get(distribution, {})
    return os.path.join(SKILL, entry["local"]) if entry.get("local") else None


def create_generation(home, base_python):
    envs = os.path.join(home, "envs")
    os.makedirs(envs, exist_ok=True)
    env_dir = os.path.join(envs, time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
    log(f"creating environment {env_dir}")
    result = subprocess.run(
        [base_python, "-m", "venv", env_dir],
        capture_output=True,
        text=True,
        env=clean_env(),
        check=False,
    )
    if result.returncode != 0 or not os.path.exists(venv_python(env_dir)):
        shutil.rmtree(env_dir, ignore_errors=True)
        raise BootstrapError(
            f"{base_python} cannot create a virtual environment with pip "
            f"({result.stderr.strip()[-200:]}). Install the interpreter's venv/ensurepip "
            "support (Debian/Ubuntu: python3-venv), pass another --python, or on NixOS "
            "use the flake in this skill (`nix run`).",
            kind="venv",
        )
    return env_dir


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def resolve_languages(args, manifest):
    if args.languages:
        wanted, unknown = [], []
        for name in args.languages.split(","):
            if not name.strip():
                continue
            key = canonical_key(name)
            (wanted if key in manifest["languages"] else unknown).append(key or name)
        if unknown:
            raise BootstrapError(
                f"unknown language(s): {', '.join(unknown)}", kind="usage"
            )
        return sorted(set(wanted)), "custom"
    profile = args.profile
    if profile not in manifest["profiles"]:
        raise BootstrapError(f"unknown profile {profile!r}", kind="usage")
    return sorted(manifest["profiles"][profile]), profile


def run(args):
    manifest = load_manifest(SKILL)
    languages, profile = resolve_languages(args, manifest)
    base = args.python or sys.executable
    interpreter = describe(base)
    target = select_target(manifest, interpreter)
    report = {
        "ok": False,
        "home": args.home,
        "profile": profile,
        "languages": languages,
        "runtime": manifest["runtime"]["version"],
        "interpreter": interpreter,
        "target": target,
        "manifest_sha256": sha256_file(os.path.join(SKILL, "vendor", "wheels.json")),
    }
    bundled = [k for k in languages if target and manifest["languages"][k]["bundled"]]
    unbundled = [k for k in languages if k not in bundled]

    if args.check:
        report["action"] = "check"
        if target:
            verify_wheels(
                args.wheelhouse, manifest, wheels_for(manifest, target, bundled)
            )
        report["bundled"], report["needs_index"] = bundled, unbundled
        state = read_state(args.home)
        if state:
            report["current"] = probe(state["python"], languages)
        report["ok"] = not (args.offline and unbundled)
        if not report["ok"]:
            report["error"] = f"not available offline here: {', '.join(unbundled)}"
        return report

    if args.offline and unbundled:
        reason = (
            f"no bundle for this interpreter ({interpreter['implementation']} "
            f"{interpreter['version']}, {interpreter['machine']}, {interpreter['libc'] or 'unknown libc'})"
            if not target
            else "these grammars are not in the bundled profile"
        )
        raise BootstrapError(
            f"--offline cannot provide: {', '.join(unbundled)} ({reason})",
            kind="offline",
            missing=unbundled,
        )

    os.makedirs(args.home, exist_ok=True)
    with HomeLock(args.home, args.lock_wait):
        state = read_state(args.home)
        if state and not args.force:
            current = probe(state["python"], languages)
            if (
                current.get("ok")
                and current.get("runtime") == manifest["runtime"]["version"]
            ):
                report.update(
                    ok=True,
                    action="reused",
                    python=state["python"],
                    environment=state["environment"],
                    probe=current,
                )
                return report
            # Keep what the current environment already serves.
            languages = sorted(set(languages) | set(state.get("languages", [])))
            bundled = [
                k for k in languages if target and manifest["languages"][k]["bundled"]
            ]
            unbundled = [k for k in languages if k not in bundled]
            report["languages"] = languages

        env_dir = create_generation(args.home, base)
        python = venv_python(env_dir)
        try:
            methods = []
            if target:
                install_from_bundle(python, manifest, target, bundled, args.wheelhouse)
                methods.append("bundle")
            if unbundled or not target:
                install_from_index(python, manifest, unbundled, with_runtime=not target)
                methods.append("index")
            result = probe(python, languages)
            if not result.get("ok"):
                failed = [
                    k for k, v in result.get("languages", {}).items() if not v.get("ok")
                ]
                raise BootstrapError(
                    "new environment failed its probe: "
                    + (
                        result.get("error")
                        or ", ".join(
                            f"{k}: {result['languages'][k]['error']}" for k in failed
                        )
                    ),
                    kind="probe",
                    probe=result,
                )
        except BaseException:
            shutil.rmtree(env_dir, ignore_errors=True)
            raise
        new_state = {
            "python": python,
            "environment": env_dir,
            "languages": languages,
            "runtime": result.get("runtime"),
            "manifest_sha256": report["manifest_sha256"],
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        write_state(args.home, new_state)
        prune(args.home, keep=[env_dir, state and state.get("environment")])
        report.update(
            ok=True,
            action="installed",
            method="+".join(methods),
            python=python,
            environment=env_dir,
            probe=result,
        )
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Set up an isolated environment for codebase-index.",
        epilog="See references/bootstrap.md.",
    )
    parser.add_argument(
        "--home", default=default_home(), help="where environments live"
    )
    parser.add_argument("--profile", default="common", help="common (default) or all")
    parser.add_argument(
        "--languages", help="comma list; overrides --profile (aliases ok)"
    )
    parser.add_argument(
        "--offline", action="store_true", help="never contact a package index"
    )
    parser.add_argument("--python", help="interpreter to build the environment from")
    parser.add_argument("--wheelhouse", default=os.path.join(SKILL, "vendor", "wheels"))
    parser.add_argument(
        "--check", action="store_true", help="verify only, install nothing"
    )
    parser.add_argument(
        "--force", action="store_true", help="build a new environment even if one works"
    )
    parser.add_argument(
        "--print-python", action="store_true", help="print only the python path"
    )
    parser.add_argument(
        "--lock-wait", type=int, default=LOCK_WAIT_SECONDS, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    args.home = os.path.abspath(os.path.expanduser(args.home))

    try:
        report = run(args)
    except (BootstrapError, ManifestError) as error:
        report = {"ok": False, "error": str(error), **getattr(error, "details", {})}
    if args.print_python and report.get("ok") and report.get("python"):
        print(report["python"])
    else:
        print(json.dumps(report, indent=2))
    if report.get("ok"):
        log(f"ready: {report.get('python', '(check only)')}")
    else:
        log(f"FAILED: {report.get('error')}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
