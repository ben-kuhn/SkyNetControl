{ pkgs ? import <nixpkgs> {} }:

let
  skynetcontrol = import ./default.nix { inherit pkgs; };
in
pkgs.dockerTools.buildLayeredImage {
  name = "skynetcontrol";
  tag = "latest";

  contents = [
    skynetcontrol
    pkgs.coreutils
    pkgs.bashInteractive
  ];

  config = {
    Cmd = [
      "${skynetcontrol}/bin/skynetcontrol-server"
      "backend.app:create_app"
      "--factory"
      "--host" "0.0.0.0"
      "--port" "8000"
    ];
    Env = [
      "SKYNET_DATABASE_URL=sqlite:////data/skynetcontrol.db"
      "SKYNET_STATIC_DIR=${skynetcontrol}/share/skynetcontrol/static"
    ];
    ExposedPorts = {
      "8000/tcp" = {};
    };
    Volumes = {
      "/data" = {};
    };
    WorkingDir = "/";
  };
}
