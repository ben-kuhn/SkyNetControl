{ pkgs ? import <nixpkgs> {} }:

let
  skynetcontrol = import ./default.nix { inherit pkgs; };

  # Container entrypoint: idempotently apply pending Alembic migrations,
  # then exec the uvicorn server. `alembic upgrade head` is a no-op when
  # the DB is already at the latest revision, so this is safe to run on
  # every container start. NixOS deployments don't use this — module.nix
  # runs migrations via systemd ExecStartPre instead.
  entrypoint = pkgs.writeShellScriptBin "skynetcontrol-entrypoint" ''
    set -e
    echo "[skynetcontrol] Applying database migrations..."
    ${skynetcontrol}/bin/skynetcontrol-alembic upgrade head
    echo "[skynetcontrol] Starting server..."
    exec ${skynetcontrol}/bin/skynetcontrol-server \
      backend.app:create_app \
      --factory \
      --host 0.0.0.0 \
      --port 8000 \
      "$@"
  '';
in
pkgs.dockerTools.buildLayeredImage {
  name = "skynetcontrol";
  tag = "latest";

  contents = [
    skynetcontrol
    entrypoint
    pkgs.coreutils
    pkgs.bashInteractive
  ];

  config = {
    Cmd = [ "${entrypoint}/bin/skynetcontrol-entrypoint" ];
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
