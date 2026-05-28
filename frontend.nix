{ pkgs ? import <nixpkgs> {} }:

pkgs.buildNpmPackage {
  pname = "skynetcontrol-frontend";
  version = "0.1.0";
  src = ./frontend;

  # Hash of the node_modules tree derived from package-lock.json. After
  # bumping frontend dependencies, set this to the all-zero placeholder
  # and re-run `nix-build frontend.nix`; Nix will print the correct value.
  npmDepsHash = "sha256-MHcknAgfxdwVwU13FEuMPatlwNq++hnckBcHkJh4W/8=";

  # nodejs_22 matches the dev shell.
  nodejs = pkgs.nodejs_22;

  # `npm run build` produces frontend/dist; copy that to $out so the
  # backend can serve it as SKYNET_STATIC_DIR.
  installPhase = ''
    runHook preInstall
    mkdir -p $out
    cp -r dist/* $out/
    runHook postInstall
  '';
}
