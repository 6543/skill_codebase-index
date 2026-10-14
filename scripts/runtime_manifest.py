"""Wheel manifest handling shared by bootstrap.py and build_wheelhouse.py.

Standard library only: this runs before anything is installed, so it cannot
depend on `packaging` or on tree-sitter.
"""

import hashlib
import json
import os
import re

# PEP 427 file name. Anything else in the wheel directory is refused, which
# also rules out path separators and `..` reaching pip.
_WHEEL = re.compile(
    r"^(?P<name>[A-Za-z0-9_.]+)-(?P<version>[A-Za-z0-9_.+!]+)"
    r"(?:-(?P<build>\d[^-]*))?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$"
)


class ManifestError(Exception):
    """The bundle is unusable; the message names what is wrong."""


def normalise(dist):
    return re.sub(r"[-_.]+", "-", dist).lower()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def wheel_tags(filename):
    """-> {"python": [...], "abi": [...], "platform": [...]} from a wheel name."""
    match = _WHEEL.match(filename)
    if not match:
        raise ManifestError(f"not a valid wheel file name: {filename!r}")
    return {
        "python": match.group("py").split("."),
        "abi": match.group("abi").split("."),
        "platform": match.group("plat").split("."),
    }


def load_inventory(skill_dir):
    with open(os.path.join(skill_dir, "vendor", "inventory.json")) as handle:
        return json.load(handle)


def inventory_digest(skill_dir):
    return sha256_file(os.path.join(skill_dir, "vendor", "inventory.json"))


def load_manifest(skill_dir):
    path = os.path.join(skill_dir, "vendor", "wheels.json")
    try:
        with open(path) as handle:
            manifest = json.load(handle)
    except FileNotFoundError:
        raise ManifestError(f"no wheel manifest at {path}") from None
    except json.JSONDecodeError as error:
        raise ManifestError(f"wheel manifest is not valid JSON: {error}") from None
    if manifest.get("inventory_sha256") != inventory_digest(skill_dir):
        raise ManifestError(
            "vendor/wheels.json was built from a different vendor/inventory.json; "
            "rebuild it with scripts/build_wheelhouse.py"
        )
    return manifest


def glibc_ok(available, required):
    if not available:
        return False
    try:
        have = tuple(int(p) for p in available.split(".")[:2])
        need = tuple(int(p) for p in required.split(".")[:2])
    except ValueError:
        return False
    return have >= need


def select_target(manifest, interpreter):
    """Bundled target matching an interpreter description, or None.

    `interpreter` is what runtime_probe.describe_interpreter() reports. pip
    checks the wheel tags again at install time; this only decides whether a
    bundle exists for the interpreter at all.
    """
    for name, target in manifest["targets"].items():
        if (
            interpreter.get("implementation") == "cpython"
            and interpreter.get("version", "").startswith(target["python"] + ".")
            and interpreter.get("machine") == target["machine"]
            and interpreter.get("libc") == "glibc"
            and glibc_ok(interpreter.get("libc_version"), target["min_glibc"])
        ):
            return name
    return None


def wheels_for(manifest, target, languages):
    """-> [filename] for the runtime plus every grammar the languages need.

    Raises naming the languages a target's bundle cannot serve.
    """
    files = manifest["targets"][target]["files"]
    wheels = manifest["wheels"]
    runtime = normalise(manifest["runtime"]["distribution"])
    chosen = [f for f in files if normalise(wheels[f]["distribution"]) == runtime]
    missing = []
    for key in languages:
        dist = manifest["languages"].get(key, {}).get("distribution")
        hit = [
            f
            for f in files
            if dist and normalise(wheels[f]["distribution"]) == normalise(dist)
        ]
        if not hit:
            missing.append(key)
        chosen.extend(f for f in hit if f not in chosen)
    if missing:
        raise ManifestError(
            f"bundle {target} has no wheel for: {', '.join(sorted(missing))}"
        )
    return chosen


def verify_wheels(wheel_dir, manifest, files):
    """Check presence, names and SHA-256 of the given wheels before pip sees them."""
    problems = []
    for name in files:
        try:
            wheel_tags(name)
        except ManifestError as error:
            problems.append(str(error))
            continue
        entry = manifest["wheels"].get(name)
        path = os.path.join(wheel_dir, name)
        if entry is None:
            problems.append(f"{name}: not listed in the manifest")
        elif not os.path.isfile(path):
            problems.append(f"{name}: missing from {wheel_dir}")
        elif sha256_file(path) != entry["sha256"]:
            problems.append(f"{name}: SHA-256 mismatch (corrupt or replaced)")
    if problems:
        raise ManifestError("; ".join(problems))


def lock_lines(manifest, files):
    """Requirement lines with exact pins and hashes, for pip --require-hashes."""
    return [
        f"{manifest['wheels'][f]['distribution']}=={manifest['wheels'][f]['version']} "
        f"--hash=sha256:{manifest['wheels'][f]['sha256']}"
        for f in files
    ]
