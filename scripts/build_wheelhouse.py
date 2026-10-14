#!/usr/bin/env python3
"""Maintainer tool: build the offline wheel bundle from vendor/inventory.json.

Not used at runtime. It needs network access to PyPI, plus `build`,
`auditwheel` and `patchelf` for the locally packaged Fish grammar:

    python3 -m venv /tmp/wh && /tmp/wh/bin/pip install build auditwheel patchelf
    /tmp/wh/bin/python scripts/build_wheelhouse.py

Outputs, all generated (do not edit by hand):
    vendor/wheels/*.whl              flat: abi3 wheels serve every target once
    vendor/wheels.json               per-target file lists, hashes, languages
    vendor/licenses/<dist>/...       license files shipped inside each wheel
    requirements/locks/<target>.txt  exact pins with --hash, one per target

Every bundled target must cover the whole bundled profile, or the build fails:
a bundle that silently lacks one grammar is how an offline install half-works.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from languages import BY_KEY  # noqa: E402
from runtime_manifest import (  # noqa: E402
    inventory_digest,
    load_inventory,
    normalise,
    sha256_file,
    wheel_tags,
)


def run(cmd, **kwargs):
    print("+", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True, **kwargs)


def download(dist, version, target, dest):
    """Exactly one binary wheel of dist==version for a target, from PyPI."""
    with tempfile.TemporaryDirectory() as tmp:
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--no-deps",
                "--only-binary=:all:",
                "--isolated",
                "--disable-pip-version-check",
                "--python-version",
                target["python"],
                "--implementation",
                "cp",
                "--abi",
                target["abi"],
                "--abi",
                "abi3",
                "--platform",
                target["platform"],
                "-d",
                tmp,
                f"{dist}=={version}",
            ]
        )
        (name,) = os.listdir(tmp)
        if not os.path.exists(os.path.join(dest, name)):
            shutil.move(os.path.join(tmp, name), os.path.join(dest, name))
        return name


def build_local(dist, entry, dest):
    """Build, policy-check and tag a locally packaged grammar (Fish)."""
    source = os.path.join(SKILL, entry["local"])
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "src")
        shutil.copytree(source, work)  # never write build output into the skill
        run([sys.executable, "-m", "build", "--wheel", "--outdir", tmp + "/raw", work])
        (raw,) = os.listdir(tmp + "/raw")
        run(
            [
                sys.executable,
                "-m",
                "auditwheel",
                "repair",
                "--plat",
                "manylinux_2_17_x86_64",
                "-w",
                tmp + "/fixed",
                os.path.join(tmp, "raw", raw),
            ]
        )
        (name,) = os.listdir(tmp + "/fixed")
        target = os.path.join(dest, name)
        if os.path.exists(target):
            os.remove(target)
        shutil.move(os.path.join(tmp, "fixed", name), target)
        return name


_LICENSE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)[^/]*$", re.IGNORECASE)


def copy_licenses(wheel_path, dist, dest_root):
    out = os.path.join(dest_root, normalise(dist))
    with zipfile.ZipFile(wheel_path) as archive:
        members = [
            m for m in archive.namelist() if ".dist-info/" in m and _LICENSE.search(m)
        ]
        if not members:
            return []
        os.makedirs(out, exist_ok=True)
        for member in members:
            with open(os.path.join(out, os.path.basename(member)), "wb") as handle:
                handle.write(archive.read(member))
    return sorted(os.path.basename(m) for m in members)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--skip-local", action="store_true", help="reuse the existing local wheels"
    )
    args = parser.parse_args()

    inventory = load_inventory(SKILL)
    profile = inventory["profiles"][inventory["bundled_profile"]]
    wheel_dir = os.path.join(SKILL, "vendor", "wheels")
    license_dir = os.path.join(SKILL, "vendor", "licenses")
    os.makedirs(wheel_dir, exist_ok=True)

    needed = {
        inventory["runtime"]["distribution"]: {
            "version": inventory["runtime"]["version"]
        }
    }
    for dist, entry in inventory["grammars"].items():
        if set(entry["languages"]) & set(profile):
            needed[dist] = entry

    wheels, targets = {}, {}
    local_built = {}
    for target_name, target in inventory["targets"].items():
        files = []
        for dist, entry in sorted(needed.items()):
            if entry.get("local"):
                if dist not in local_built:
                    existing = [
                        f
                        for f in os.listdir(wheel_dir)
                        if f.startswith(normalise(dist).replace("-", "_") + "-")
                    ]
                    local_built[dist] = (
                        existing[0]
                        if args.skip_local and existing
                        else build_local(dist, entry, wheel_dir)
                    )
                name = local_built[dist]
            else:
                name = download(dist, entry["version"], target, wheel_dir)
            files.append(name)
            path = os.path.join(wheel_dir, name)
            wheels[name] = {
                "distribution": dist,
                "version": entry["version"],
                "sha256": sha256_file(path),
                "size": os.path.getsize(path),
                "tags": wheel_tags(name),
                "languages": sorted(entry.get("languages", [])),
                "licenses": copy_licenses(path, dist, license_dir),
            }
        covered = {lang for f in files for lang in wheels[f]["languages"]}
        missing = sorted(set(profile) - covered)
        if missing:
            sys.exit(f"{target_name}: bundle does not cover {missing}")
        targets[target_name] = dict(target, files=sorted(files))

    stale = set(os.listdir(wheel_dir)) - set(wheels)
    for name in stale:
        os.remove(os.path.join(wheel_dir, name))

    languages = {}
    for dist, entry in inventory["grammars"].items():
        for key in entry["languages"]:
            spec = BY_KEY[key]
            languages[key] = {
                "distribution": dist,
                "version": entry["version"],
                "module": spec.module,
                "entry": spec.entry,
                "bundled": key in profile,
            }

    manifest = {
        "_comment": "Generated by scripts/build_wheelhouse.py from vendor/inventory.json.",
        "inventory_sha256": inventory_digest(SKILL),
        "runtime": inventory["runtime"],
        "bundled_profile": inventory["bundled_profile"],
        "profiles": inventory["profiles"],
        "languages": languages,
        "targets": targets,
        "wheels": dict(sorted(wheels.items())),
    }
    with open(os.path.join(SKILL, "vendor", "wheels.json"), "w") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")

    lock_dir = os.path.join(SKILL, "requirements", "locks")
    os.makedirs(lock_dir, exist_ok=True)
    for target_name, target in targets.items():
        lines = [
            f"# Generated by scripts/build_wheelhouse.py for {target_name}. Do not edit."
        ]
        for name in target["files"]:
            w = wheels[name]
            lines.append(
                f"{w['distribution']}=={w['version']} --hash=sha256:{w['sha256']}"
            )
        with open(os.path.join(lock_dir, f"{target_name}.txt"), "w") as handle:
            handle.write("\n".join(lines) + "\n")

    total = sum(w["size"] for w in wheels.values())
    print(
        f"{len(wheels)} wheels, {total / 1e6:.1f} MB, targets: {', '.join(targets)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
