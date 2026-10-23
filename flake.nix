{
  description = "nix-container";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
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
          gh
          vim
          python313
          uv
          stdenv.cc.cc.lib
          nodejs_26
          # Remotion renders with headless Chromium; on NixOS the browser it
          # downloads itself cannot run (missing nspr/nss), so provide the
          # patched nixpkgs chromium instead.
          chromium
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
