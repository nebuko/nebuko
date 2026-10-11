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
          uv
          stdenv.cc.cc.lib
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
