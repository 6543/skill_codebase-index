{
  description = "codebase-index: structural code search and call-graph tracing with tree-sitter";

  # git+https rather than github: works where only git access is allowed, and
  # shallow keeps the fetch small.
  inputs.nixpkgs.url = "git+https://github.com/NixOS/nixpkgs?ref=nixos-unstable&shallow=1";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      # The bundled grammar wheels are manylinux x86_64. Elsewhere, run
      # scripts/bootstrap.py, which installs the same pins from PyPI.
      systems = [ "x86_64-linux" ];
      forAll = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # Only what the package needs, so editing docs does not rebuild it.
      runtimeSrc = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions [
          ./scripts
          ./vendor/wheels.json
          ./vendor/wheels
        ];
      };
      package = pkgs: pkgs.callPackage ./nix/package.nix { src = runtimeSrc; };
    in
    {
      packages = forAll (pkgs: {
        default = package pkgs;
        codebase-index = package pkgs;
      });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = lib.getExe (package pkgs);
        };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (package pkgs).pythonEnv
            pkgs.ruff
            pkgs.nixfmt
          ];
        };
      });

      checks = forAll (
        pkgs:
        let
          pkg = package pkgs;
        in
        {
          # Every bundled grammar imports, has a compatible ABI, parses its fixture.
          probe = pkgs.runCommand "codebase-index-probe" { } ''
            ${pkg}/bin/codebase-index-probe > $out
          '';
          # The behavioural suite, minus the tests that build pip environments.
          tests =
            pkgs.runCommand "codebase-index-tests"
              {
                nativeBuildInputs = [ pkg.pythonEnv ];
                CODEBASE_INDEX_SKIP_VENV_TESTS = "1";
              }
              ''
                cp -r ${self} skill && chmod -R u+w skill && cd skill
                HOME=$TMPDIR python -m unittest discover -s scripts/tests
                touch $out
              '';
          format = pkgs.runCommand "codebase-index-nixfmt" { } ''
            ${lib.getExe pkgs.nixfmt} --check ${./flake.nix} ${./nix/package.nix}
            touch $out
          '';
        }
      );

      formatter = forAll (pkgs: pkgs.nixfmt);
    };
}
