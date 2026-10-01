{
  description = "nix-container";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        sharedPkgs = with pkgs; [
          git
          python313
          python313Packages.pyyaml
          python313Packages.pytest
          python313Packages.pydantic
          python313Packages.cairosvg
          python313Packages.uharfbuzz
          python313Packages.fonttools
          python313Packages.urllib3
        ];

      in {
        devShell = pkgs.mkShell {
          buildInputs = sharedPkgs;
          shellHook = ''
            echo "Welcome to nix-container devShell"
          '';
        };
      }
    );
}
