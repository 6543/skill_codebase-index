# codebase-index with a Python environment holding the pinned tree-sitter
# runtime and grammars.
#
# The grammars are the exact wheels in vendor/wheels (the offline bundle the pip
# route installs), checked against the SHA-256 in vendor/wheels.json, installed
# into nixpkgs' CPython 3.13 and patched with autoPatchelfHook. Both routes thus
# run one tested set of binaries, and the build needs nothing from the network
# beyond nixpkgs itself. Upstream sdists are not used: many omit parser.h or
# scanner.c and build modules that fail on import.
{
  lib,
  python313,
  autoPatchelfHook,
  runCommand,
  makeWrapper,
  # Skill sources: scripts/ and vendor/ (wheels.json, wheels/).
  src,
}:
let
  python = python313;
  manifest = lib.importJSON "${src}/vendor/wheels.json";
  target = manifest.targets."cp313-manylinux_x86_64";
  languages = manifest.profiles.${manifest.bundled_profile};

  module =
    distribution:
    if distribution == manifest.runtime.distribution then
      manifest.runtime.module
    else
      (lib.findFirst (l: l.distribution == distribution) null (lib.attrValues manifest.languages)).module;

  fromWheel =
    file:
    let
      wheel = manifest.wheels.${file};
    in
    python.pkgs.buildPythonPackage {
      pname = wheel.distribution;
      inherit (wheel) version;
      format = "wheel";
      src = "${src}/vendor/wheels/${file}";
      nativeBuildInputs = [ autoPatchelfHook ];
      # The flake source is content-addressed already; this pins each file to
      # the manifest too, so a wheel swapped in the tree fails loudly here.
      preInstall = ''
        echo "${wheel.sha256}  $src" | sha256sum --check --quiet
      '';
      doCheck = false;
      pythonImportsCheck = [ (module wheel.distribution) ];
    };

  pythonEnv = python.withPackages (_: map fromWheel target.files);
in
runCommand "codebase-index"
  {
    nativeBuildInputs = [ makeWrapper ];
    passthru = {
      inherit pythonEnv languages;
    };
    meta = {
      description = "Structural code search and call-graph tracing with tree-sitter";
      mainProgram = "codebase-index";
      # The bundled wheels are manylinux x86_64; other systems use bootstrap.py.
      platforms = [ "x86_64-linux" ];
    };
  }
  ''
    mkdir -p $out/bin $out/share/codebase-index
    cp -r ${src}/scripts $out/share/codebase-index/
    makeWrapper ${pythonEnv}/bin/python $out/bin/codebase-index \
      --add-flags $out/share/codebase-index/scripts/ts_query.py
    makeWrapper ${pythonEnv}/bin/python $out/bin/codebase-index-probe \
      --add-flags $out/share/codebase-index/scripts/runtime_probe.py \
      --add-flags --languages=${lib.concatStringsSep "," languages}
  ''
