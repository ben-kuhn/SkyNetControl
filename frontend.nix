{ pkgs ? import <nixpkgs> {} }:

pkgs.buildNpmPackage {
  pname = "skynetcontrol-frontend";
  version = "0.1.0";
  src = ./frontend;

  npmDepsHash = "sha256-opJQTO0+RhUwduGm99+nIFfF+yFRLdQ5n/goS4KtKLw=";

  buildPhase = ''
    npm run build
  '';

  installPhase = ''
    cp -r dist $out
  '';
}
